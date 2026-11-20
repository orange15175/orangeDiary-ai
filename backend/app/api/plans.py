"""周计划与饮食偏好接口。

计划分两步：`POST /drafts` 只生成草稿，`POST /{id}/adopt` 才生效。
这样前端可以先展示营养与费用估算，用户确认后再落生效版本。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.plan import AdoptRequest, ExecutionRequest, PlanRequest, PreferenceRequest
from app.services.plan_service import PlanService

router = APIRouter(prefix="/api/meal-plans", tags=["周计划"])
meal_router = APIRouter(prefix="/api/plan-meals", tags=["周计划"])
preference_router = APIRouter(prefix="/api/diet-preferences", tags=["饮食偏好"])


@router.post("/drafts", summary="生成计划草稿（不生效）")
async def create_draft(
    payload: PlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).generate_draft(user, payload.model_dump()))


@router.get("", summary="查询生效计划")
async def list_plans(
    startDate: date | None = Query(default=None),
    endDate: date | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).list_plans(user.id, startDate, endDate))


@router.get("/{plan_id}", summary="计划详情")
async def get_plan(
    plan_id: int,
    versionNumber: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).get_plan(user.id, plan_id, versionNumber))


@router.get("/{plan_id}/versions", summary="计划历史版本")
async def list_versions(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).list_versions(user.id, plan_id))


@router.post("/{plan_id}/adopt", summary="采纳计划版本")
async def adopt(
    plan_id: int,
    payload: AdoptRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).adopt(user.id, plan_id, payload.versionNumber))


@meal_router.post("/{plan_meal_id}/execution", summary="标记餐次执行状态")
async def set_execution(
    plan_meal_id: int,
    payload: ExecutionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).set_execution(user.id, plan_meal_id, payload.state))


@preference_router.get("", summary="读取饮食偏好")
async def get_preference(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).get_preference(user.id))


@preference_router.put("", summary="保存饮食偏好")
async def save_preference(
    payload: PreferenceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await PlanService(db).save_preference(user.id, payload.model_dump()))
