"""每日汇总写入器（对齐 `HealthSummaryWriter`）。

两条规则决定了并发正确性，不能省：

1. **先锁 user 行再写子表**。反过来的话，`diet_record.user_id` 的外键校验会先给
   user 行加共享锁，两个并发事务随后都想升级成排他锁，MySQL 直接报死锁
   （Java 侧压测时 20 并发写错误率 69%）。所以每个健康写操作开头都要 `lock_user`。
2. **汇总整行重算，显式写 NULL**。MyBatis 的 updateById 会跳过 null 字段，
   Java 为此专门写了 `LambdaUpdateWrapper.set(...)`；SQLAlchemy 正常赋值即可，
   但要注意"当天删掉唯一一条体重记录"时 weight_kg 必须被置空。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.diet import DietRecord
from app.models.health import DailySummary, ExerciseRecord, WeightRecord
from app.models.user import User


async def lock_user(db: AsyncSession, user_id: int) -> None:
    """`SELECT id ... FOR UPDATE`；用户不存在直接 401，不留悬空外键。"""
    locked = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if locked is None:
        raise BusinessError(401, "用户不存在")


async def refresh(db: AsyncSession, user_id: int, day: date) -> None:
    intake_total, diet_count = (
        await db.execute(
            select(func.coalesce(func.sum(DietRecord.calorie_snapshot), 0), func.count())
            .select_from(DietRecord)
            .where(DietRecord.user_id == user_id, DietRecord.record_date == day)
        )
    ).one()
    burn_total, exercise_count = (
        await db.execute(
            select(func.coalesce(func.sum(ExerciseRecord.calorie_burned), 0), func.count())
            .select_from(ExerciseRecord)
            .where(ExerciseRecord.user_id == user_id, ExerciseRecord.record_date == day)
        )
    ).one()

    latest_weight = await db.scalar(
        select(WeightRecord)
        .where(WeightRecord.user_id == user_id, WeightRecord.record_date == day)
        .order_by(WeightRecord.record_time.desc(), WeightRecord.id.desc())
        .limit(1)
    )

    intake = Decimal(str(intake_total or 0))
    burn = Decimal(str(burn_total or 0))
    weight_kg = latest_weight.weight_kg if latest_weight is not None else None

    summary = await db.scalar(
        select(DailySummary).where(
            DailySummary.user_id == user_id, DailySummary.summary_date == day
        )
    )
    if summary is None:
        db.add(
            DailySummary(
                user_id=user_id,
                summary_date=day,
                total_intake_calorie=intake,
                total_burn_calorie=burn,
                net_calorie=intake - burn,
                diet_count=int(diet_count),
                exercise_count=int(exercise_count),
                weight_kg=weight_kg,
            )
        )
    else:
        summary.total_intake_calorie = intake
        summary.total_burn_calorie = burn
        summary.net_calorie = intake - burn
        summary.diet_count = int(diet_count)
        summary.exercise_count = int(exercise_count)
        # 显式赋值 None：当天体重记录被删后，汇总里的旧体重不能残留。
        summary.weight_kg = weight_kg
    await db.flush()
