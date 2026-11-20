"""AI 助手接口，路径与 `AiController` 一致。

流式那支有两个容易被"优化"掉的细节：

- `media_type` 必须显式带 `charset=utf-8`。少了它，反向代理或客户端会按
  ISO-8859-1 解中文 token（实测"控制钠的摄入"变成 "??????"），而且这种乱码
  只在流式出现，非流式接口看起来完全正常，排查时极易漏掉。
- `X-Accel-Buffering: no` 是为了让 nginx 不攒包。默认缓冲会把逐字输出变成
  "憋几秒再一次性刷出来"，用户以为卡死了。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError, success
from app.core.javacompat import local_datetime_string
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.schemas.ai import AiChatRequest, AiFeedbackRequest
from app.services.ai_conversation import ConversationService
from app.services.ai_feedback import AiFeedbackService
from app.services.ai_service import AiChatService
from app.services.ai_source_snapshot import AiSourceSnapshotService

log = logging.getLogger("orange-diary-ai.api.ai")

router = APIRouter(prefix="/api/ai", tags=["AI助手"])


def _ensure_enabled(settings: Settings) -> None:
    """`FEATURES_AI_CHAT_ENABLED=false` 时三个端点都当作不存在。

    对齐 Java：控制器带 `@ConditionalOnProperty`，关掉后路径直接 404，
    不会返回"AI 已关闭"这种需要前端额外分支的响应。
    """
    if not settings.ai_chat_enabled:
        raise BusinessError(404, "接口不存在")


def _service(
    db: AsyncSession, settings: Settings, user: User
) -> AiChatService:
    return AiChatService(
        db,
        settings,
        user.id,
        # 管理员免次数：演示账号被自己的配额拦在门口是最难看的故障。
        is_admin=(user.role == "admin"),
        conversation_service=ConversationService(db, settings),
    )


@router.post("/chat")
async def chat(
    payload: AiChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    _ensure_enabled(settings)
    return success(await _service(db, settings, user).chat(payload.message))


@router.post("/chat/stream")
async def chat_stream(
    payload: AiChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    _ensure_enabled(settings)
    service = _service(db, settings, user)
    return StreamingResponse(
        service.chat_stream(payload.message),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversation/messages")
async def history(
    limit: int = Query(40),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    _ensure_enabled(settings)
    rows = await ConversationService(db, settings).recent_messages(user.id, limit)
    # 回答附带当时的来源快照（永久保存），这样索引更新后旧回答仍可追溯引用。
    snapshots = await AiSourceSnapshotService(db).by_messages([row.id for row in rows])
    feedback = AiFeedbackService(db)
    ratings = await feedback.mine_for_messages([row.id for row in rows], user.id)
    counts = await feedback.counts([row.id for row in rows])
    return success(
        [
            {
                "messageId": row.id,
                "role": row.role,
                "content": row.content,
                "createdAt": local_datetime_string(row.created_at),
                "sources": snapshots.get(row.id, []),
                "rating": ratings.get(row.id),
                "upCount": counts.get(row.id, {}).get("up", 0),
                "downCount": counts.get(row.id, {}).get("down", 0),
            }
            for row in rows
        ]
    )


@router.post("/messages/{message_id}/feedback", summary="赞或踩一条回答")
async def set_feedback(
    message_id: int,
    payload: AiFeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    _ensure_enabled(settings)
    result = await AiFeedbackService(db).set(
        message_id=message_id, user_id=user.id, rating=payload.rating
    )
    # 接口都要写库；get_session 本身不提交，漏了这一步赞踩刷新后就没了。
    await db.commit()
    return success(result)
