"""鉴权依赖：逐条对齐 `JwtAuthenticationInterceptor`。

判定顺序不能改，改了就出现"Java 拒绝、Python 放行"的差分：

1. 没有 `Authorization: Bearer` → 401「请先登录」
2. JWT 解析失败/过期 → 401「登录状态已失效，请重新登录」
3. Redis 会话不存在、状态非 active、或会话里的 userId 与令牌不一致 → 401 同上
4. 用户不存在或 status != active → 401「账号不存在或已被禁用」
5. Redis / 数据库不可用 → 503「登录服务暂不可用」，不能伪装成 401
   （401 会让前端清掉本地令牌，用户明明登录有效却被登出）

第 3 条的 userId 一致性容易被漏掉：令牌是签名的拿不到伪造，但会话被
"另一个用户重新分配 sid" 时（例如并发刷新脚本 bug），只查 status 会放行错人。
"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError
from app.core.security import decode_jwt_secret, parse_access_token
from app.db.session import get_session
from app.models.user import User
from app.services.auth_session import AuthSessionStore, get_redis_session_store

try:  # redis 是运行依赖，单测可以完全不装
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover

    class RedisError(Exception):  # type: ignore[no-redef]
        pass


# 与 Java `AuthSessionService.ACTIVE_STATUS` 同值。
_ACTIVE = "active"

_UNAUTHORIZED_MESSAGE = "登录状态已失效，请重新登录"


def get_settings() -> Settings:
    """按请求读取配置。

    不做模块级缓存：测试会改环境变量，缓存住会让每个用例互相污染。
    Settings.from_env 只是若干 os.getenv 调用，开销可以忽略。
    """
    return Settings.from_env()


def get_session_store(settings: Settings = Depends(get_settings)) -> AuthSessionStore:
    try:
        return get_redis_session_store(settings)
    except RuntimeError as exc:
        # 没配 REDIS_URL 属于部署问题：500 会把内部信息吐给前端，统一转 503。
        raise BusinessError(503, "登录服务暂不可用") from exc


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization") or ""
    if not authorization.startswith("Bearer "):
        raise BusinessError(401, "请先登录")
    token = authorization[7:].strip()
    if not token:
        raise BusinessError(401, "请先登录")
    return token


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    store: AuthSessionStore = Depends(get_session_store),
) -> User:
    token = _bearer_token(request)

    try:
        user_id, session_id = parse_access_token(token, decode_jwt_secret(settings.jwt_secret))
    except ValueError as exc:
        # 密钥没配置也是 ValueError，但这是部署错误，不能当成"用户令牌过期"。
        if "JWT_SECRET" in str(exc):
            raise BusinessError(503, "登录服务暂不可用") from exc
        raise BusinessError(401, _UNAUTHORIZED_MESSAGE) from exc

    try:
        state = await store.read(session_id)
    except RedisError as exc:
        raise BusinessError(503, "登录服务暂不可用") from exc
    if state is None or state.status != _ACTIVE or state.user_id != user_id:
        raise BusinessError(401, _UNAUTHORIZED_MESSAGE)

    user = await db.get(User, user_id)
    if user is None or user.status != _ACTIVE:
        raise BusinessError(401, "账号不存在或已被禁用")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员判定沿用 Java `AdminInterceptor` 的口径：数据库里的 role 字段。"""
    if user.role != "admin":
        raise BusinessError(403, "无权限访问")
    return user
