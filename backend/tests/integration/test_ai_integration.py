"""AI 会话在真实 MySQL 8 上的验收。

SQLite 跑不出来的两件事，只有这里能验证：

1. `SELECT ... FOR UPDATE` 真的串行化序号分配。SQLite 方言直接忽略这个子句，
   两个并发请求会算出同一个序号——线上的表现是其中一个用户收到 500。
2. 唯一键 `(conversation_id, message_sequence)` 在并发下真的会拦住重复序号，
   而不是像 SQLite 那样靠单连接串行"碰巧"不撞。
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models.ai import AiConversation, AiConversationMessage
from app.models.user import User
from app.services.ai_conversation import ConversationService, default_conversation_id

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"ai-integration-secret-32-bytes!").decode("ascii"),
    )


async def _seed(factory) -> tuple[int, int]:
    async with factory() as session:
        user = User(
            email=f"ai-{datetime.now().timestamp()}@example.com", status="active", role="user"
        )
        session.add(user)
        await session.commit()
        service = ConversationService(session, _settings(), session_factory=factory)
        conversation = await service.get_or_create(
            user.id, default_conversation_id(user.id)
        )
        return user.id, conversation.id


@pytest.mark.asyncio
async def test_concurrent_appends_get_distinct_sequences(mysql_session_factory):
    """并发两条消息必须拿到不同序号，且都不报错。"""
    user_id, conversation_id = await _seed(mysql_session_factory)

    async def append(text: str) -> None:
        async with mysql_session_factory() as session:
            service = ConversationService(session, _settings(), session_factory=mysql_session_factory)
            row = await session.get(AiConversation, conversation_id)
            await service.append_message(row, "user", text)

    await asyncio.gather(append("a"), append("b"), append("c"))

    async with mysql_session_factory() as session:
        rows = (
            await session.scalars(
                select(AiConversationMessage)
                .where(AiConversationMessage.conversation_id == conversation_id)
                .order_by(AiConversationMessage.message_sequence)
            )
        ).all()

    assert [row.message_sequence for row in rows] == [1, 2, 3]
    assert sorted(row.content for row in rows) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_conversation_id_is_stable_across_restarts(mysql_session_factory):
    """会话 ID 只由用户 ID 派生，所以"重启/换副本"后还能找到同一行。"""
    user_id, conversation_id = await _seed(mysql_session_factory)

    async with mysql_session_factory() as session:
        service = ConversationService(session, _settings(), session_factory=mysql_session_factory)
        again = await service.get_or_create(user_id, default_conversation_id(user_id))

    assert again.id == conversation_id
    assert again.conversation_id == default_conversation_id(user_id)
