"""实际饮食观察与计划动态调整接口（M6）。

观察不创建虚构的营养值：`estimatedNutrition` 为空就是空，前端显示"未估算"，
不填 0。调整只生成新的 draft 版本，采纳入口仍在 `/meal-plans/{id}/adopt`。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.review import AdjustmentRequest, ObservationRequest, ResolveRequest
from app.services.adjustment_service import AdjustmentService

router = APIRouter(prefix="/api/meal-observations", tags=["实际饮食观察"])
plan_router = APIRouter(prefix="/api/meal-plans", tags=["计划调整"])


@router.post("", summary="记录一次实际饮食（可精确度不足）")
async def create_observation(
    payload: ObservationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    data = payload.model_dump()
    return success(
        await AdjustmentService(db).create_observation(
            user.id,
            occurred_at=data["occurredAt"],
            description=data["description"],
            source=data["source"],
            estimated_nutrition=data["estimatedNutrition"],
            completeness=data["completeness"],
        )
    )


@router.get("", summary="查询实际饮食记录")
async def list_observations(
    startDate: date | None = Query(default=None),
    endDate: date | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await AdjustmentService(db).list_observations(user.id, startDate, endDate))


@router.post("/{observation_id}/resolve", summary="关联到正式饮食记录")
async def resolve_observation(
    observation_id: int,
    payload: ResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(
        await AdjustmentService(db).resolve(user.id, observation_id, payload.dietRecordIds)
    )


@router.post("/{observation_id}/retract", summary="撤回一条观察")
async def retract_observation(
    observation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await AdjustmentService(db).retract(user.id, observation_id))


@plan_router.post("/{plan_id}/adjustments", summary="按实际情况生成调整草稿")
async def create_adjustment(
    plan_id: int,
    payload: AdjustmentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(
        await AdjustmentService(db).create_adjustment(
            user.id, plan_id, event=payload.event, note=payload.note, max_minutes=payload.maxPrepMinutes, excluded_foods=payload.excludedFoods
        )
    )
