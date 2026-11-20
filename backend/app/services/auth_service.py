"""注册 / 登录 / 刷新 / 登出。

语义全部对齐 `AuthServiceImpl` + `AuthSessionService`：账号可以是邮箱或手机号、
禁用账号返回 403、refresh 走一次性轮换。这里不引入"顺便优化"的行为差异，否则
M2 做 Java/Python 差分测试时会分不清是迁移错误还是设计变更。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError
from app.core.security import create_access_token, decode_jwt_secret, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.user_view import build_user_payload
from app.services.auth_session import (
    ROTATE_OK,
    AuthSessionStore,
    hash_secret,
    new_refresh_secret,
    new_session_id,
    parse_refresh_token,
)

logger = logging.getLogger("orange-diary-ai")

try:  # redis 属于运行依赖，但单测可以完全不碰它
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    class RedisError(Exception):  # type: ignore[no-redef]
        pass


class AuthService:
    def __init__(self, db: AsyncSession, store: AuthSessionStore, settings: Settings) -> None:
        self._db = db
        self._store = store
        self._settings = settings

    # ---------------- 注册 ----------------

    async def register(self, payload: RegisterRequest) -> dict[str, Any]:
        if payload.password != payload.confirmPassword:
            raise BusinessError(400, "两次输入的密码不一致")

        existing = await self._db.scalar(select(User).where(User.email == payload.email))
        if existing is not None:
            raise BusinessError(409, "邮箱已注册")

        user = User(
            email=payload.email,
            nickname=payload.username,
            password_hash=hash_password(payload.password),
            status="active",
            role="user",
        )
        self._db.add(user)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            # 并发注册时先查后插仍可能撞唯一索引，把 500 收敛成 409。
            await self._db.rollback()
            raise BusinessError(409, "邮箱已注册") from exc
        await self._db.refresh(user)
        return await self._user_payload(user)

    # ---------------- 登录 ----------------

    async def login(self, payload: LoginRequest) -> dict[str, Any]:
        try:
            account = payload.resolved_account()
        except ValueError as exc:
            raise BusinessError(400, "邮箱或手机号不能为空") from exc

        column = User.email if "@" in account else User.mobile
        user = await self._db.scalar(select(User).where(column == account))
        if user is None or not verify_password(payload.password, user.password_hash):
            raise BusinessError(401, "邮箱或手机号或密码错误")
        if user.status != "active":
            raise BusinessError(403, "当前账号已被禁用")

        return await self._create_session(user.id)

    # ---------------- 刷新 ----------------

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        session_id, secret = self._parse_or_401(refresh_token)
        state = await self._read_session(session_id)
        if state is None or state.status != "active":
            raise BusinessError(401, "登录凭证已失效，请重新登录")

        new_secret = new_refresh_secret()
        try:
            result = await self._store.rotate(
                session_id,
                hash_secret(secret),
                hash_secret(new_secret),
                self._settings.jwt_refresh_expiration_ms,
            )
        except RedisError as exc:
            logger.error("刷新会话时 Redis 不可用: %s", exc.__class__.__name__)
            raise BusinessError(503, "登录服务暂不可用") from exc

        if result != ROTATE_OK:
            # -1 表示旧 token 被重复使用，会话已被销毁；对外统一报"失效"，
            # 不告诉攻击者到底命中了哪条规则。
            raise BusinessError(401, "登录凭证已失效，请重新登录")

        user = await self._db.get(User, state.user_id)
        if user is None or user.status != "active":
            await self._store.delete_if_matches(session_id, hash_secret(new_secret))
            raise BusinessError(401, "账号不存在或已被禁用")

        return self._login_response(state.user_id, session_id, new_secret)

    # ---------------- 登出 ----------------

    async def logout(self, refresh_token: str) -> None:
        session_id, secret = self._parse_or_401(refresh_token)
        try:
            await self._store.delete_if_matches(session_id, hash_secret(secret))
        except RedisError as exc:
            logger.error("登出时 Redis 不可用: %s", exc.__class__.__name__)
            raise BusinessError(503, "登录服务暂不可用") from exc

    # ---------------- 组装 ----------------

    async def _create_session(self, user_id: int) -> dict[str, Any]:
        session_id = new_session_id()
        secret = new_refresh_secret()
        try:
            created = await self._store.create(
                session_id,
                user_id,
                hash_secret(secret),
                self._settings.jwt_refresh_expiration_ms,
            )
        except RedisError as exc:
            logger.error("创建登录会话时 Redis 不可用: %s", exc.__class__.__name__)
            raise BusinessError(503, "登录服务暂不可用") from exc
        if not created:
            raise BusinessError(503, "登录服务暂不可用")
        return self._login_response(user_id, session_id, secret)

    def _login_response(self, user_id: int, session_id: str, secret: str) -> dict[str, Any]:
        expires_seconds = self._settings.jwt_expiration_ms // 1000
        token = create_access_token(
            user_id,
            session_id,
            decode_jwt_secret(self._settings.jwt_secret),
            expires_seconds,
        )
        return {
            "token": token,
            "tokenType": "Bearer",
            "expiresIn": expires_seconds,
            "refreshToken": f"{session_id}.{secret}",
            "refreshExpiresIn": self._settings.jwt_refresh_expiration_ms // 1000,
        }

    async def _read_session(self, session_id: str):
        try:
            return await self._store.read(session_id)
        except RedisError as exc:
            logger.error("读取会话时 Redis 不可用: %s", exc.__class__.__name__)
            raise BusinessError(503, "登录服务暂不可用") from exc

    @staticmethod
    def _parse_or_401(refresh_token: str) -> tuple[str, str]:
        try:
            return parse_refresh_token(refresh_token)
        except ValueError as exc:
            raise BusinessError(401, "登录凭证已失效，请重新登录") from exc

    async def _user_payload(self, user: User) -> dict[str, Any]:
        # 保留这个方法名：注册/登录都走它，拼装逻辑本身放在 user_view，
        # 保证与 /api/users/me 输出同一份结构。
        return await build_user_payload(self._db, user)
