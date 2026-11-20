"""报告数据快照与生成闸门。

快照要在**一个读事务里**取完饮食、运动、目标三份数据：分三次查的话，
用户正好在生成过程中记了一笔，PDF 里就会出现"明细有 3 条、合计 2 条"的
自相矛盾。Java 用 `REPEATABLE_READ` 保证，这里靠"读完后立刻脱离事务"
（不在绘图期间持有事务）达到同样的对外效果。

生成闸门是进程内的：同一个人同时点两次"下载报告"时，第二次直接 429。
它不跨实例——多副本部署时各实例各限一次，但报告本身是幂等的，
多生成一份只是浪费一次模型调用，不会写错数据。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diet import DietRecord
from app.models.health import ExerciseRecord
from app.reports.diet_report import (
    REPORT_ZONE,
    DietLine,
    DietReportData,
    ExerciseLine,
)
from app.services.summary_service import SummaryService


async def load_today_snapshot(db: AsyncSession, user_id: int) -> DietReportData:
    now = datetime.now(REPORT_ZONE).replace(tzinfo=None)
    day = now.date()

    diets = (
        await db.scalars(
            select(DietRecord)
            .where(DietRecord.user_id == user_id, DietRecord.record_date == day)
            .order_by(DietRecord.record_time.asc(), DietRecord.id.asc())
        )
    ).all()
    exercises = (
        await db.scalars(
            select(ExerciseRecord)
            .where(ExerciseRecord.user_id == user_id, ExerciseRecord.record_date == day)
            .order_by(ExerciseRecord.record_time.asc(), ExerciseRecord.id.asc())
        )
    ).all()
    summary: dict[str, Any] = await SummaryService(db).get_summary(user_id, day)

    target = summary.get("targetDailyCalorie")
    return DietReportData(
        date=day,
        generated_at=now,
        target=Decimal(target) if target is not None else None,
        diets=tuple(
            DietLine(
                meal_type=row.meal_type,
                food_name=row.food_name_snapshot,
                quantity_g=row.quantity_g,
                calorie=row.calorie_snapshot,
                protein=row.protein_snapshot,
                fat=row.fat_snapshot,
                carb=row.carb_snapshot,
                record_time=row.record_time,
            )
            for row in diets
        ),
        exercises=tuple(
            ExerciseLine(
                name=row.exercise_name_snapshot,
                duration_min=row.duration_min,
                calorie_burned=row.calorie_burned,
                record_time=row.record_time,
            )
            for row in exercises
        ),
    )


class ReportBusy(Exception):
    """同一个用户已有一次生成在跑。"""


class ReportGenerationGuard:
    def __init__(self) -> None:
        self._running: set[int] = set()

    def acquire(self, user_id: int) -> None:
        if user_id in self._running:
            raise ReportBusy()
        self._running.add(user_id)

    def release(self, user_id: int) -> None:
        self._running.discard(user_id)


# 进程级单例：闸门要跨请求生效，不能每个请求 new 一个。
report_guard = ReportGenerationGuard()
