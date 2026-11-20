"""AI 会话持久化测试（SQLite）。

钉住四件事：

1. **会话 ID 是 MD5 版 v3 UUID，且不含命名空间**。换成 `uuid.uuid3` 会算出不同的值，
   切流后老用户的 `ai_conversation` 行对不上，历史消息看起来全丢。
2. **序号必须连续且不撞唯一键**。撞键的表现是用户看到 500，而消息其实写进去了。
3. **上下文窗口双重封顶**（24 条 / 12000 字符）。只按条数封顶时，连续摘要失败会让
   token 成本线性膨胀。
4. **摘要游标乐观推进**。旧摘要不能覆盖新摘要。
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.base import Base
from app.models.ai import AiConversation, AiConversationMessage
from app.models.user import User
from app.services import ai_conversation
from app.services.ai_conversation import (
    MAX_CONTEXT_MESSAGES,
    ConversationService,
    default_conversation_id,
)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session


async def make_user(db, email: str | None = None) -> User:
    user = User(
        email=email or f"{uuid.uuid4().hex[:10]}@example.com",
        nickname="橙子",
        status="active",
        role="user",
    )
    db.add(user)
    await db.commit()
    return user


def service(db, factory) -> ConversationService:
    return ConversationService(db, Settings.from_env(), session_factory=factory)


def test_conversation_id_matches_java_name_uuid() -> None:
    """Java `UUID.nameUUIDFromBytes` 是"对字节做 MD5 再打成 v3 UUID"。"""
    expected_digest = hashlib.md5(b"orange-diary-ai-default:7").digest()
    expected = str(uuid.UUID(bytes=expected_digest, version=3))
    assert default_conversation_id(7) == expected
    # 带命名空间的 uuid3 会算出不同的值——这正是不能用它的原因。
    assert default_conversation_id(7) != str(uuid.uuid3(uuid.NAMESPACE_DNS, "7"))


def test_conversation_id_is_stable_and_distinct() -> None:
    assert default_conversation_id(1) == default_conversation_id(1)
    assert default_conversation_id(1) != default_conversation_id(2)
    value = uuid.UUID(default_conversation_id(1))
    assert value.version == 3
    assert value.variant == uuid.RFC_4122
    assert len(default_conversation_id(1)) == 36


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation_id = default_conversation_id(user.id)

    first = await svc.get_or_create(user.id, conversation_id)
    second = await svc.get_or_create(user.id, conversation_id)

    assert first.id == second.id
    assert first.summary_covered_sequence == 0
    assert first.status == "active"


@pytest.mark.asyncio
async def test_append_message_increments_sequence(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))

    await svc.append_message(conversation, "user", "今天吃了什么")
    await svc.append_message(conversation, "assistant", "你吃了米饭")
    await svc.append_message(conversation, "user", "热量呢")

    rows = await svc.recent_messages(user.id, 10)
    assert [row.message_sequence for row in rows] == [1, 2, 3]
    assert [row.role for row in rows] == ["user", "assistant", "user"]
    # last_message_at 必须跟着更新，管理页按它排序。
    refreshed = await db.get(AiConversation, conversation.id)
    assert refreshed.last_message_at is not None


@pytest.mark.asyncio
async def test_append_from_separate_sessions_continue_sequence(db, factory) -> None:
    """跨会话的序号必须接着排，不能各自从 1 开始。

    真正的并发碰撞隔离要 MySQL 的行锁才能验证（SQLite 会忽略 `FOR UPDATE`），
    这里钉的是同一份逻辑在串行交错下的结果：两个会话交替写，序号仍然连续。
    """
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    conversation_id = conversation.id

    for text in ("a", "b"):
        async with factory() as session:
            inner = ConversationService(session, Settings.from_env(), session_factory=factory)
            row = await session.get(AiConversation, conversation_id)
            await inner.append_message(row, "user", text)

    # 用新会话读，避免外层会话停在旧快照上。
    async with factory() as fresh:
        rows = list(
            await fresh.scalars(
                select(AiConversationMessage)
                .where(AiConversationMessage.conversation_id == conversation_id)
                .order_by(AiConversationMessage.message_sequence)
            )
        )
    assert [row.message_sequence for row in rows] == [1, 2]


@pytest.mark.asyncio
async def test_recent_messages_returns_ascending_tail(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    for index in range(5):
        await svc.append_message(conversation, "user", f"m{index}")

    rows = await svc.recent_messages(user.id, 3)
    assert [row.content for row in rows] == ["m2", "m3", "m4"]
    # limit 钳制：0 当 1，超过 100 当 100。
    assert len(await svc.recent_messages(user.id, 0)) == 1
    assert len(await svc.recent_messages(user.id, 500)) == 5


@pytest.mark.asyncio
async def test_recent_messages_ignores_unknown_user(db, factory) -> None:
    assert await service(db, factory).recent_messages(999, 10) == []


@pytest.mark.asyncio
async def test_build_context_without_history(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))

    context = await svc.build_context(conversation)
    assert "【长期对话摘要】" in context
    assert "暂无长期摘要。" in context
    assert "暂无历史消息。" in context
    assert context.endswith("请回答最近一条用户消息。")


@pytest.mark.asyncio
async def test_build_context_uses_role_names(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    await svc.append_message(conversation, "user", "我吃了米饭")
    await svc.append_message(conversation, "assistant", "收到")

    context = await svc.build_context(conversation)
    assert "[用户] 我吃了米饭" in context
    assert "[AI] 收到" in context


@pytest.mark.asyncio
async def test_build_context_caps_message_count(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    for index in range(MAX_CONTEXT_MESSAGES + 6):
        await svc.append_message(conversation, "user", f"m{index}")

    context = await svc.build_context(conversation)
    assert context.count("[用户]") == MAX_CONTEXT_MESSAGES
    # 保留的是最近的那批：最早的消息不该出现。
    assert "[用户] m0\n" not in context
    assert f"[用户] m{MAX_CONTEXT_MESSAGES + 5}" in context


@pytest.mark.asyncio
async def test_long_single_message_is_truncated(db, factory) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    await svc.append_message(conversation, "user", "字" * 5000)

    context = await svc.build_context(conversation)
    assert "…" in context
    # 2000 字 + 省略号，远小于原文。
    assert len(context) < 3000


@pytest.mark.asyncio
async def test_summary_advances_cursor(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    settings = Settings.from_env()
    svc = ConversationService(db, settings, session_factory=factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))

    total = settings.ai_summary_trigger_messages
    for index in range(total):
        await svc.append_message(conversation, "user" if index % 2 == 0 else "assistant", f"m{index}")

    async def fake_summary(prompt: str) -> str:
        assert "【旧摘要】" in prompt
        return "用户偏好少油少盐。"

    monkeypatch.setattr(ConversationService, "_call_summary_model", lambda self, prompt: fake_summary(prompt))
    await svc.summarize(conversation.id)

    refreshed = await db.get(AiConversation, conversation.id)
    await db.refresh(refreshed)
    assert refreshed.summary == "用户偏好少油少盐。"
    assert refreshed.summary_covered_sequence == settings.ai_summary_batch_size


@pytest.mark.asyncio
async def test_summary_skips_below_trigger(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = service(db, factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    await svc.append_message(conversation, "user", "只有一条")

    async def boom(self, prompt):  # pragma: no cover - 不该被调用
        raise AssertionError("消息不足 50 条时不应调用摘要模型")

    monkeypatch.setattr(ConversationService, "_call_summary_model", boom)
    await svc.summarize(conversation.id)
    refreshed = await db.get(AiConversation, conversation.id)
    await db.refresh(refreshed)
    assert refreshed.summary is None


@pytest.mark.asyncio
async def test_summary_cursor_conflict_discards_result(db, factory, monkeypatch) -> None:
    """游标已被别的任务推进时丢弃本次结果，不让旧摘要覆盖新摘要。"""
    user = await make_user(db)
    settings = Settings.from_env()
    svc = ConversationService(db, settings, session_factory=factory)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    for index in range(settings.ai_summary_trigger_messages):
        await svc.append_message(conversation, "user", f"m{index}")

    async def slow_summary(self, prompt: str) -> str:
        # 模拟"算摘要期间游标被推进"。
        async with factory() as other:
            row = await other.get(AiConversation, conversation.id)
            row.summary_covered_sequence = 99
            row.summary = "较新的摘要"
            await other.commit()
        return "旧摘要"

    monkeypatch.setattr(ConversationService, "_call_summary_model", slow_summary)
    await svc.summarize(conversation.id)

    async with factory() as fresh:
        row = await fresh.get(AiConversation, conversation.id)
        assert row.summary == "较新的摘要"
        assert row.summary_covered_sequence == 99


@pytest.mark.asyncio
async def test_request_summary_dedupes_inflight(db, factory, monkeypatch) -> None:
    """同一会话已有摘要任务在跑时直接合并，不排队。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(self, conversation_id: int) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(ConversationService, "summarize", slow)
    svc = service(db, factory)
    svc.request_summary(1)
    await started.wait()
    svc.request_summary(1)
    assert len(ai_conversation._summary_inflight) == 1
    release.set()
    await ai_conversation.drain_summary_tasks(timeout=2)
    assert 1 not in ai_conversation._summary_inflight


@pytest.mark.asyncio
async def test_default_session_factory_opens_sessions(db, factory, monkeypatch) -> None:
    """不传工厂时必须拿到 sessionmaker 本身，而不是"返回 sessionmaker 的函数"。

    回归：`self._session_factory = session_factory or get_session_factory` 少调一层，
    `async with self._session_factory()` 拿到的是 async_sessionmaker，抛
    TypeError: 'async_sessionmaker' object does not support the asynchronous
    context manager protocol。摘要和流式断线补写都走这条路，失败被 try/except
    吞成一条 warning，表现为"回答正常但历史摘要永远不推进"。
    """
    monkeypatch.setattr(ai_conversation, "get_session_factory", lambda: factory)
    user = await make_user(db)
    svc = ConversationService(db, Settings.from_env())
    assert svc._session_factory is factory

    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    await svc.append_message_by_id(conversation.id, "user", "断线补写的内容")

    rows = await svc.recent_messages(user.id, 10)
    assert [row.content for row in rows] == ["断线补写的内容"]


@pytest.mark.asyncio
async def test_history_and_context_keep_default_recent_transcript(db, factory, monkeypatch):
    user = await make_user(db)
    svc = service(db, factory)
    monkeypatch.setattr(svc, "request_summary", lambda _: None)
    conversation = await svc.get_or_create(user.id, default_conversation_id(user.id))
    for index in range(12):
        await svc.append_message(conversation, "user", f"turn-{index}:" + "字" * 1990)
    conversation.summary_covered_sequence = 12
    await db.commit()
    another = await svc.get_or_create(user.id, str(uuid.uuid4()))
    await svc.append_message(another, "user", "不属于默认会话")
    assert (await svc.recent_messages(user.id, 1))[0].content.startswith("turn-11:")
    context = await svc.build_context(conversation)
    assert "turn-11:" in context and "turn-10:" in context
    assert "turn-0:" not in context
    assert "不属于默认会话" not in context
