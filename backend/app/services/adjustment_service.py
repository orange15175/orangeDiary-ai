"""实际饮食观察与计划动态调整。

观察（meal_observation）用于"吃了但没法精确记"的场景：聚餐、外卖、一碗面。
它不创建虚构的 food_item/克重，而是保存事件与可信程度；确认匹配后才关联正式记录，
汇总时不能与已关联记录重复计入（设计 §9.1）。

调整本身由 `PlanService.revise` 生成新 draft 版本：已执行/锁定餐次不动，
其余重新选餐，采纳前旧计划保持生效（设计 §9.2）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.core.jsonable import to_jsonable
from app.models.diet import DietRecord
from app.models.diet_assistant import MealObservation, ObservationRecordLink
from app.services.plan_service import PlanService

_STATUSES = {"draft", "confirmed_unmatched", "resolved", "retracted"}


class AdjustmentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_observation(
        self,
        user_id: int,
        *,
        occurred_at: datetime,
        description: str,
        source: str,
        estimated_nutrition: dict | None,
        completeness: str,
    ) -> dict[str, Any]:
        if not description.strip():
            raise BusinessError(400, "描述不能为空")
        row = MealObservation(
            user_id=user_id,
            occurred_at=occurred_at,
            description=description.strip(),
            source=source or "manual",
            estimated_nutrition=to_jsonable(estimated_nutrition),
            completeness=completeness if completeness in {"partial", "complete", "unknown"} else "partial",
            status="draft",
        )
        self._db.add(row)
        await self._db.commit()
        return self._serialize(row)

    async def list_observations(
        self, user_id: int, start: date | None, end: date | None
    ) -> list[dict[str, Any]]:
        stmt = select(MealObservation).where(MealObservation.user_id == user_id)
        if start:
            stmt = stmt.where(MealObservation.occurred_at >= datetime.combine(start, datetime.min.time()))
        if end:
            stmt = stmt.where(MealObservation.occurred_at <= datetime.combine(end, datetime.max.time()))
        rows = list(await self._db.scalars(stmt.order_by(MealObservation.occurred_at.desc()).limit(100)))
        return [self._serialize(row) for row in rows]

    async def resolve(
        self, user_id: int, observation_id: int, diet_record_ids: list[int]
    ) -> dict[str, Any]:
        row = await self._owned(user_id, observation_id)
        if row.status == "retracted":
            raise BusinessError(409, "该记录已撤回")
        if not diet_record_ids:
            raise BusinessError(400, "至少关联一条正式记录")
        record_ids = set(diet_record_ids)
        owned = set(await self._db.scalars(select(DietRecord.id).where(DietRecord.id.in_(record_ids), DietRecord.user_id == user_id)))
        if owned != record_ids:
            raise BusinessError(404, "关联的饮食记录不存在或不属于当前账号")
        existing = {
            link.diet_record_id
            for link in await self._db.scalars(
                select(ObservationRecordLink).where(ObservationRecordLink.observation_id == observation_id)
            )
        }
        for record_id in diet_record_ids:
            if record_id in existing:
                continue
            self._db.add(
                ObservationRecordLink(observation_id=observation_id, diet_record_id=record_id)
            )
        row.status = "resolved"
        await self._db.commit()
        return self._serialize(row)

    async def retract(self, user_id: int, observation_id: int) -> dict[str, Any]:
        row = await self._owned(user_id, observation_id)
        row.status = "retracted"
        await self._db.commit()
        return self._serialize(row)

    async def create_adjustment(
        self, user_id: int, plan_id: int, *, event: str | None, note: str, max_minutes: int | None = None, excluded_foods: list[str] | None = None
    ) -> dict[str, Any]:
        """生成调整草稿；不直接改生效计划。"""
        if not note.strip():
            raise BusinessError(400, "请说明调整原因")
        return await PlanService(self._db).revise(user_id, plan_id, note.strip(), event, max_minutes=max_minutes, excluded_foods=excluded_foods)

    async def _owned(self, user_id: int, observation_id: int) -> MealObservation:
        row = await self._db.get(MealObservation, observation_id)
        if row is None or row.user_id != user_id:
            raise BusinessError(404, "记录不存在")
        return row

    @staticmethod
    def _serialize(row: MealObservation) -> dict[str, Any]:
        return {
            "id": row.id,
            "occurredAt": row.occurred_at.isoformat() if row.occurred_at else None,
            "description": row.description,
            "source": row.source,
            "estimatedNutrition": row.estimated_nutrition,
            "completeness": row.completeness,
            "status": row.status,
        }
