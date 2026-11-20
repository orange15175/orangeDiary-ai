"""社区限流与图片校验测试。

限流这层只做三件事，每一件错了都是线上事故：

1. 超限 → 429 + 正确的 `Retry-After`（取被拒窗口的剩余 TTL，不是固定的 60）；
2. Redis 不可用 → 503，**不能**降级放行（社区写操作是安全边界）；
3. 空文件不扣字节额度（否则一次误点就吃掉当天的上传配额）。

图片校验测的是魔数：只信 `Content-Type` 等于允许上传任意内容。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings
from app.core.errors import BusinessError
from app.core.limiter import Bucket, LimiterUnavailable, RedisLimiter, local_day, window
from app.services.community_limit import CommunityLimiter
from app.services.image_storage import (
    MAX_AVATAR_BYTES,
    MAX_IMAGE_BYTES,
    build_object_key,
    matches_image_type,
    validate_upload,
)

# 所有样本都取 12 字节：校验函数按 Java `readNBytes(12)` 的语义先判长度，
# 短于 12 字节一律判假——一个 10 字节的"GIF"本来也不可能是合法图片。
PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d"
JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00\x01"
GIF_HEADER = b"GIF89a" + b"\x01\x00\x01\x00\x80\x00"
WEBP_HEADER = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 "


def settings(**overrides) -> Settings:
    """Settings 是 frozen dataclass，只能整体重建。"""
    return replace(Settings.from_env(), **overrides)


class RecordingClient:
    """记录 eval 的入参，返回预设结果。"""

    def __init__(self, result: int = 0, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    async def eval(self, script, numkeys, *args):
        self.calls.append((numkeys, args))
        if self.error is not None:
            raise self.error
        return self.result


# ==================== RedisLimiter ====================


@pytest.mark.asyncio
async def test_consume_builds_keys_and_args_like_java():
    client = RecordingClient(result=0)
    limiter = RedisLimiter(client)
    buckets = [Bucket("hour:123", 20, 1, 1800)]

    denied = await limiter.consume("community", "post", 7, buckets)

    assert denied is None
    numkeys, args = client.calls[0]
    assert numkeys == 1
    assert args[0] == "community:limit:post:user:7:hour:123"
    # ARGV 是三组 (maximum, weight, ttl)。
    assert args[1:] == ("20", "1", "1800")


@pytest.mark.asyncio
async def test_consume_returns_denied_bucket():
    client = RecordingClient(result=2)
    limiter = RedisLimiter(client)
    buckets = [
        Bucket("minute:1", 30, 1, 40),
        Bucket("day:2026-11-18", 500, 1, 3600),
    ]

    denied = await limiter.consume("community", "comment", 7, buckets)

    assert denied is buckets[1]
    assert denied.ttl_seconds == 3600


@pytest.mark.asyncio
async def test_consume_wraps_redis_failure():
    limiter = RedisLimiter(RecordingClient(error=ConnectionError("down")))
    with pytest.raises(LimiterUnavailable):
        await limiter.consume("community", "post", 7, [Bucket("hour:1", 20, 1, 60)])


def test_window_and_local_day_are_stable_within_the_unit():
    first = window("hour", 3600, 20)
    second = window("hour", 3600, 20)
    assert first.name == second.name
    assert 0 < first.ttl_seconds <= 3600

    day = local_day("day", 500)
    assert day.name.startswith("day:")
    assert 0 < day.ttl_seconds <= 86400


# ==================== CommunityLimiter ====================


class StubLimiter:
    """直接返回"被哪个窗口拒绝"，不经过 Lua。"""

    def __init__(self, denied: Bucket | None = None, error: Exception | None = None) -> None:
        self.denied = denied
        self.error = error
        self.buckets_seen: list[Bucket] = []

    async def consume(self, source, action, subject_id, buckets):
        self.buckets_seen = buckets
        if self.error is not None:
            raise self.error
        return self.denied


@pytest.mark.asyncio
async def test_denied_post_reports_retry_after():
    limiter = CommunityLimiter(StubLimiter(Bucket("hour:1", 20, 1, 1800)), settings())
    with pytest.raises(BusinessError) as info:
        await limiter.check_post(1)
    assert info.value.code == 429
    assert info.value.message == "发帖次数已达上限，请稍后重试"
    assert info.value.retry_after_seconds == 1800


@pytest.mark.asyncio
async def test_redis_failure_is_fail_closed():
    limiter = CommunityLimiter(StubLimiter(error=LimiterUnavailable("down")), settings())
    with pytest.raises(BusinessError) as info:
        await limiter.check_comment(1)
    assert info.value.code == 503
    assert info.value.message == "操作保护服务暂不可用，请稍后重试"


@pytest.mark.asyncio
async def test_comment_checks_two_windows():
    stub = StubLimiter()
    limiter = CommunityLimiter(stub, settings())
    await limiter.check_comment(1)
    assert [bucket.name.split(":")[0] for bucket in stub.buckets_seen] == ["minute", "day"]


@pytest.mark.asyncio
async def test_empty_upload_skips_byte_quota():
    stub = StubLimiter()
    limiter = CommunityLimiter(stub, settings())
    await limiter.check_image_upload(1, 0)
    assert stub.buckets_seen == []

    await limiter.check_image_upload(1, 1024)
    assert [bucket.name.split(":")[0] for bucket in stub.buckets_seen] == ["hour", "bytes"]
    assert stub.buckets_seen[1].weight == 1024


# ==================== 图片校验 ====================


@pytest.mark.parametrize(
    "content_type,header",
    [
        ("image/png", PNG_HEADER),
        ("image/jpeg", JPEG_HEADER),
        ("image/gif", GIF_HEADER),
        ("image/webp", WEBP_HEADER),
    ],
)
def test_magic_bytes_match_declared_type(content_type, header):
    assert matches_image_type(header, content_type) is True


@pytest.mark.parametrize(
    "content_type,header",
    [
        # 声明的类型与真实内容不一致：这就是"改后缀 + 伪造请求头"的攻击面。
        ("image/png", JPEG_HEADER),
        ("image/jpeg", PNG_HEADER),
        ("image/webp", b"RIFFxxxxAVI "),
        ("image/gif", b"GIF88a" + b"\x00\x00"),
        ("image/png", b"\x89PN"),
    ],
)
def test_mismatched_or_short_header_is_rejected(content_type, header):
    assert matches_image_type(header, content_type) is False


def test_validate_rejects_empty_and_oversized():
    with pytest.raises(BusinessError) as info:
        validate_upload(size=0, content_type="image/png", header=PNG_HEADER, avatar=False)
    assert info.value.message == "请选择图片"

    with pytest.raises(BusinessError) as info:
        validate_upload(
            size=MAX_IMAGE_BYTES + 1,
            content_type="image/png",
            header=PNG_HEADER,
            avatar=False,
        )
    assert info.value.message == "单张图片不能超过10MB"


def test_validate_rejects_unsupported_type():
    with pytest.raises(BusinessError) as info:
        validate_upload(
            size=1024, content_type="image/svg+xml", header=b"<svg xmlns=", avatar=False
        )
    assert info.value.message == "仅支持 JPG、PNG、WEBP 或 GIF 图片"


def test_validate_avatar_rejects_gif_and_large_file():
    with pytest.raises(BusinessError) as info:
        validate_upload(size=1024, content_type="image/gif", header=GIF_HEADER, avatar=True)
    assert info.value.message == "头像仅支持不超过5MB的 JPG、PNG、WEBP 图片"

    with pytest.raises(BusinessError) as info:
        validate_upload(
            size=MAX_AVATAR_BYTES + 1,
            content_type="image/png",
            header=PNG_HEADER,
            avatar=True,
        )
    assert info.value.message == "头像仅支持不超过5MB的 JPG、PNG、WEBP 图片"


def test_validate_rejects_content_type_mismatch():
    with pytest.raises(BusinessError) as info:
        validate_upload(size=1024, content_type="image/png", header=JPEG_HEADER, avatar=False)
    assert info.value.message == "图片内容与格式不匹配"


def test_object_key_carries_owner_and_extension():
    post_key = build_object_key(user_id=12, content_type="image/png", avatar=False)
    assert post_key.startswith("posts/12/")
    assert post_key.endswith(".png")

    avatar_key = build_object_key(user_id=12, content_type="image/webp", avatar=True)
    assert avatar_key.startswith("avatars/12/")
    assert avatar_key.endswith(".webp")

    # 同一个人连续两次上传不能撞 key。
    again = build_object_key(user_id=12, content_type="image/png", avatar=False)
    assert again != post_key
