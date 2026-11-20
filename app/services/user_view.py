"""`UserResponse` 的唯一组装点。

注册/登录的响应和 `/api/users/me` 必须是同一份结构，否则前端在"刚登录"和
"刷新页面"两个时刻看到的字段会不一样。之前这类拼装散在 AuthService 里，
迁移用户域时很容易复制成两份然后慢慢漂移。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserGoal, UserProfile


async def build_user_payload(db: AsyncSession, user: User) -> dict[str, Any]:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    goal = await db.scalar(
        select(UserGoal)
        .where(UserGoal.user_id == user.id, UserGoal.status == "active")
        .order_by(UserGoal.id.desc())
        .limit(1)
    )
    return {
        "id": user.id,
        "mobile": user.mobile,
        "email": user.email,
        "nickname": user.nickname,
        "avatar": user.avatar,
        "status": user.status,
        "role": user.role,
        "profileCompleted": is_profile_completed(profile, goal),
        "profile": profile_payload(profile),
        "activeGoal": goal_payload(goal),
    }


def is_profile_completed(profile: UserProfile | None, goal: UserGoal | None) -> bool:
    """与 `ProfileCompletionServiceImpl` 同一口径：性别、身高、当前体重齐全且有生效目标。

    注意"有目标"是硬条件：只填了身体数据但没设置减重/维持/增重目标的用户，
    在 Java 侧记饮食会被 428 拦住，这里必须一模一样。
    """
    return bool(
        profile
        and profile.gender
        and profile.height_cm is not None
        and profile.current_weight_kg is not None
        and goal is not None
    )


def profile_payload(profile: UserProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "gender": profile.gender,
        "birthday": _iso(profile.birthday),
        "heightCm": profile.height_cm,
        "currentWeightKg": profile.current_weight_kg,
        "targetWeightKg": profile.target_weight_kg,
        "activityLevel": profile.activity_level,
    }


def goal_payload(goal: UserGoal | None) -> dict[str, Any] | None:
    if goal is None:
        return None
    return {
        "goalType": goal.goal_type,
        "dailyIntakeTarget": goal.daily_intake_target,
        "dailyBurnTarget": goal.daily_burn_target,
        "startDate": _iso(goal.start_date),
        "endDate": _iso(goal.end_date),
        "status": goal.status,
    }


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
