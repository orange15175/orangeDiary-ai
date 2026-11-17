"""限流组件测试：并发上限、排队、用户频率、Redis 降级与名额回收。"""

from __future__ import annotations

import threading
import time
import unittest

from app.rag.limiter import BusyError, RagLimiter


class FakeRedis:
    """只实现限流用到的四个命令，够验证计数与回滚。"""

    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.fail = False
        self._lock = threading.Lock()

    def _guard(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    def incr(self, key: str) -> int:
        self._guard()
        with self._lock:
            self.data[key] = self.data.get(key, 0) + 1
            return self.data[key]

    def decr(self, key: str) -> int:
        self._guard()
        with self._lock:
            self.data[key] = self.data.get(key, 0) - 1
            return self.data[key]

    def set(self, key: str, value: int) -> None:
        self._guard()
        self.data[key] = value

    def expire(self, key: str, seconds: int) -> None:
        self._guard()


class ConcurrencyTest(unittest.TestCase):
    def test_rejects_when_queue_is_full(self):
        limiter = RagLimiter(max_concurrency=1, max_queue=0, max_wait_seconds=0.05)
        ticket = limiter.acquire()
        with self.assertRaises(BusyError) as ctx:
            limiter.acquire()
        self.assertEqual("queue_full", ctx.exception.reason)
        limiter.release(ticket)
        self.assertEqual(0, limiter.stats()["inflight"])

    def test_waits_for_slot_when_queue_allows(self):
        limiter = RagLimiter(max_concurrency=1, max_queue=1, max_wait_seconds=2.0)
        ticket = limiter.acquire()
        result: list[str] = []

        def worker() -> None:
            inner = limiter.acquire()
            result.append("ok")
            limiter.release(inner)

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.2)
        self.assertEqual([], result, "名额未释放前不应放行")
        limiter.release(ticket)
        thread.join(timeout=2)
        self.assertEqual(["ok"], result)

    def test_queue_wait_times_out(self):
        limiter = RagLimiter(max_concurrency=1, max_queue=1, max_wait_seconds=0.1)
        ticket = limiter.acquire()
        with self.assertRaises(BusyError) as ctx:
            limiter.acquire()
        self.assertEqual("queue_timeout", ctx.exception.reason)
        limiter.release(ticket)

    def test_run_releases_slot_on_exception(self):
        limiter = RagLimiter(max_concurrency=1, max_queue=1, max_wait_seconds=0.5)

        def boom() -> None:
            raise ValueError("失败也要还名额")

        with self.assertRaises(ValueError):
            limiter.run("u1", boom)
        stats = limiter.stats()
        self.assertEqual(0, stats["inflight"])
        self.assertEqual(1, stats["accepted"])
        # 名额已归还：下一次仍能拿到
        limiter.run("u1", lambda: None)


class UserRateTest(unittest.TestCase):
    def test_user_window_limits_and_recovers(self):
        now = [1000.0]
        limiter = RagLimiter(
            max_concurrency=4,
            max_queue=4,
            user_limit=2,
            user_window_seconds=60.0,
            clock=lambda: now[0],
        )
        limiter.run("alice", lambda: None)
        limiter.run("alice", lambda: None)
        with self.assertRaises(BusyError) as ctx:
            limiter.run("alice", lambda: None)
        self.assertEqual("user_rate_limited", ctx.exception.reason)
        # 另一个用户不受影响
        limiter.run("bob", lambda: None)
        # 窗口滑过后恢复
        now[0] += 61
        limiter.run("alice", lambda: None)

    def test_anonymous_is_not_rate_limited(self):
        limiter = RagLimiter(max_concurrency=2, max_queue=2, user_limit=1)
        limiter.run(None, lambda: None)
        limiter.run(None, lambda: None)
        self.assertNotIn("user_rate_limited", limiter.stats()["rejected"])


class RedisTest(unittest.TestCase):
    def test_global_limit_rejects_and_rolls_back(self):
        redis = FakeRedis()
        limiter = RagLimiter(
            max_concurrency=4, max_queue=4, redis_client=redis, global_limit=1
        )
        ticket = limiter.acquire()
        with self.assertRaises(BusyError) as ctx:
            limiter.acquire()
        self.assertEqual("global_limit", ctx.exception.reason)
        # 被拒那次必须把计数还回去，否则配额会被永久吃掉
        self.assertEqual(1, redis.data["rag:inflight"])
        limiter.release(ticket)
        self.assertEqual(0, redis.data["rag:inflight"])

    def test_redis_failure_degrades_to_local_limit(self):
        redis = FakeRedis()
        redis.fail = True
        limiter = RagLimiter(
            max_concurrency=2, max_queue=2, redis_client=redis, global_limit=10
        )
        limiter.run("u", lambda: None)
        stats = limiter.stats()
        self.assertTrue(stats["redis"].startswith("degraded:"), stats)
        self.assertEqual(0, stats["inflight"])

    def test_negative_counter_is_clamped(self):
        redis = FakeRedis()
        limiter = RagLimiter(max_concurrency=2, max_queue=2, redis_client=redis, global_limit=5)
        redis.data["rag:inflight"] = 0
        ticket = limiter.acquire()
        limiter.release(ticket)
        self.assertGreaterEqual(redis.data["rag:inflight"], 0)


if __name__ == "__main__":
    unittest.main()
