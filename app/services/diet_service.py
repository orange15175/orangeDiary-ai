"""饮食记录服务（对齐 `DietRecordServiceImpl`）。

三个不可省的细节：

1. **快照在写入时算好**。`calorie_snapshot` 等字段是本次摄入的绝对值，
   后续食物库改营养值不影响历史——用食物库现值回算是错的。
2. **ratio 精度**。Java 用 `divide(100, 4, HALF_UP)` 再乘，即 150g → 1.5000；
   若直接 `* quantity / 100` 用高精度 Decimal，某些边界值会差 0.01。
3. **record_date 取 record_time 的日期**，不是服务器当前日期。补记昨天的
   午餐时，汇总必须落在昨天。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.diet import DietRecord
from app.models.food import FoodItem
from app.schemas.diet import CreateDietRecordRequest
from app.services import health_summary
from app.services.profile_gate import require_completed_profile

_ONE_HUNDRED = Decimal("100")
_RATIO_SCALE = Decimal("0.0001")
_PERCENT = Decimal("0.01")


def diet_row(record: DietRecord, food: FoodItem | None = None) -> dict[str, Any]:
    """序列化成 Java `DietRecord` 实体的 JSON 形态（含两个非表字段）。

    时间统一输出字符串：幂等重放直接存这份 JSON，若这里给 datetime 对象，
    首次响应与重放响应的格式会不一致（JSON 里是 "T"，str() 是空格）。
    """
    return {
        "id": record.id,
        "userId": record.user_id,
        "foodItemId": record.food_item_id,
        "mealType": record.meal_type,
        "foodNameSnapshot": record.food_name_snapshot,
        "foodImageUrl": food.cover_image_url if food is not None else None,
        "foodCategory": food.category if food is not None else None,
        "quantityG": record.quantity_g,
        "calorieSnapshot": record.calorie_snapshot,
        "proteinSnapshot": record.protein_snapshot,
        "fatSnapshot": record.fat_snapshot,
        "carbSnapshot": record.carb_snapshot,
        "recordTime": record.record_time.isoformat() if record.record_time else None,
        "recordDate": record.record_date.isoformat() if record.record_date else None,
        "remark": record.remark,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
        "updatedAt": record.updated_at.isoformat() if record.updated_at else None,
    }


class DietService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_record(self, user_id: int, payload: CreateDietRecordRequest) -> dict[str, Any]:
        # 顺序与 Java 一致：先锁用户行，再校验档案、查食物。
        await health_summary.lock_user(self._db, user_id)
        await require_completed_profile(self._db, user_id)

        food = await self._db.get(FoodItem, payload.foodItemId)
        if food is None or food.status != "active":
            raise BusinessError(404, "食物不存在或已停用")

        quantity = payload.quantityG
        ratio = (quantity / _ONE_HUNDRED).quantize(_RATIO_SCALE, rounding=ROUND_HALF_UP)
        record = DietRecord(
            user_id=user_id,
            food_item_id=food.id,
            meal_type=payload.mealType,
            food_name_snapshot=food.name,
            quantity_g=quantity,
            calorie_snapshot=_scaled(food.calorie_per_100g, ratio),
            protein_snapshot=_scaled(food.protein_per_100g, ratio),
            fat_snapshot=_scaled(food.fat_per_100g, ratio),
            carb_snapshot=_scaled(food.carb_per_100g, ratio),
            record_time=payload.recordTime,
            record_date=payload.recordTime.date(),
            remark=payload.remark,
        )
        self._db.add(record)
        await self._db.flush()
        # 读回 created_at（服务端默认值）后再刷新汇总，保证响应里的时间字段可信。
        await self._db.refresh(record)
        await health_summary.refresh(self._db, user_id, record.record_date)
        return diet_row(record, food)

    async def list_records(self, user_id: int, day: date) -> list[dict[str, Any]]:
        records = (
            await self._db.scalars(
                select(DietRecord)
                .where(DietRecord.user_id == user_id, DietRecord.record_date == day)
                .order_by(DietRecord.record_time.desc(), DietRecord.id.desc())
            )
        ).all()
        foods = await self._foods_by_id({record.food_item_id for record in records})
        return [diet_row(record, foods.get(record.food_item_id)) for record in records]

    async def delete_record(self, user_id: int, record_id: int) -> None:
        await health_summary.lock_user(self._db, user_id)
        record = await self._db.scalar(
            select(DietRecord).where(
                DietRecord.id == record_id, DietRecord.user_id == user_id
            )
        )
        if record is None:
            # Java 这里返回英文文案，保持原样：前端按 status 404 处理，不解析文案。
            raise BusinessError(404, "Diet record not found")
        record_date = record.record_date
        await self._db.delete(record)
        await self._db.flush()
        await health_summary.refresh(self._db, user_id, record_date)

    async def _foods_by_id(self, food_ids: set[int]) -> dict[int, FoodItem]:
        if not food_ids:
            return {}
        rows = (
            await self._db.scalars(select(FoodItem).where(FoodItem.id.in_(food_ids)))
        ).all()
        return {food.id: food for food in rows}


def _scaled(value_per_100g: Decimal | None, ratio: Decimal) -> Decimal | None:
    if value_per_100g is None:
        return None
    return (value_per_100g * ratio).quantize(_PERCENT, rounding=ROUND_HALF_UP)
