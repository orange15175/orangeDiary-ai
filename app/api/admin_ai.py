"""AI 管理端接口（对齐 `AdminAiTraceController`）。

管理端能看"这一次问答慢在哪一段、检索到了几条、有没有降级"，但看不到
完整问题与回答——摘要与哈希足够定位问题类型，不足以复原用户原话。

`/rag-stats` 直接读**本进程**的检索服务，不再绕一次 HTTP：现在前后端在同一个
Python 进程里，还去调内部接口只会让计数器分成两份，排障时对不上。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rag import runtime_stats
from app.config import Settings
from app.core.errors import BusinessError, success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.services.ai_feedback import AiFeedbackService, RATINGS
from app.services.ai_source_snapshot import AiSourceSnapshotService
from app.services.ai_trace_service import AiChatTraceService

router = APIRouter(prefix="/api/admin/ai", tags=["AI 管理"])


def get_trace_service(db: AsyncSession = Depends(get_session)) -> AiChatTraceService:
    return AiChatTraceService(db)


async def _require_admin(db: AsyncSession, user_id: int) -> User:
    # 每个请求回库读角色：管理员被降权后，旧 token 不该还能看追踪。
    user = await db.get(User, user_id)
    if user is None or user.role != "admin" or user.status != "active":
        raise BusinessError(403, "仅管理员可访问")
    return user


@router.get("/traces", summary="问答追踪列表")
async def list_traces(
    page: int = Query(1),
    size: int = Query(20),
    userId: int | None = Query(None),
    traceId: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: AiChatTraceService = Depends(get_trace_service),
):
    await _require_admin(db, user.id)
    return success(
        await service.page(page=page, size=size, user_id=userId, trace_id=traceId)
    )


@router.get("/traces/{trace_id}", summary="按 traceId 查一次问答的全部记录")
async def trace_detail(
    trace_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: AiChatTraceService = Depends(get_trace_service),
):
    await _require_admin(db, user.id)
    # 同一个 traceId 可能有多条（重试、多段流程），所以返回列表而不是单条。
    return success(await service.by_trace_id(trace_id))


@router.get("/sources", summary="回答引用的来源快照列表")
async def list_sources(
    page: int = Query(1),
    size: int = Query(20),
    traceId: str | None = Query(None),
    messageId: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    await _require_admin(db, user.id)
    return success(
        await AiSourceSnapshotService(db).page(
            page=page, size=size, trace_id=traceId, message_id=messageId
        )
    )


@router.get("/feedbacks", summary="用户赞踩反馈列表")
async def list_feedbacks(
    page: int = Query(1),
    size: int = Query(20),
    rating: str | None = Query(None),
    userId: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    await _require_admin(db, user.id)
    # 非法 rating 直接返回空列表会让管理员以为"没有数据"，不如明说是参数错。
    if rating is not None and rating not in RATINGS:
        raise BusinessError(400, "评分只能是 up 或 down")
    return success(
        await AiFeedbackService(db).page(page=page, size=size, rating=rating, user_id=userId)
    )


@router.get("/rag-stats", summary="检索运行态：缓存命中率、熔断与限流")
async def rag_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    await _require_admin(db, user.id)
    if not settings.rag_enabled:
        raise BusinessError(503, "知识检索未启用")
    try:
        return success(runtime_stats(settings))
    except Exception as exc:
        # 检索服务构造失败（集合不存在、Qdrant 不可达）是 502 而不是 500：
        # 这是下游依赖的问题，管理员该去查 Qdrant，不该以为后端崩了。
        raise BusinessError(502, "知识检索服务不可用") from exc
