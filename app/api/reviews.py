"""周期复盘接口（M7）。

`POST /api/reviews` 生成或返回当前周期报告：输入没变直接回原报告，
输入变了把旧报告标 `stale` 再生成新的，不覆盖历史结论。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.review import DayStatusRequest, ReviewRequest
from app.services.review_service import ReviewService

router = APIRouter(prefix="/api/reviews", tags=["周期复盘"])
day_router = APIRouter(prefix="/api/diet-day-status", tags=["记录完整度"])


@router.post("", summary="生成周期复盘")
async def generate_review(
    payload: ReviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(
        await ReviewService(db).generate(
            user.id,
            period_start=payload.periodStart,
            period_end=payload.periodEnd,
            with_draft_plan=payload.withDraftPlan,
        )
    )


@router.get("", summary="复盘报告列表")
async def list_reviews(
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await ReviewService(db).list_reports(user.id, limit))


@router.get("/{report_id}", summary="复盘报告详情")
async def get_review(
    report_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await ReviewService(db).get(user.id, report_id))


@day_router.put("/{day}", summary="标记当天记录是否完整")
async def mark_day(
    day: date,
    payload: DayStatusRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await ReviewService(db).mark_day(user.id, day, payload.completeness, payload.note))


@day_router.get("", summary="查询记录完整度")
async def list_days(
    startDate: date = Query(...),
    endDate: date = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await ReviewService(db).list_days(user.id, startDate, endDate))
