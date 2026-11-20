"""每日汇总读取（对齐 `DailySummaryServiceImpl`）。

这张表可能没有当天的行（用户只看不记），此时要返回一份"全零 + 目标已算"的
虚拟汇总，而不是 404。前端的环形进度条依赖它渲染"今天还能吃多少"。

目标热量（targetDailyCalorie）优先级：生效目标的 `daily_intake_target` →
Mifflin-St Jeor 估算（性别/年龄/身高/体重/活动水平 + 目标类型修正）。
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import DailySummary
from app.models.user import UserGoal, UserProfile

_CENT = Decimal("0.01")
_ACTIVITY_FACTORS = {"low": Decimal("1.20"), "high": Decimal("1.45")}
_DEFAULT_ACTIVITY = Decimal("1.325")
# 减重 -400 kcal、增重 +225 kcal：Java 的固定修正，不做"按周目标换算"。
_ADJUSTMENTS = {"lose": Decimal("-400"), "gain": Decimal("225")}


def summary_row(summary: DailySummary, target: Decimal) -> dict[str, Any]:
    intake = summary.total_intake_calorie or Decimal("0")
    # Java 用 max(0) 兜住负数：吃超了 available 显示 0，而不是负数。
    available = max(target - intake, Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)
    over_budget = (
        max(intake - target, Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)
        if target > 0
        else None
    )
    return {
        "id": summary.id,
        "userId": summary.user_id,
        "summaryDate": summary.summary_date.isoformat() if summary.summary_date else None,
        "totalIntakeCalorie": summary.total_intake_calorie,
        "totalBurnCalorie": summary.total_burn_calorie,
        "netCalorie": summary.net_calorie,
        "dietCount": summary.diet_count,
        "exerciseCount": summary.exercise_count,
        "weightKg": summary.weight_kg,
        "goalIntakeTarget": summary.goal_intake_target,
        "goalBurnTarget": summary.goal_burn_target,
        "targetDailyCalorie": target,
        "availableIntakeCalorie": available,
        "overBudgetCalorie": over_budget,
        "createdAt": summary.created_at.isoformat() if summary.created_at else None,
        "updatedAt": summary.updated_at.isoformat() if summary.updated_at else None,
    }


class SummaryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_summary(self, user_id: int, day: date) -> dict[str, Any]:
        summary = await self._db.scalar(
            select(DailySummary).where(
                DailySummary.user_id == user_id, DailySummary.summary_date == day
            )
        )
        if summary is None:
            summary = DailySummary(
                user_id=user_id,
                summary_date=day,
                total_intake_calorie=Decimal("0"),
                total_burn_calorie=Decimal("0"),
                net_calorie=Decimal("0"),
                diet_count=0,
                exercise_count=0,
            )
        target = await self._daily_target(user_id, day)
        return summary_row(summary, target)

    async def _daily_target(self, user_id: int, day: date) -> Decimal:
        return await daily_target(self._db, user_id, day)


async def daily_target(db: AsyncSession, user_id: int, day: date) -> Decimal:
    """每日目标热量。规划与汇总共用，避免两套口径。

    优先级：生效目标的 `daily_intake_target` → Mifflin-St Jeor 估算
    （性别/年龄/身高/体重/活动水平 + 目标类型修正）。资料不足返回 0，
    调用方据此提示补充档案，而不是编一个数字。
    """
    goal = await db.scalar(
        select(UserGoal)
        .where(UserGoal.user_id == user_id, UserGoal.status == "active")
        .order_by(UserGoal.id.desc())
        .limit(1)
    )
    if goal is not None and goal.daily_intake_target is not None:
        return goal.daily_intake_target.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None or profile.height_cm is None or profile.current_weight_kg is None:
        return Decimal("0")

    age = 30
    if profile.birthday is not None:
        # Java 用 Period.between(birthday, date).getYears()，并且下限 15 岁。
        age = max(15, day.year - profile.birthday.year - ((day.month, day.day) < (profile.birthday.month, profile.birthday.day)))
    bmr = (
        Decimal("10") * profile.current_weight_kg
        + Decimal("6.25") * profile.height_cm
        - Decimal("5") * age
        + (Decimal("-161") if profile.gender == "female" else Decimal("5"))
    )
    factor = _ACTIVITY_FACTORS.get(profile.activity_level or "medium", _DEFAULT_ACTIVITY)
    adjustment = _ADJUSTMENTS.get(goal.goal_type if goal is not None else "maintain", Decimal("0"))
    return (bmr * factor + adjustment).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
