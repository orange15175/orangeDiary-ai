"""档案完整度闸门。

`ProfileCompletionServiceImpl.requireCompletedProfile` 的 428 是前端"先去完善资料"
引导的触发条件。少了它，用户可以记一堆没有体重和三围基础的饮食数据，
后面的营养建议与目标达成率全部算不准。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.user import UserGoal, UserProfile
from app.services.user_view import is_profile_completed

# 428 Precondition Required：Java 原文案是英文，保持逐字一致，前端按文案分支过。
MESSAGE = "Please complete gender, height, current weight, and goal before recording data"


async def require_completed_profile(db: AsyncSession, user_id: int) -> None:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    goal = await db.scalar(
        select(UserGoal)
        .where(UserGoal.user_id == user_id, UserGoal.status == "active")
        .order_by(UserGoal.id.desc())
        .limit(1)
    )
    if not is_profile_completed(profile, goal):
        raise BusinessError(428, MESSAGE)
