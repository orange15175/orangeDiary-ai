"""检索缓存（TTL / LRU / 请求合并）的单元测试。"""

from __future__ import annotations

import threading
import unittest

from app.rag.cache import CacheKey, QueryCache, normalize_query


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class NormalizeQueryTest(unittest.TestCase):
    def test_collapses_whitespace_and_case(self):
        self.assertEqual(normalize_query("  BMI  怎么算 "), "bmi 怎么算")

    def test_keeps_chinese_untouched(self):
        self.assertEqual(normalize_query("减重期间主食应该吃多少克"), "减重期间主食应该吃多少克")


class QueryCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.cache = QueryCache(max_entries=2, ttl_seconds=10, clock=self.clock)
        self.key = CacheKey("query", 3, 0.6)

    def test_miss_then_hit(self):
        self.assertIsNone(self.cache.get(self.key))
        self.cache.put(self.key, {"v": 1})
        value, age_ms = self.cache.get(self.key)
        self.assertEqual(value, {"v": 1})
        self.assertGreaterEqual(age_ms, 0.0)
        stats = self.cache.stats()
        self.assertEqual((stats["hits"], stats["misses"]), (1, 1))

    def test_entry_expires_after_ttl(self):
        self.cache.put(self.key, "v")
        self.clock.advance(9.9)
        self.assertIsNotNone(self.cache.get(self.key))
        self.clock.advance(0.2)
        self.assertIsNone(self.cache.get(self.key))
        self.assertEqual(self.cache.stats()["expired"], 1)

    def test_lru_evicts_least_recently_used(self):
        other = CacheKey("other", 3, 0.6)
        third = CacheKey("third", 3, 0.6)
        self.cache.put(self.key, "a")
        self.cache.put(other, "b")
        # 访问 key 让它变成最新，再插入第三条应淘汰 other。
        self.cache.get(self.key)
        self.cache.put(third, "c")
        self.assertIsNotNone(self.cache.get(self.key))
        self.assertIsNone(self.cache.get(other))
        self.assertEqual(self.cache.stats()["evictions"], 1)

    def test_ttl_zero_disables_storage(self):
        cache = QueryCache(max_entries=4, ttl_seconds=0, clock=self.clock)
        cache.put(self.key, "v")
        self.assertIsNone(cache.get(self.key))

    def test_begin_marks_only_first_caller_as_leader(self):
        leader, event = self.cache.begin(self.key)
        self.assertTrue(leader)
        follower, same_event = self.cache.begin(self.key)
        self.assertFalse(follower)
        self.assertIs(event, same_event)
        self.assertFalse(event.is_set())
        self.cache.end(self.key)
        self.assertTrue(event.is_set())
        self.assertEqual(self.cache.stats()["shared_waits"], 1)

    def test_end_is_safe_for_unknown_key(self):
        self.cache.end(CacheKey("never-started", 1, 0.0))

    def test_concurrent_get_put_keeps_stats_consistent(self):
        def worker(index: int) -> None:
            for _ in range(50):
                key = CacheKey(f"q{index}", 3, 0.6)
                self.cache.put(key, index)
                self.cache.get(key)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stats = self.cache.stats()
        self.assertEqual(stats["hits"] + stats["misses"], 200)


if __name__ == "__main__":
    unittest.main()
