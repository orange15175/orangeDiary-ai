"""熔断器状态机的单元测试。"""

from __future__ import annotations

import unittest

from app.rag.circuit import CircuitBreaker, CircuitOpenError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.breaker = CircuitBreaker(
            "vector", failure_threshold=3, reset_timeout_seconds=30, clock=self.clock
        )

    def test_closed_allows_calls(self):
        self.assertTrue(self.breaker.allow())
        self.assertEqual(self.breaker.state, "closed")

    def test_opens_after_threshold_failures(self):
        for _ in range(2):
            self.breaker.record_failure("timeout")
            self.assertTrue(self.breaker.allow())
        self.breaker.record_failure("timeout")
        self.assertEqual(self.breaker.state, "open")
        self.assertFalse(self.breaker.allow())

    def test_success_before_threshold_resets_counter(self):
        self.breaker.record_failure("e1")
        self.breaker.record_failure("e2")
        self.breaker.record_success()
        self.breaker.record_failure("e3")
        self.breaker.record_failure("e4")
        self.assertEqual(self.breaker.state, "closed")

    def test_cooldown_then_single_half_open_probe(self):
        for _ in range(3):
            self.breaker.record_failure("timeout")
        self.clock.advance(29)
        self.assertFalse(self.breaker.allow())
        self.clock.advance(2)
        # 冷却结束：放行一个探针，其余并发调用仍然被挡。
        self.assertTrue(self.breaker.allow())
        self.assertFalse(self.breaker.allow())
        self.assertEqual(self.breaker.state, "half_open")

    def test_probe_success_closes(self):
        for _ in range(3):
            self.breaker.record_failure("timeout")
        self.clock.advance(31)
        self.assertTrue(self.breaker.allow())
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, "closed")
        self.assertTrue(self.breaker.allow())
        self.assertEqual(self.breaker.snapshot()["failures"], 0)

    def test_probe_failure_reopens_immediately(self):
        for _ in range(3):
            self.breaker.record_failure("timeout")
        self.clock.advance(31)
        self.assertTrue(self.breaker.allow())
        self.breaker.record_failure("still broken")
        self.assertEqual(self.breaker.state, "open")
        self.assertFalse(self.breaker.allow())
        self.assertEqual(self.breaker.snapshot()["trips"], 2)

    def test_retry_after_seconds_counts_down(self):
        for _ in range(3):
            self.breaker.record_failure("timeout")
        self.assertAlmostEqual(self.breaker.retry_after_seconds(), 30.0)
        self.clock.advance(10)
        self.assertAlmostEqual(self.breaker.retry_after_seconds(), 20.0)

    def test_circuit_open_error_reports_backoff(self):
        for _ in range(3):
            self.breaker.record_failure("timeout")
        error = CircuitOpenError(self.breaker)
        self.assertEqual(error.breaker_name, "vector")
        self.assertIn("熔断", str(error))
        self.assertAlmostEqual(error.retry_after_seconds, 30.0)

    def test_snapshot_includes_last_error(self):
        self.breaker.record_failure("连接被拒绝")
        snapshot = self.breaker.snapshot()
        self.assertEqual(snapshot["last_error"], "连接被拒绝")
        self.assertEqual(snapshot["failure_threshold"], 3)


if __name__ == "__main__":
    unittest.main()
