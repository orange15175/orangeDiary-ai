"""社区举报接口（对齐 `CommunityReportController`）。

用户侧两个入口都挂在 `/api/posts/...` 下，管理端两个挂在 `/api/admin/...` 下。
之所以拆两个 router 而不是加 prefix 参数，是因为 FastAPI 一个 APIRouter 只能
有一个 prefix，混在一起会得到 `/api/posts/{id}/admin/...` 这种不存在的路径。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.schemas.post import CreateCommunityReportRequest, ResolveReportRequest
from app.services.community_limit import build_limiter
from app.services.community_report_service import CommunityReportService

report_router = APIRouter(prefix="/api/posts", tags=["社区举报"])
admin_report_router = APIRouter(prefix="/api/admin/community-reports", tags=["举报处理"])


def get_report_service(
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CommunityReportService:
    return CommunityReportService(db, limiter=build_limiter(settings))


@report_router.post("/{post_id}/reports", summary="举报帖子")
async def report_post(
    post_id: int,
    payload: CreateCommunityReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: CommunityReportService = Depends(get_report_service),
):
    result = await service.report_post(user.id, post_id, payload)
    await db.commit()
    return success(result)


@report_router.post("/{post_id}/comments/{comment_id}/reports", summary="举报评论")
async def report_comment(
    post_id: int,
    comment_id: int,
    payload: CreateCommunityReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: CommunityReportService = Depends(get_report_service),
):
    result = await service.report_comment(user.id, post_id, comment_id, payload)
    await db.commit()
    return success(result)


@admin_report_router.get("", summary="举报列表")
async def list_reports(
    page: int = Query(1),
    size: int = Query(20),
    status: str | None = Query(None),
    user: User = Depends(get_current_user),
    service: CommunityReportService = Depends(get_report_service),
):
    return success(await service.list_admin(user.id, page=page, size=size, status=status))


@admin_report_router.put("/{report_id}", summary="处理举报")
async def resolve_report(
    report_id: int,
    payload: ResolveReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: CommunityReportService = Depends(get_report_service),
):
    result = await service.resolve(
        user.id, report_id, resolution=payload.resolution, reason=payload.reason
    )
    # 处置动作（下架/移除评论）与举报状态在同一个事务里提交。
    await db.commit()
    return success(result)
