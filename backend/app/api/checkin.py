"""登录足迹接口（`/api/check-ins`）。

`POST /today` 走幂等，且**把日期写进摘要**：同一个 requestId 在跨天后重放时，
摘要不同会返回 409 而不是把今天的足迹记成昨天——Java 的注释专门说明了这一点。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services import idempotency
from app.services.checkin_service import CheckinService, today_in_app_zone

router = APIRouter(prefix="/api/check-ins", tags=["登录足迹"])

DAILY_CHECKIN = "DAILY_CHECKIN"


def get_checkin_service(db: AsyncSession = Depends(get_session)) -> CheckinService:
    return CheckinService(db)


@router.post("/today", summary="记录今天的登录足迹")
async def record_today(
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: CheckinService = Depends(get_checkin_service),
):
    async def action() -> None:
        await service.record_today(user.id)
        return None

    await idempotency.execute(
        db,
        user_id=user.id,
        operation=DAILY_CHECKIN,
        request_id=x_request_id,
        payload={"checkinDate": today_in_app_zone().isoformat()},
        action=action,
    )
    return success(None)


@router.get("", summary="查询指定日期范围的登录足迹")
async def get_footprint(
    startDate: date = Query(...),
    endDate: date = Query(...),
    user: User = Depends(get_current_user),
    service: CheckinService = Depends(get_checkin_service),
):
    return success(await service.get_footprint(user.id, startDate, endDate))
