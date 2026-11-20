"""认证接口：路径与 Java `AuthController` 一一对应，前端不需要改 baseURL。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import success
from app.db.session import get_session
from app.schemas.auth import LoginRequest, RefreshTokenRequest, RegisterRequest
from app.services.auth_service import AuthService
from app.services.auth_session import get_redis_session_store

router = APIRouter(prefix="/api/auth", tags=["认证"])


def get_auth_service(db: AsyncSession = Depends(get_session)) -> AuthService:
    settings = Settings.from_env()
    return AuthService(db, get_redis_session_store(settings), settings)


@router.post("/register", summary="用户注册")
async def register(payload: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    return success(await service.register(payload))


@router.post("/login", summary="用户登录")
async def login(payload: LoginRequest, service: AuthService = Depends(get_auth_service)):
    return success(await service.login(payload))


@router.post("/refresh", summary="刷新登录状态")
async def refresh(payload: RefreshTokenRequest, service: AuthService = Depends(get_auth_service)):
    return success(await service.refresh(payload.refreshToken))


@router.post("/logout", summary="退出登录")
async def logout(payload: RefreshTokenRequest, service: AuthService = Depends(get_auth_service)):
    await service.logout(payload.refreshToken)
    return success(None)
