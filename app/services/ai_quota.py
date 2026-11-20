"""AI 问答配额（对齐 `AiQuotaService`）。

三条与社区限流**相反**的处理，都是刻意的：

1. **Redis 不可用时放行**。社区写操作 fail-closed 是安全边界；AI 配额只是防滥用，
   一次 Redis 抖动不该让整个聊天不可用。
2. **成功才计数、失败退还**。模型超时返回 502 时用户没拿到回答，不能再扣一次额度。
3. **管理员免次数、不免成本**。演示账号被自己的配额拦在门口是最难看的故障；
   但同一个账号也会把当日 token 预算打满，所以成本上限不跟着豁免。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.core.errors import BusinessError
from app.core.limiter import Bucket, LimiterUnavailable, RedisLimiter, local_day, window
from app.core.redis_client import get_redis

log = logging.getLogger("orange-diary-ai.ai_quota")

_SOURCE = "ai"
_ACTION = "chat"


@dataclass(frozen=True)
class QuotaTicket:
    """一次提问占用的额度凭证；免限或降级放行时为 None。"""

    user_id: int
    buckets: list[Bucket]


class AiQuota:
    def __init__(self, limiter: RedisLimiter, settings: Settings) -> None:
        self._limiter = limiter
        self._settings = settings

    async def acquire(self, user_id: int, is_admin: bool = False) -> QuotaTicket | None:
        if user_id is None:
            return None
        if self._settings.ai_quota_admin_exempt and is_admin:
            return None

        buckets = [
            local_day("day", self._settings.ai_daily_limit, 1),
            window("minute", 60, self._settings.ai_quota_per_minute, 1),
        ]
        try:
            denied = await self._limiter.consume(_SOURCE, _ACTION, user_id, buckets)
        except LimiterUnavailable as error:
            # 配额不是安全边界：放行 + 告警，别让 Redis 抖动变成聊天整体不可用。
            log.warning("[AI配额] 限流存储不可用，本次放行 userId=%s error=%s", user_id, error)
            return None
        if denied is None:
            return QuotaTicket(user_id, buckets)
        if denied.name.startswith("day"):
            log.info("[AI配额] 日上限用尽 userId=%s limit=%s", user_id, self._settings.ai_daily_limit)
            raise BusinessError(
                429,
                f"今日 AI 问答次数已用完（每日 {self._settings.ai_daily_limit} 次），明天 0 点恢复",
                denied.ttl_seconds,
            )
        log.info(
            "[AI配额] 提问过于频繁 userId=%s limit=%s/分钟",
            user_id,
            self._settings.ai_quota_per_minute,
        )
        raise BusinessError(429, "提问太频繁了，请稍等几秒再试", denied.ttl_seconds)

    async def refund(self, ticket: QuotaTicket | None) -> None:
        """生成失败时退还额度；best-effort，失败只留日志。"""
        if ticket is None:
            return
        await self._limiter.refund(_SOURCE, _ACTION, ticket.user_id, ticket.buckets)


def build_quota(settings: Settings) -> AiQuota:
    """Redis 未配置时用一个"永远抛错"的客户端，让 acquire 走 fail-open 分支。"""
    try:
        client = get_redis(settings.redis_url)
    except RuntimeError:
        client = _MissingClient()
    return AiQuota(RedisLimiter(client), settings)


class _MissingClient:
    async def eval(self, *_args, **_kwargs):
        raise ConnectionError("REDIS_URL 未配置")
