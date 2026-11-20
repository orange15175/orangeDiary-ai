"""登录会话存储。

Java 侧把会话放在 Redis hash 里，靠 Lua 保证"检查 + 写入"原子。这里逐字复刻三段
脚本的语义，尤其是刷新复用检测：`previousRefreshHash` 命中即销毁整个会话——这是
防 refresh token 被盗用的核心。删掉它，一次泄露就等于永久登录。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

_USER_ID_FIELD = "userId"
_STATUS_FIELD = "status"
_ACTIVE = "active"

# 与 Java AuthSessionService 中的脚本保持同构，改一处必须同步另一处。
_CREATE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
redis.call('HSET', KEYS[1],
    'userId', ARGV[1],
    'refreshHash', ARGV[2],
    'previousRefreshHash', '',
    'status', 'active')
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""

_ROTATE_SCRIPT = """
local storedHash = redis.call('HGET', KEYS[1], 'refreshHash')
local previousHash = redis.call('HGET', KEYS[1], 'previousRefreshHash')
local status = redis.call('HGET', KEYS[1], 'status')
if previousHash and previousHash ~= '' and previousHash == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return -1
end
if not storedHash or storedHash ~= ARGV[1] or status ~= 'active' then
    return 0
end
redis.call('HSET', KEYS[1],
    'previousRefreshHash', storedHash,
    'refreshHash', ARGV[2])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""

_LOGOUT_SCRIPT = """
local storedHash = redis.call('HGET', KEYS[1], 'refreshHash')
local previousHash = redis.call('HGET', KEYS[1], 'previousRefreshHash')
if (storedHash and storedHash == ARGV[1]) or (previousHash and previousHash == ARGV[1]) then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

# rotate 的返回值含义与 Java 一致：1 成功、0 无效、-1 检测到令牌复用（会话已销毁）。
ROTATE_OK = 1
ROTATE_INVALID = 0
ROTATE_REUSED = -1


@dataclass(frozen=True)
class SessionState:
    user_id: int
    status: str


def new_session_id() -> str:
    return uuid.uuid4().hex


def new_refresh_secret() -> str:
    # 32 字节 → 43 个 base64url 字符，落在 Java 侧正则 [A-Za-z0-9_-]{32,128} 内。
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    import base64

    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def parse_refresh_token(token: str) -> tuple[str, str]:
    """`sessionId.secret` → (sessionId, secret)；格式不对按无效令牌处理。"""
    parts = (token or "").split(".")
    if len(parts) != 2:
        raise ValueError("refresh token 格式不正确")
    session_id, secret = parts
    if not 20 <= len(session_id) <= 64 or not 32 <= len(secret) <= 128:
        raise ValueError("refresh token 长度不合法")
    if not all(ch.isalnum() or ch in "-_" for ch in session_id + secret):
        raise ValueError("refresh token 含非法字符")
    return session_id, secret


class AuthSessionStore(Protocol):
    async def create(self, session_id: str, user_id: int, refresh_hash: str, ttl_ms: int) -> bool: ...

    async def read(self, session_id: str) -> SessionState | None: ...

    async def rotate(self, session_id: str, old_hash: str, new_hash: str, ttl_ms: int) -> int: ...

    async def delete_if_matches(self, session_id: str, token_hash: str) -> int: ...

    async def ping(self) -> bool: ...


class RedisAuthSessionStore:
    def __init__(self, client, namespace: str) -> None:
        self._client = client
        self._namespace = namespace

    def _key(self, session_id: str) -> str:
        return f"{self._namespace}{session_id}"

    async def create(self, session_id: str, user_id: int, refresh_hash: str, ttl_ms: int) -> bool:
        result = await self._client.eval(
            _CREATE_SCRIPT, 1, self._key(session_id), str(user_id), refresh_hash, str(ttl_ms)
        )
        return int(result) == 1

    async def read(self, session_id: str) -> SessionState | None:
        values = await self._client.hmget(self._key(session_id), [_USER_ID_FIELD, _STATUS_FIELD])
        if not values or values[0] is None or values[1] is None:
            return None
        raw_user_id = values[0]
        status = values[1]
        if isinstance(raw_user_id, bytes):
            raw_user_id = raw_user_id.decode("ascii")
        if isinstance(status, bytes):
            status = status.decode("ascii")
        try:
            return SessionState(user_id=int(raw_user_id), status=str(status))
        except (TypeError, ValueError):
            return None

    async def rotate(self, session_id: str, old_hash: str, new_hash: str, ttl_ms: int) -> int:
        result = await self._client.eval(
            _ROTATE_SCRIPT, 1, self._key(session_id), old_hash, new_hash, str(ttl_ms)
        )
        return int(result)

    async def delete_if_matches(self, session_id: str, token_hash: str) -> int:
        result = await self._client.eval(
            _LOGOUT_SCRIPT, 1, self._key(session_id), token_hash
        )
        return int(result)

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False


class InMemoryAuthSessionStore:
    """测试用；语义与 Lua 脚本保持一致，不引入 Redis 依赖。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, str]] = {}

    async def create(self, session_id: str, user_id: int, refresh_hash: str, ttl_ms: int) -> bool:
        if session_id in self._sessions:
            return False
        self._sessions[session_id] = {
            _USER_ID_FIELD: str(user_id),
            "refreshHash": refresh_hash,
            "previousRefreshHash": "",
            _STATUS_FIELD: _ACTIVE,
        }
        return True

    async def read(self, session_id: str) -> SessionState | None:
        data = self._sessions.get(session_id)
        if not data:
            return None
        return SessionState(user_id=int(data[_USER_ID_FIELD]), status=data[_STATUS_FIELD])

    async def rotate(self, session_id: str, old_hash: str, new_hash: str, ttl_ms: int) -> int:
        data = self._sessions.get(session_id)
        if not data:
            return ROTATE_INVALID
        previous = data.get("previousRefreshHash", "")
        if previous and previous == old_hash:
            # 复用旧 token：Java 侧直接 DEL，这里同样销毁，不给第二次机会。
            self._sessions.pop(session_id, None)
            return ROTATE_REUSED
        if data.get("refreshHash") != old_hash or data.get(_STATUS_FIELD) != _ACTIVE:
            return ROTATE_INVALID
        data["previousRefreshHash"] = data["refreshHash"]
        data["refreshHash"] = new_hash
        return ROTATE_OK

    async def delete_if_matches(self, session_id: str, token_hash: str) -> int:
        data = self._sessions.get(session_id)
        if not data:
            return 0
        if token_hash in {data.get("refreshHash"), data.get("previousRefreshHash")}:
            self._sessions.pop(session_id, None)
            return 1
        return 0

    async def ping(self) -> bool:
        return True


_redis_client = None


def get_redis_session_store(settings: Settings) -> RedisAuthSessionStore:
    """懒加载 Redis 客户端；未配置 REDIS_URL 时给出可诊断的错误，而不是 500 空指针。"""
    global _redis_client
    if _redis_client is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL 未配置，登录会话无法创建")
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - 依赖缺失属于部署问题
            raise RuntimeError("缺少 redis 依赖，无法创建登录会话") from exc
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisAuthSessionStore(_redis_client, settings.auth_session_namespace)


async def reset_redis_client() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
