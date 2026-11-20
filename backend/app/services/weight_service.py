"""体重记录与趋势（对齐 `WeightRecordServiceImpl`）。

`user_profile.current_weight_kg` 是"最新测量体重"的冗余字段：新增体重会覆盖它，
删除体重会按剩余记录重算（没有记录则置空）。它影响 BMR 与运动热量估算，
所以必须跟着记录走，不能只在设置资料时写一次。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.health import WeightRecord
from app.models.user import UserProfile
from app.schemas.weight import CreateWeightRecordRequest
from app.services import health_summary
from app.services.profile_gate import require_completed_profile

_CENT = Decimal("0.01")
_MAX_TREND_DAYS = 90


def weight_row(record: WeightRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "userId": record.user_id,
        "weightKg": record.weight_kg,
        "bodyFatRate": record.body_fat_rate,
        "bmi": record.bmi,
        "recordDate": record.record_date.isoformat() if record.record_date else None,
        "recordTime": record.record_time.isoformat() if record.record_time else None,
        "remark": record.remark,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
        "updatedAt": record.updated_at.isoformat() if record.updated_at else None,
    }


class WeightService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_record(
        self, user_id: int, payload: CreateWeightRecordRequest
    ) -> dict[str, Any]:
        await health_summary.lock_user(self._db, user_id)
        await require_completed_profile(self._db, user_id)
        profile = await self._profile(user_id)

        record = WeightRecord(
            user_id=user_id,
            weight_kg=payload.weightKg,
            body_fat_rate=payload.bodyFatRate,
            bmi=_bmi(payload.weightKg, profile.height_cm if profile else None),
            record_time=payload.recordTime,
            record_date=payload.recordTime.date(),
            remark=payload.remark,
        )
        self._db.add(record)
        await self._db.flush()
        await self._db.refresh(record)

        await self._sync_profile_weight(user_id, profile)
        await health_summary.refresh(self._db, user_id, record.record_date)
        return weight_row(record)

    async def list_records(self, user_id: int, day: date) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(WeightRecord)
                .where(WeightRecord.user_id == user_id, WeightRecord.record_date == day)
                .order_by(WeightRecord.record_time.desc(), WeightRecord.id.desc())
            )
        ).all()
        return [weight_row(record) for record in rows]

    async def list_trend(self, user_id: int, start_date: date, end_date: date) -> list[dict[str, Any]]:
        # 与 Java 同文案：前端对这两句有 toast 兜底。
        if start_date > end_date:
            raise BusinessError(400, "Start date must not be after end date")
        # Java 是 `startDate.plusDays(89).isBefore(endDate)`：含首尾最多 90 天。
        if start_date + timedelta(days=_MAX_TREND_DAYS - 1) < end_date:
            raise BusinessError(400, "Weight trend supports at most 90 days")

        rows = (
            await self._db.scalars(
                select(WeightRecord)
                .where(
                    WeightRecord.user_id == user_id,
                    WeightRecord.record_date >= start_date,
                    WeightRecord.record_date <= end_date,
                )
                .order_by(
                    WeightRecord.record_date.asc(),
                    WeightRecord.record_time.asc(),
                    WeightRecord.id.asc(),
                )
            )
        ).all()
        # 同一天只保留最后一次测量；正序遍历、后者覆盖前者，与 Java 的 LinkedHashMap 一致。
        latest: dict[date, WeightRecord] = {}
        for record in rows:
            latest[record.record_date] = record
        return [
            {"recordDate": day.isoformat(), "weightKg": record.weight_kg}
            for day, record in latest.items()
        ]

    async def get_progress(self, user_id: int) -> dict[str, Any]:
        profile = await self._profile(user_id)
        initial = await self._db.scalar(
            select(WeightRecord)
            .where(WeightRecord.user_id == user_id)
            .order_by(WeightRecord.record_time.asc(), WeightRecord.id.asc())
            .limit(1)
        )
        latest = await self._db.scalar(
            select(WeightRecord)
            .where(WeightRecord.user_id == user_id)
            .order_by(WeightRecord.record_time.desc(), WeightRecord.id.desc())
            .limit(1)
        )
        profile_weight = (
            profile.current_weight_kg
            if profile is not None and profile.current_weight_kg is not None
            else Decimal("0")
        )
        target_weight = (
            profile.target_weight_kg
            if profile is not None and profile.target_weight_kg is not None
            else Decimal("0")
        )
        lost = Decimal("0")
        if initial is not None and latest is not None:
            lost = max(initial.weight_kg - latest.weight_kg, Decimal("0")).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
        return {
            "initialWeightKg": initial.weight_kg if initial is not None else profile_weight,
            "latestWeightKg": latest.weight_kg if latest is not None else profile_weight,
            "targetWeightKg": target_weight,
            "lostWeightKg": lost,
        }

    async def delete_record(self, user_id: int, record_id: int) -> None:
        await health_summary.lock_user(self._db, user_id)
        record = await self._db.scalar(
            select(WeightRecord).where(
                WeightRecord.id == record_id, WeightRecord.user_id == user_id
            )
        )
        if record is None:
            raise BusinessError(404, "Weight record not found")
        day = record.record_date
        await self._db.delete(record)
        await self._db.flush()

        profile = await self._profile(user_id)
        if profile is not None:
            await self._sync_profile_weight(user_id, profile)
        await health_summary.refresh(self._db, user_id, day)

    # ---------------- 内部 ----------------

    async def _profile(self, user_id: int) -> UserProfile | None:
        return await self._db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    async def _sync_profile_weight(self, user_id: int, profile: UserProfile | None) -> None:
        if profile is None:
            return
        latest = await self._db.scalar(
            select(WeightRecord)
            .where(WeightRecord.user_id == user_id)
            .order_by(WeightRecord.record_time.desc(), WeightRecord.id.desc())
            .limit(1)
        )
        profile.current_weight_kg = latest.weight_kg if latest is not None else None
        await self._db.flush()


def _bmi(weight_kg: Decimal, height_cm: Decimal | None) -> Decimal | None:
    """BMI = kg / m²；身高缺失时返回 null（不能拿 0 去除）。"""
    if height_cm is None or height_cm <= 0:
        return None
    height_m = (height_cm / Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return (weight_kg / (height_m * height_m)).quantize(_CENT, rounding=ROUND_HALF_UP)
