"""用户中心：当前用户、资料修改、目标设置、活跃用户数。

对齐 `UserServiceImpl`。两处容易做错的地方：

1. `updateProfile` 对 `user` 表是部分更新、对 `user_profile` 是整体覆盖。
   这里用 `payload.nickname is not None` 判断是否写 user 表，profile 则无条件
   六个字段全写——和 MyBatis-Plus 的实际行为一致。
2. `createGoal` 先把"所有生效目标"置为 cancelled 再插入新目标，
   不是"插入后取最新一条"。下游按 status='active' 查目标，两种做法结果不同。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.avatar import AvatarUpload
from app.models.user import User, UserGoal, UserProfile
from app.schemas.user import CreateUserGoalRequest, UpdateUserProfileRequest
from app.services.user_view import build_user_payload

# 与 `AvatarService.PREFIX` 同值，前端拿到的就是这种相对路径。
AVATAR_PREFIX = "/api/avatars/"
# 未保存的头像允许存活一天；超过一天的上传记录视为垃圾，不能再用作头像。
AVATAR_TTL = timedelta(days=1)


class UserService:
    def __init__(self, db: AsyncSession, *, storage: Any = None) -> None:
        self._db = db
        # 头像校验最后一步要确认对象真实存在（Java 会 statObject）。不传 storage
        # 时跳过——单测不想为了校验一个 URL 去连 MinIO。
        self._storage = storage

    async def count_active_users(self) -> int:
        # 禁用账号不计入对外展示的用户数。
        total = await self._db.scalar(
            select(func.count()).select_from(User).where(User.status == "active")
        )
        return int(total or 0)

    async def current_user_payload(self, user: User) -> dict[str, Any]:
        return await build_user_payload(self._db, user)

    async def update_profile(self, user: User, payload: UpdateUserProfileRequest) -> dict[str, Any]:
        # 头像只在新值与旧值不同时校验：老用户带着已过期的历史头像提交其他字段时，
        # Java 也是直接放行（url.equals(previous) 提前返回）。
        if payload.avatar is not None and payload.avatar != user.avatar:
            await self._validate_avatar(user.id, payload.avatar)

        if payload.nickname is not None:
            user.nickname = payload.nickname
        if payload.avatar is not None:
            user.avatar = payload.avatar

        profile = await self._db.scalar(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
        if profile is None:
            profile = UserProfile(user_id=user.id)
            self._db.add(profile)
        # 整体覆盖，与 Java 无条件 set 六个字段一致；不传即清空。
        profile.gender = payload.gender
        profile.birthday = payload.birthday
        profile.height_cm = payload.heightCm
        profile.current_weight_kg = payload.currentWeightKg
        profile.target_weight_kg = payload.targetWeightKg
        profile.activity_level = payload.activityLevel

        await self._db.commit()
        return await build_user_payload(self._db, user)

    async def create_goal(self, user: User, payload: CreateUserGoalRequest) -> dict[str, Any]:
        if payload.endDate is not None and payload.endDate < payload.startDate:
            raise BusinessError(400, "目标结束日期不能早于开始日期")

        await self._db.execute(
            update(UserGoal)
            .where(UserGoal.user_id == user.id, UserGoal.status == "active")
            .values(status="cancelled")
        )
        self._db.add(
            UserGoal(
                user_id=user.id,
                goal_type=payload.goalType,
                daily_intake_target=payload.dailyIntakeTarget,
                daily_burn_target=payload.dailyBurnTarget,
                start_date=payload.startDate,
                end_date=payload.endDate,
                status="active",
            )
        )
        await self._db.commit()
        return await build_user_payload(self._db, user)

    async def _validate_avatar(self, user_id: int, url: str) -> None:
        """复刻 `AvatarService.validate`。

        四道判定缺一不可：前缀对不对、登记行在不在、是不是本人的、上传是否
        还在一天之内。最后再 stat 一次对象存储，确认图片真的存在——登记行
        在但对象被清掉时，用户会得到一个永久 404 的头像。
        """
        if not url.startswith(AVATAR_PREFIX):
            raise BusinessError(400, "请通过头像上传入口选择图片")
        key = url[len(AVATAR_PREFIX):]
        asset = await self._db.get(AvatarUpload, key)
        if (
            asset is None
            or asset.user_id != user_id
            or not key.startswith(f"avatars/{user_id}/")
            or asset.created_at < datetime.now() - AVATAR_TTL
        ):
            raise BusinessError(400, "头像图片无效")
        if self._storage is not None:
            await self._storage.content_type_of(key)
