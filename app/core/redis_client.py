"""按 URL 复用 Redis 客户端。

之前认证会话自己 new 一个客户端，社区限流再 new 一个，进程里会积累多条连接池。
统一成"每个 URL 一个客户端"，停机时一起关闭。
"""
from __future__ import annotations

_clients: dict[str, object] = {}


def get_redis(url: str):
    if not url:
        raise RuntimeError("REDIS_URL 未配置")
    client = _clients.get(url)
    if client is None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - 依赖缺失属于部署问题
            raise RuntimeError("缺少 redis 依赖") from exc
        client = Redis.from_url(url, decode_responses=True)
        _clients[url] = client
    return client


async def close_redis_clients() -> None:
    global _clients
    for client in _clients.values():
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - 停机路径不应抛错
            pass
    _clients = {}
