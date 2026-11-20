"""社区写操作限流（对齐 `CommunityRateLimitService`）。

与 AI 配额的关键区别：**写操作 fail-closed**。Redis 挂了就返回 503，
宁可发不出帖，也不能让刷帖脚本把库打穿。AI 配额是反滥用，Redis 抖动时
应当放行而不是让聊天整体不可用——两者的降级方向相反，不要合并。

超限返回 429 并带 `Retry-After`，秒数取被拒那一个窗口的剩余 TTL：
用户在"每小时 20 帖"里被拦，要等的是当前小时窗口结束，不是 60 秒。
"""
from __future__ import annotations

from app.config import Settings
from app.core.errors import BusinessError
from app.core.limiter import Bucket, LimiterUnavailable, RedisLimiter, local_day, window
from app.core.redis_client import get_redis

_SOURCE = "community"


class CommunityLimiter:
    def __init__(self, limiter: RedisLimiter, settings: Settings) -> None:
        self._limiter = limiter
        self._settings = settings

    # ---------------- 对外四个入口 ----------------

    async def check_post(self, user_id: int) -> None:
        await self._consume(
            user_id,
            "post",
            "发帖",
            [window("hour", 3600, self._settings.community_posts_per_hour, 1)],
        )

    async def check_comment(self, user_id: int) -> None:
        await self._consume(
            user_id,
            "comment",
            "评论",
            [
                window("minute", 60, self._settings.community_comments_per_minute, 1),
                local_day("day", self._settings.community_comments_per_day, 1),
            ],
        )

    async def check_image_upload(self, user_id: int, size: int) -> None:
        # 空文件在更早的校验里就会被拒；这里直接返回，避免白扣一次额度。
        if size <= 0:
            return
        await self._consume(
            user_id,
            "image",
            "图片上传",
            [
                window("hour", 3600, self._settings.community_images_per_hour, 1),
                local_day("bytes", self._settings.community_image_bytes_per_day, size),
            ],
        )

    async def check_report(self, user_id: int) -> None:
        await self._consume(
            user_id,
            "report",
            "举报",
            [local_day("day", self._settings.community_reports_per_day, 1)],
        )

    # ---------------- 内部 ----------------

    async def _consume(
        self, user_id: int, action: str, label: str, buckets: list[Bucket]
    ) -> None:
        try:
            denied = await self._limiter.consume(_SOURCE, action, user_id, buckets)
        except LimiterUnavailable as exc:
            raise BusinessError(503, "操作保护服务暂不可用，请稍后重试") from exc
        if denied is not None:
            raise BusinessError(
                429, f"{label}次数已达上限，请稍后重试", denied.ttl_seconds
            )


def build_limiter(settings: Settings) -> CommunityLimiter:
    """按当前配置造一个限流器。

    Redis 地址未配置时**不**在这里抛错：真正调用时 `get_redis` 会抛
    `RuntimeError`，但那条路径被包在 `_consume` 之外。所以这里先兜一层，
    把"没有 Redis"也当成"限流不可用"，语义与连不上一致（503）。
    """
    try:
        client = get_redis(settings.redis_url)
    except RuntimeError:
        client = _MissingClient()
    return CommunityLimiter(RedisLimiter(client), settings)


class _MissingClient:
    """占位客户端：任何调用都失败，让上层统一走 503 分支。"""

    async def eval(self, *_args, **_kwargs):
        raise ConnectionError("REDIS_URL 未配置")
