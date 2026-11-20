"""回答赞踩反馈。

两条硬约束：

1. **身份只从登录态取**：请求体里不接受 userId。否则任何登录用户都能替别人点赞。
2. **只能评自己的助手消息**：`message` 必须先关联到 `ai_conversation` 再按
   `user_id` 校验，且角色必须是 assistant。不能评自己的提问，更不能评别人的回答。

同一用户重复提交是"改主意"，更新原行而不是新增；传 `None` 是取消，直接删行。
这样计数等于表里的行数，不需要再做去重。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.ai import AiAnswerFeedback, AiConversation, AiConversationMessage
from app.services.paging import mp_page, safe_page, safe_size

RATINGS = ("up", "down")
_MAX_PAGE_SIZE = 100


def feedback_row(row: AiAnswerFeedback) -> dict[str, Any]:
    return {
        "id": row.id,
        "messageId": row.message_id,
        "userId": row.user_id,
        "rating": row.rating,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


class AiFeedbackService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _owned_assistant_message(self, message_id: int, user_id: int) -> AiConversationMessage:
        row = (
            await self._db.scalar(
                select(AiConversationMessage)
                .join(AiConversation, AiConversation.id == AiConversationMessage.conversation_id)
                .where(
                    AiConversationMessage.id == message_id,
                    AiConversation.user_id == user_id,
                    AiConversationMessage.role == "assistant",
                )
            )
        )
        if row is None:
            # 不区分"不存在"与"不是你的"：避免用返回值探测别人的 message_id。
            raise BusinessError(404, "消息不存在")
        return row

    async def set(self, *, message_id: int, user_id: int, rating: str | None) -> dict[str, Any]:
        if rating is not None and rating not in RATINGS:
            raise BusinessError(400, "评分只能是 up 或 down")
        await self._owned_assistant_message(message_id, user_id)

        existing = await self._db.scalar(
            select(AiAnswerFeedback).where(
                AiAnswerFeedback.message_id == message_id,
                AiAnswerFeedback.user_id == user_id,
            )
        )
        if rating is None:
            if existing is not None:
                await self._db.delete(existing)
                await self._db.flush()
            return {"messageId": message_id, "rating": None}

        if existing is None:
            self._db.add(
                AiAnswerFeedback(
                    message_id=message_id,
                    user_id=user_id,
                    rating=rating,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
        elif existing.rating != rating:
            existing.rating = rating
            existing.updated_at = datetime.now()
        await self._db.flush()
        return {"messageId": message_id, "rating": rating}

    async def mine(self, *, message_id: int, user_id: int) -> str | None:
        return await self._db.scalar(
            select(AiAnswerFeedback.rating).where(
                AiAnswerFeedback.message_id == message_id,
                AiAnswerFeedback.user_id == user_id,
            )
        )

    async def mine_for_messages(self, message_ids: list[int], user_id: int) -> dict[int, str]:
        if not message_ids:
            return {}
        rows = (
            await self._db.execute(
                select(AiAnswerFeedback.message_id, AiAnswerFeedback.rating).where(
                    AiAnswerFeedback.message_id.in_(message_ids),
                    AiAnswerFeedback.user_id == user_id,
                )
            )
        ).all()
        return {int(message_id): str(rating) for message_id, rating in rows}

    async def counts(self, message_ids: list[int]) -> dict[int, dict[str, int]]:
        if not message_ids:
            return {}
        rows = (
            await self._db.execute(
                select(
                    AiAnswerFeedback.message_id,
                    AiAnswerFeedback.rating,
                    func.count().label("n"),
                )
                .where(AiAnswerFeedback.message_id.in_(message_ids))
                .group_by(AiAnswerFeedback.message_id, AiAnswerFeedback.rating)
            )
        ).all()
        result: dict[int, dict[str, int]] = {mid: {"up": 0, "down": 0} for mid in message_ids}
        for message_id, rating, n in rows:
            if rating in RATINGS and message_id in result:
                result[message_id][rating] = int(n)
        return result

    async def page(
        self,
        *,
        page: int,
        size: int,
        rating: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        current = safe_page(page)
        limit = safe_size(size, _MAX_PAGE_SIZE)
        conditions: list[Any] = []
        if rating in RATINGS:
            conditions.append(AiAnswerFeedback.rating == rating)
        if user_id is not None:
            conditions.append(AiAnswerFeedback.user_id == user_id)
        total = int(
            await self._db.scalar(
                select(func.count()).select_from(AiAnswerFeedback).where(*conditions)
            )
            or 0
        )
        rows = (
            await self._db.scalars(
                select(AiAnswerFeedback)
                .where(*conditions)
                .order_by(AiAnswerFeedback.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return mp_page([feedback_row(row) for row in rows], total=total, current=current, limit=limit)
