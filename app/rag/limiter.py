"""检索限流：全局并发闸门 + 用户级频率 + 有限排队。

为什么需要它：rerank 是纯 CPU 的 1.5s 级操作，并发一高就互相拖慢，
最终所有人都变慢且 Java 侧 6s 读超时集体失败。宁可快速拒绝一部分请求（429），
也不要让全部请求一起超时——拒绝是可解释的，超时只表现为"没有来源"。

几个刻意的取舍：
1. **名额由工作线程自己释放**（`run()` 在 finally 里放行），不是在协程的 finally 里。
   客户端断开时协程被取消，但 `asyncio.to_thread` 里的活还在跑，此时放行会让实际
   并发超过上限；只有线程跑完才真正释放。
2. 排队有上限（`max_queue`），超出直接拒绝，不排长队：排队期间用户已经在等，
   而检索预算只有 2s 量级。
3. Redis 计数只用于跨进程的总量上限，**不做租约续租**。进程崩溃后靠 TTL 回收，
   代价是崩溃后最多 `lease_seconds` 内配额偏紧；单实例部署下这段逻辑用不上。
4. Redis 不可用时降级为进程内限额并在 stats 里标注，不因为限流组件故障拒绝服务。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("orange-diary-ai.limiter")


class BusyError(RuntimeError):
    """容量不足的快速拒绝；上层转 429 并带 Retry-After。"""

    def __init__(self, reason: str, retry_after_seconds: float = 1.0) -> None:
        self.reason = reason
        self.retry_after_seconds = max(0.1, retry_after_seconds)
        super().__init__(f"检索繁忙（{reason}），请约 {self.retry_after_seconds:.1f}s 后重试")


@dataclass
class Ticket:
    """一次放行凭证：记录是否占用了 Redis 名额，便于精确回滚。"""

    took_redis: bool = False


@dataclass
class LimiterStats:
    inflight: int = 0
    waiting: int = 0
    accepted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    redis_state: str = "disabled"

    def as_dict(self) -> dict:
        return {
            "inflight": self.inflight,
            "waiting": self.waiting,
            "accepted": self.accepted,
            "rejected": dict(self.rejected),
            "redis": self.redis_state,
        }


class RagLimiter:
    def __init__(
        self,
        max_concurrency: int = 4,
        max_queue: int = 8,
        max_wait_seconds: float = 2.0,
        user_limit: int = 10,
        user_window_seconds: float = 60.0,
        redis_client: Any | None = None,
        global_limit: int | None = None,
        redis_key: str = "rag:inflight",
        lease_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self.max_queue = max(0, max_queue)
        self.max_wait_seconds = max(0.0, max_wait_seconds)
        self.user_limit = max(1, user_limit)
        self.user_window_seconds = max(1.0, user_window_seconds)
        self.redis_client = redis_client
        self.global_limit = global_limit
        self.redis_key = redis_key
        self.lease_seconds = max(5, lease_seconds)
        self._clock = clock

        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)
        # 可重入锁：_reject() 会在已持锁的临界区里被调用（计数与拒绝必须原子），
        # 用普通 Lock 会自锁——和之前熔断器 snapshot() 踩的是同一个坑。
        self._lock = threading.RLock()
        self._waiting = 0
        self._inflight = 0
        self._accepted = 0
        self._rejected: dict[str, int] = {}
        self._user_hits: dict[str, deque[float]] = {}
        self._redis_state = "enabled" if redis_client is not None else "disabled"

    # ---------- 对外入口 ----------

    def run(self, user_key: str | None, work: Callable[[], Any]) -> Any:
        """在名额内执行 work；名额由本函数在线程结束时释放。"""
        with self._slot(user_key):
            return work()

    @contextmanager
    def _slot(self, user_key: str | None):
        ticket = self.acquire(user_key)
        try:
            yield ticket
        finally:
            self.release(ticket)

    # ---------- 名额 ----------

    def acquire(self, user_key: str | None = None) -> Ticket:
        self._check_user(user_key)

        # 先试无阻塞取名额：有空位就不该因为"队列长度为 0"被拒。
        admitted = self._semaphore.acquire(blocking=False)
        if not admitted:
            with self._lock:
                if self._waiting >= self.max_queue:
                    self._reject("queue_full")
                    raise BusyError("queue_full", retry_after_seconds=1.0)
                self._waiting += 1
            try:
                admitted = self._semaphore.acquire(timeout=self.max_wait_seconds)
            finally:
                with self._lock:
                    self._waiting -= 1
            if not admitted:
                self._reject("queue_timeout")
                raise BusyError("queue_timeout", retry_after_seconds=1.0)

        ticket = Ticket()
        try:
            ticket.took_redis = self._redis_acquire()
        except BusyError:
            self._semaphore.release()
            raise
        with self._lock:
            self._inflight += 1
            self._accepted += 1
        return ticket

    def release(self, ticket: Ticket) -> None:
        if ticket.took_redis:
            self._redis_release()
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1
        self._semaphore.release()

    # ---------- 用户级频率 ----------

    def _check_user(self, user_key: str | None) -> None:
        if not user_key:
            return
        now = self._clock()
        with self._lock:
            bucket = self._user_hits.setdefault(user_key, deque())
            cutoff = now - self.user_window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.user_limit:
                self._reject("user_rate_limited")
                raise BusyError("user_rate_limited", retry_after_seconds=self.user_window_seconds / 2)
            bucket.append(now)
            # 顺手清理长期不活跃的用户桶，避免字典无限增长。
            if len(self._user_hits) > 2048:
                for key in [k for k, v in self._user_hits.items() if not v]:
                    self._user_hits.pop(key, None)

    # ---------- Redis（跨进程总量，无租约续租） ----------

    def _redis_acquire(self) -> bool:
        if self.redis_client is None or self.global_limit is None:
            return False
        try:
            count = int(self.redis_client.incr(self.redis_key))
            if count == 1:
                self.redis_client.expire(self.redis_key, self.lease_seconds)
            if count > self.global_limit:
                self.redis_client.decr(self.redis_key)
                self._reject("global_limit")
                raise BusyError("global_limit", retry_after_seconds=1.0)
            self._redis_state = "enabled"
            return True
        except BusyError:
            raise
        except Exception as error:  # noqa: BLE001 - 限流组件故障不能拖垮检索
            self._redis_state = f"degraded:{type(error).__name__}"
            log.warning("Redis 限流失效，降级为进程内限额：%s", error)
            return False

    def _redis_release(self) -> None:
        try:
            value = int(self.redis_client.decr(self.redis_key))
            if value < 0:
                # 崩溃重启后残留的负值会永久抬高配额，直接归零。
                self.redis_client.set(self.redis_key, 0)
        except Exception as error:  # noqa: BLE001
            self._redis_state = f"degraded:{type(error).__name__}"
            log.warning("Redis 名额回滚失败：%s", error)

    # ---------- 观测 ----------

    def _reject(self, reason: str) -> None:
        with self._lock:
            self._rejected[reason] = self._rejected.get(reason, 0) + 1

    def stats(self) -> dict:
        with self._lock:
            snapshot = LimiterStats(
                inflight=self._inflight,
                waiting=self._waiting,
                accepted=self._accepted,
                rejected=dict(self._rejected),
                redis_state=self._redis_state,
            )
        return {
            "limits": {
                "max_concurrency": self.max_concurrency,
                "max_queue": self.max_queue,
                "max_wait_ms": round(self.max_wait_seconds * 1000, 1),
                "user_limit": self.user_limit,
                "user_window_seconds": self.user_window_seconds,
                "global_limit": self.global_limit,
            },
            **snapshot.as_dict(),
        }
