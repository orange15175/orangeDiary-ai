"""滑动窗口限流的 Redis 实现（对齐 `SlidingWindowLimiter`）。

一次 Lua 调用里"先全部检查、再全部扣减"，保证原子：分批检查会出现
"前两个窗口通过、第三个超限"时已经扣掉一部分额度的中间态。

超限后是拒绝还是放行由调用方决定——社区写操作 fail-closed，AI 配额 fail-open。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# 与 Java 一致：固定 +08:00，避免依赖系统 tzdata（Windows 上没有）。
APP_ZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

_CONSUME_SCRIPT = """
for i, key in ipairs(KEYS) do
  local current = tonumber(redis.call('GET', key) or '0')
  local maximum = tonumber(ARGV[(i - 1) * 3 + 1])
  local weight = tonumber(ARGV[(i - 1) * 3 + 2])
  if current + weight > maximum then return i end
end
for i, key in ipairs(KEYS) do
  local weight = tonumber(ARGV[(i - 1) * 3 + 2])
  local ttl = tonumber(ARGV[(i - 1) * 3 + 3])
  local value = redis.call('INCRBY', key, weight)
  if value == weight then redis.call('EXPIRE', key, ttl) end
end
return 0
"""

_REFUND_SCRIPT = """
for i, key in ipairs(KEYS) do
  local weight = tonumber(ARGV[i])
  local value = redis.call('DECRBY', key, weight)
  if value < 0 then redis.call('SET', key, 0, 'KEEPTTL') end
end
return 0
"""


class LimiterUnavailable(Exception):
    """Redis 不可用；降级策略由调用方决定。"""


@dataclass(frozen=True)
class Bucket:
    name: str
    maximum: int
    weight: int
    ttl_seconds: int


def window(name: str, seconds: int, maximum: int, weight: int = 1) -> Bucket:
    """按 seconds 取整的滚动窗口，例如"每分钟最多 30 次"。"""
    now = int(datetime.now(timezone.utc).timestamp())
    index = now // seconds
    ttl = seconds - now % seconds
    return Bucket(f"{name}:{index}", maximum, weight, ttl)


def local_day(name: str, maximum: int, weight: int = 1) -> Bucket:
    """自然日窗口（Asia/Shanghai），TTL 到次日 0 点。"""
    now = datetime.now(APP_ZONE)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=APP_ZONE)
    ttl = max(1, int((tomorrow - now).total_seconds()))
    return Bucket(f"{name}:{now.date().isoformat()}", maximum, weight, ttl)


def _keys(source: str, action: str, subject_id: int, buckets: list[Bucket]) -> list[str]:
    # 与 Java 相同的键格式，便于在 redis-cli 按前缀排查。
    return [f"{source}:limit:{action}:user:{subject_id}:{bucket.name}" for bucket in buckets]


class RedisLimiter:
    def __init__(self, client) -> None:
        self._client = client

    async def consume(
        self, source: str, action: str, subject_id: int, buckets: list[Bucket]
    ) -> Bucket | None:
        keys = _keys(source, action, subject_id, buckets)
        args: list[str] = []
        for bucket in buckets:
            args.extend([str(bucket.maximum), str(bucket.weight), str(bucket.ttl_seconds)])
        try:
            denied = await self._client.eval(_CONSUME_SCRIPT, len(keys), *keys, *args)
        except Exception as exc:  # redis.RedisError 及其子类
            raise LimiterUnavailable("限流存储不可用") from exc
        if denied is None:
            raise LimiterUnavailable("限流脚本返回空结果")
        value = int(denied)
        return buckets[value - 1] if value > 0 else None

    async def refund(
        self, source: str, action: str, subject_id: int, buckets: list[Bucket]
    ) -> None:
        keys = _keys(source, action, subject_id, buckets)
        try:
            await self._client.eval(
                _REFUND_SCRIPT, len(keys), *keys, *[str(bucket.weight) for bucket in buckets]
            )
        except Exception:  # pragma: no cover - 退还是补偿动作
            pass
