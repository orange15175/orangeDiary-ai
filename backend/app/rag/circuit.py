"""熔断器：下游连续失败后快速失败，冷却期内不再空等超时。

为什么要它：Embedding 超时设的是 8s，而 Java 侧检索读超时是 6s。Ollama 挂住时，
每次用户提问都会先浪费 6s 再降级回答——熔断把这个等待降到毫秒级。

状态机：
    closed  --连续失败达阈值--> open
    open    --冷却时间到--> half_open（只放行 1 个探针请求）
    half_open --成功--> closed；--失败--> open（重新计时）

刻意不做"单次失败重试"：检索是回答的前置步骤，一次重试就要再等一个超时，
总预算必然超过 Java 的 6s 上限，重试反而让用户等更久。快速失败 + 熔断更合适。
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class CircuitBreaker:
    """单进程内的失败计数熔断器，按名字区分下游。"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_seconds = max(0.0, reset_timeout_seconds)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_calls = 0
        self._trips = 0
        self._last_error = ""

    # ---------- 调用方接口 ----------

    def allow(self) -> bool:
        """本次调用是否可以打到下游。"""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if self._clock() - self._opened_at < self.reset_timeout_seconds:
                    return False
                # 冷却结束：转半开，只放一个探针，避免恢复瞬间被并发打满。
                self._state = "half_open"
                self._half_open_calls = 0
            if self._half_open_calls >= self.half_open_max_calls:
                return False
            self._half_open_calls += 1
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._half_open_calls = 0
            self._last_error = ""

    def record_failure(self, error: object = "") -> None:
        with self._lock:
            self._last_error = str(error)[:200]
            if self._state == "half_open":
                # 探针失败：立刻重新熔断，不用再攒满阈值。
                self._trip()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._trip()

    # ---------- 观测 ----------

    @property
    def state(self) -> str:
        with self._lock:
            return self._effective_state_locked()

    def _effective_state_locked(self) -> str:
        """只读状态（调用方须持锁）。

        冷却已过的 open 对外显示 half_open：否则 /stats 会一直显示"已熔断"，
        而实际上下一次调用已经会放行探针。注意不能在这里再取锁——Lock 非重入，
        snapshot() 持有锁时调用本方法会直接死锁。
        """
        if (
            self._state == "open"
            and self._clock() - self._opened_at >= self.reset_timeout_seconds
        ):
            return "half_open"
        return self._state

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self._effective_state_locked(),
                "failures": self._failures,
                "failure_threshold": self.failure_threshold,
                "reset_timeout_seconds": self.reset_timeout_seconds,
                "opened_for_ms": (
                    round((self._clock() - self._opened_at) * 1000, 1)
                    if self._state in {"open", "half_open"}
                    else 0.0
                ),
                "trips": self._trips,
                "last_error": self._last_error,
            }

    def retry_after_seconds(self) -> float:
        """还要多久才会放行探针；closed 时为 0。"""
        with self._lock:
            if self._state != "open":
                return 0.0
            return max(0.0, self.reset_timeout_seconds - (self._clock() - self._opened_at))

    def reset(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._half_open_calls = 0
            self._opened_at = 0.0
            self._last_error = ""

    def _trip(self) -> None:
        """进入 open 并重新计时（内部调用，须已持锁）。"""
        self._state = "open"
        self._opened_at = self._clock()
        self._failures = 0
        self._half_open_calls = 0
        self._trips += 1


class CircuitOpenError(RuntimeError):
    """熔断期内的快速失败；上层据此降级，不再调用下游。"""

    def __init__(self, breaker: CircuitBreaker) -> None:
        self.breaker_name = breaker.name
        self.retry_after_seconds = breaker.retry_after_seconds()
        super().__init__(
            f"{breaker.name} 连续失败已熔断，约 {self.retry_after_seconds:.0f}s 后重试探针"
        )
