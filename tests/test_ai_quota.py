"""AI 配额测试。

三条与社区限流相反的处理，一条都不能"顺手套用"：

1. Redis 不可用 → **放行**（社区写操作是 503）。配额只是防滥用，不是安全边界。
2. 管理员免次数：演示账号被自己的配额拦住是最难看的故障。
3. 429 的 `Retry-After` 取被拒窗口的剩余 TTL，不是固定 60 秒。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.core.errors import BusinessError
from app.core.limiter import LimiterUnavailable
from app.services.ai_quota import AiQuota


class FakeLimiter:
    def __init__(self, deny: str | None = None, fail: bool = False) -> None:
        self.deny = deny
        self.fail = fail
        self.consumed: list[tuple] = []
        self.refunded: list[tuple] = []

    async def consume(self, source, action, subject_id, buckets):
        self.consumed.append((source, action, subject_id, buckets))
        if self.fail:
            raise LimiterUnavailable("redis down")
        if self.deny == "day":
            return buckets[0]
        if self.deny == "minute":
            return buckets[1]
        return None

    async def refund(self, source, action, subject_id, buckets):
        self.refunded.append((source, action, subject_id, buckets))


def settings(**overrides) -> Settings:
    base = Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret="x" * 32,
    )
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


@pytest.mark.asyncio
async def test_consume_two_buckets() -> None:
    limiter = FakeLimiter()
    ticket = await AiQuota(limiter, settings()).acquire(7)

    assert ticket is not None
    assert ticket.user_id == 7
    assert len(ticket.buckets) == 2
    assert ticket.buckets[0].name.startswith("day:")
    assert ticket.buckets[0].maximum == 50
    assert ticket.buckets[1].name.startswith("minute:")
    assert ticket.buckets[1].maximum == 10
    assert limiter.consumed[0][:3] == ("ai", "chat", 7)


@pytest.mark.asyncio
async def test_admin_is_exempt() -> None:
    limiter = FakeLimiter()
    assert await AiQuota(limiter, settings()).acquire(1, is_admin=True) is None
    assert limiter.consumed == []


@pytest.mark.asyncio
async def test_admin_exemption_can_be_disabled() -> None:
    limiter = FakeLimiter()
    quota = AiQuota(limiter, settings(ai_quota_admin_exempt=False))
    assert await quota.acquire(1, is_admin=True) is not None
    assert len(limiter.consumed) == 1


@pytest.mark.asyncio
async def test_day_limit_message_and_retry_after() -> None:
    limiter = FakeLimiter(deny="day")
    with pytest.raises(BusinessError) as error:
        await AiQuota(limiter, settings()).acquire(1)

    assert error.value.code == 429
    assert error.value.message == "今日 AI 问答次数已用完（每日 50 次），明天 0 点恢复"
    # Retry-After 取被拒窗口剩余 TTL（到次日 0 点），不是固定 60。
    assert error.value.retry_after_seconds > 60


@pytest.mark.asyncio
async def test_minute_limit_message() -> None:
    limiter = FakeLimiter(deny="minute")
    with pytest.raises(BusinessError) as error:
        await AiQuota(limiter, settings()).acquire(1)

    assert error.value.message == "提问太频繁了，请稍等几秒再试"
    assert 0 < error.value.retry_after_seconds <= 60


@pytest.mark.asyncio
async def test_redis_failure_fails_open() -> None:
    limiter = FakeLimiter(fail=True)
    assert await AiQuota(limiter, settings()).acquire(1) is None


@pytest.mark.asyncio
async def test_refund_replays_same_buckets() -> None:
    limiter = FakeLimiter()
    quota = AiQuota(limiter, settings())
    ticket = await quota.acquire(3)
    await quota.refund(ticket)

    assert len(limiter.refunded) == 1
    assert limiter.refunded[0][2] == 3
    assert limiter.refunded[0][3] == ticket.buckets


@pytest.mark.asyncio
async def test_refund_without_ticket_is_noop() -> None:
    limiter = FakeLimiter()
    await AiQuota(limiter, settings()).refund(None)
    assert limiter.refunded == []
