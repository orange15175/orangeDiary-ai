"""站内消息接口（`/api/notifications`）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/api/notifications", tags=["站内消息"])


def get_notification_service(db: AsyncSession = Depends(get_session)) -> NotificationService:
    return NotificationService(db)


@router.get("", summary="分页查询消息")
async def list_notifications(
    page: int = Query(default=1),
    size: int = Query(default=10),
    type: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    return success(await service.list(user.id, page, size, type))


@router.get("/unread-count", summary="未读消息数")
async def unread_count(
    user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    return success({"count": await service.unread_count(user.id)})


@router.put("/{notification_id}/read", summary="标记消息已读")
async def mark_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: NotificationService = Depends(get_notification_service),
):
    await service.mark_read(user.id, notification_id)
    await db.commit()
    return success(None)
