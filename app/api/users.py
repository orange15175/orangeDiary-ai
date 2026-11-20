"""用户中心接口，路径与 `UserController` 一一对应。

`/api/users/count` 也要求登录：Java 的拦截器只放行 `/api/auth/**`，
如果这里做成公开接口，就成了"Python 版泄漏的匿名查询面"。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.schemas.user import CreateUserGoalRequest, UpdateUserProfileRequest
from app.services.image_storage import get_image_storage
from app.services.user_service import UserService

router = APIRouter(prefix="/api/users", tags=["用户中心"])


def get_user_service(
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserService:
    return UserService(db, storage=get_image_storage(settings))


@router.get("/count", summary="获取项目活跃用户数")
async def active_user_count(
    _user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    return success({"totalUsers": await service.count_active_users()})


@router.get("/me", summary="获取当前用户")
async def current_user(
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    return success(await service.current_user_payload(user))


@router.get("/validate", summary="校验当前登录状态")
async def validate_current_user(_user: User = Depends(get_current_user)):
    # 能走到这里说明 JWT、Redis 会话、账号状态三道校验都过了。
    return success({"authenticated": True})


@router.put("/profile", summary="修改个人资料")
async def update_profile(
    payload: UpdateUserProfileRequest,
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    return success(await service.update_profile(user, payload))


@router.post("/goals", summary="设置目标")
async def create_goal(
    payload: CreateUserGoalRequest,
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    return success(await service.create_goal(user, payload))
