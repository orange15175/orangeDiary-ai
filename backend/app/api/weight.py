"""体重接口（`/api/weight-records`）。

趋势接口限制 90 天：图表一次拉全史会让我的 1w 条记录变成慢查询，
Java 就是用 400 挡住的，这里保持同样的边界与文案。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.weight import CreateWeightRecordRequest
from app.services import idempotency
from app.services.weight_service import WeightService

router = APIRouter(prefix="/api/weight-records", tags=["体重记录"])

WEIGHT_CREATE = "WEIGHT_CREATE"


def get_weight_service(db: AsyncSession = Depends(get_session)) -> WeightService:
    return WeightService(db)


@router.post("", summary="Create weight record")
async def create_weight_record(
    payload: CreateWeightRecordRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: WeightService = Depends(get_weight_service),
):
    result = await idempotency.execute(
        db,
        user_id=user.id,
        operation=WEIGHT_CREATE,
        request_id=x_request_id,
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create_record(user.id, payload),
    )
    return success(result)


@router.get("", summary="List weight records for one day")
async def list_weight_records(
    date: date = Query(...),
    user: User = Depends(get_current_user),
    service: WeightService = Depends(get_weight_service),
):
    return success(await service.list_records(user.id, date))


@router.get("/trend", summary="Get daily weight trend for a date range")
async def weight_trend(
    startDate: date = Query(...),
    endDate: date = Query(...),
    user: User = Depends(get_current_user),
    service: WeightService = Depends(get_weight_service),
):
    return success(await service.list_trend(user.id, startDate, endDate))


@router.get("/progress", summary="Get weight progress")
async def weight_progress(
    user: User = Depends(get_current_user),
    service: WeightService = Depends(get_weight_service),
):
    return success(await service.get_progress(user.id))


@router.delete("/{record_id}", summary="Delete weight record")
async def delete_weight_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: WeightService = Depends(get_weight_service),
):
    await service.delete_record(user.id, record_id)
    await db.commit()
    return success(None)
