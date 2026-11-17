"""检索结果缓存：同进程内按查询去重，带 TTL、容量上限与同请求合并。

设计取舍：
1. 只对"规范化后完全相同"的查询生效（去首尾空白、压缩连续空白、统一小写），
   不做语义近似——近似命中会把 A 问题的来源挂到 B 问题上，而来源必须可追溯；
2. 缓存整条 `RetrievalOutcome`（含来源、耗时、降级标注），不做分通道缓存：
   分通道缓存需要处理向量与 BM25 的版本一致性，收益远小于复杂度；
3. 同一查询并发进来只让一个请求真正检索（single-flight），其余等待并复用结果。
   rerank 单次约 1.5s，用户连点两次发送按钮是最常见的重复来源。

缓存命中必须显式暴露：`RetrievalOutcome.cache` 记 `hit`/`shared`/`miss`/`bypass`，
调用方据此知道这次没有重新检索，避免把旧的耗时当成实时性能。
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

_WHITESPACE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """归一化查询用于缓存键；只做无语义变化的处理。"""
    return _WHITESPACE.sub(" ", (query or "").strip()).lower()


@dataclass(frozen=True)
class CacheKey:
    """缓存键：查询 + 影响结果的请求参数。

    `top_k` 与 `min_score` 会改变返回条数与判定，必须进键，否则换个 top_k
    会拿到上一次条数的结果。索引版本不进键——换索引时检索服务整体重建，缓存随之丢弃。
    """

    query: str
    top_k: int
    min_score: float


class QueryCache:
    """线程安全的 TTL + LRU 缓存，另带按 key 的请求合并。"""

    def __init__(
        self,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
        wait_timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = max(0.0, ttl_seconds)
        # 等待上限要大于一次完整检索的最坏耗时，否则会退化成"等待超时再自己算"。
        self.wait_timeout_seconds = max(0.1, wait_timeout_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: "OrderedDict[CacheKey, tuple[float, Any]]" = OrderedDict()
        self._inflight: dict[CacheKey, threading.Event] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expired = 0
        self._shared = 0

    # ---------- 读写 ----------

    def get(self, key: CacheKey) -> tuple[Any, float] | None:
        """返回 (值, 已存活毫秒)；过期或不存在返回 None。命中会刷新 LRU 位置。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            now = self._clock()
            if expires_at <= now:
                self._entries.pop(key, None)
                self._expired += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value, max(0.0, self.ttl_seconds - (expires_at - now)) * 1000.0

    def put(self, key: CacheKey, value: Any) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            self._entries[key] = (self._clock() + self.ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ---------- 请求合并 ----------

    def begin(self, key: CacheKey) -> tuple[bool, threading.Event]:
        """登记一次检索。返回 (是否由本请求执行, 完成事件)。"""
        with self._lock:
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                return True, event
            self._shared += 1
            return False, event

    def end(self, key: CacheKey) -> None:
        """标记检索结束并唤醒等待者；必须放在 finally 里，异常也要唤醒。"""
        with self._lock:
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()

    # ---------- 观测 ----------

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
                "evictions": self._evictions,
                "expired": self._expired,
                "shared_waits": self._shared,
                "inflight": len(self._inflight),
            }
