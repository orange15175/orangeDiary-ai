"""每日汇总接口（`/api/daily-summaries`）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services.summary_service import SummaryService

router = APIRouter(prefix="/api/daily-summaries", tags=["每日汇总"])


def get_summary_service(db: AsyncSession = Depends(get_session)) -> SummaryService:
    return SummaryService(db)


@router.get("", summary="Get daily calorie summary")
async def get_daily_summary(
    date: date = Query(...),
    user: User = Depends(get_current_user),
    service: SummaryService = Depends(get_summary_service),
):
    return success(await service.get_summary(user.id, date))
