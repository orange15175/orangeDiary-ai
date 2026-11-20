"""管理操作流水接口（对齐 `AdminActionLogController`）。

流水是只读的：没有任何接口能改或删一条处置记录。这一点和帖子/评论不同——
处置可以撤销，记录不能。出了争议，靠的就是这份表能原样回放。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services.moderation_service import ModerationService

router = APIRouter(prefix="/api/admin/action-logs", tags=["管理流水"])


def get_moderation_service(db: AsyncSession = Depends(get_session)) -> ModerationService:
    return ModerationService(db)


@router.get("", summary="操作流水列表")
async def list_logs(
    page: int = Query(1),
    size: int = Query(10),
    action: str | None = Query(None),
    adminId: int | None = Query(None),
    targetId: int | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None),
    user: User = Depends(get_current_user),
    service: ModerationService = Depends(get_moderation_service),
):
    return success(
        await service.list_logs(
            user.id,
            page=page,
            size=size,
            action=action,
            admin_id=adminId,
            target_id=targetId,
            from_time=from_,
            until_time=until,
        )
    )


@router.get("/{log_id}", summary="操作流水详情")
async def get_log(
    log_id: int,
    user: User = Depends(get_current_user),
    service: ModerationService = Depends(get_moderation_service),
):
    return success(await service.get_log(user.id, log_id))
