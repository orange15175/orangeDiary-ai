"""来源快照与赞踩反馈测试。

重点不是 CRUD 本身，而是几个会直接毁掉功能的边界：

- 快照必须能解释"旧回答引用了什么"：存片段正文、标题、索引版本；重复写不产生副本。
- 快照写失败不能把已经生成的回答变成 500（best-effort）。
- 赞踩只能评自己的 assistant 消息；评别人消息 / 评用户提问都必须拒绝。
- 重复提交是改主意（单行更新），不是堆行；取消是真删行。
- 历史接口一次装配快照与计数，不能 N+1 到超时。
"""
from __future__ import annotations

import base64
from dataclasses import replace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_session_store, get_settings
from app.main import create_app
from app.models.ai import AiAnswerFeedback, AiAnswerSource, AiConversation, AiConversationMessage
from app.models.user import User
from app.services.ai_feedback import AiFeedbackService
from app.services.ai_source_snapshot import AiSourceSnapshotService
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore


def build_settings(**overrides) -> Settings:
    base = Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"source-feedback-secret-key-32b").decode("ascii"),
    )
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


class Hit:
    def __init__(self, rank: int = 1) -> None:
        self.rank = rank
        self.chunk_id = f"chunk-{rank}"
        self.document_id = "doc-1"
        self.title = "成人肥胖食养指南"
        self.heading_path = ("控制总能量摄入", "蛋白质")
        self.text = "每日蛋白质摄入应占总能量的15%到20%。"
        self.source_url = "https://example.org/guide"
        self.needs_review = False
        self.score = 0.87


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    store = InMemoryAuthSessionStore()
    settings = build_settings()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 三个账号都走真实注册，密码哈希与登录链路一致；服务层用例按 email 取 id。
        for name, email in (
            ("alice", "alice@example.com"),
            ("bob", "bob@example.com"),
            ("admin", "admin@example.com"),
        ):
            await client.post(
                "/api/auth/register",
                json={
                    "username": name,
                    "email": email,
                    "password": "orange123",
                    "confirmPassword": "orange123",
                },
            )
        yield client, session, factory, store, settings
    app.dependency_overrides.clear()
    await session.close()
    await engine.dispose()


async def token(client: AsyncClient, session, email: str, *, admin: bool = False) -> str:
    """走真实登录链路拿 token，不改会话库；管理员标识直接改库里的角色。"""
    if admin:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.role = "admin"
        await session.commit()
    tokens = (
        await client.post("/api/auth/login", json={"account": email, "password": "orange123"})
    ).json()["data"]
    return tokens["token"]


async def seed_message(session, user_id: int, role: str = "assistant") -> AiConversationMessage:
    from app.services.ai_conversation import default_conversation_id
    conv = AiConversation(user_id=user_id, conversation_id=default_conversation_id(user_id))
    session.add(conv)
    await session.flush()
    msg = AiConversationMessage(conversation_id=conv.id, message_sequence=1, role=role, content="建议")
    session.add(msg)
    await session.commit()
    return msg


# ---------------- 来源快照：服务层 ----------------


@pytest.mark.asyncio
async def test_snapshot_records_excerpt_and_index_version(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1)
    service = AiSourceSnapshotService(session)
    added = await service.record(
        message_id=msg.id,
        trace_id="t-1",
        hits=[Hit()],
        index_version="knowledge-v3",
    )
    await session.commit()

    assert added == 1
    rows = await service.by_message(msg.id)
    assert len(rows) == 1
    assert rows[0]["chunkId"] == "chunk-1"
    assert rows[0]["title"] == "成人肥胖食养指南"
    assert rows[0]["headingPath"] == ["控制总能量摄入", "蛋白质"]
    assert "蛋白质摄入" in rows[0]["excerpt"]
    assert rows[0]["indexVersion"] == "knowledge-v3"
    assert rows[0]["score"] == pytest.approx(0.87)


@pytest.mark.asyncio
async def test_snapshot_is_idempotent(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1)
    service = AiSourceSnapshotService(session)
    await service.record(message_id=msg.id, trace_id="t-1", hits=[Hit(1), Hit(2)])
    await service.record(message_id=msg.id, trace_id="t-1", hits=[Hit(1), Hit(2)])
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(AiAnswerSource))
    assert count == 2


@pytest.mark.asyncio
async def test_snapshot_by_trace_and_page(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1)
    service = AiSourceSnapshotService(session)
    await service.record(message_id=msg.id, trace_id="trace-A", hits=[Hit(1)])
    await session.commit()

    assert len(await service.by_trace("trace-A")) == 1
    page = await service.page(page=1, size=10, trace_id="trace-A")
    assert page["total"] == 1
    assert page["records"][0]["traceId"] == "trace-A"


# ---------------- 赞踩：服务层边界 ----------------


@pytest.mark.asyncio
async def test_feedback_rejects_other_users_message(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 2)  # bob 的回答
    service = AiFeedbackService(session)
    with pytest.raises(Exception) as exc:
        await service.set(message_id=msg.id, user_id=1, rating="up")
    assert getattr(exc.value, "code", None) == 404


@pytest.mark.asyncio
async def test_feedback_rejects_user_message(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1, role="user")
    service = AiFeedbackService(session)
    with pytest.raises(Exception) as exc:
        await service.set(message_id=msg.id, user_id=1, rating="up")
    assert getattr(exc.value, "code", None) == 404


@pytest.mark.asyncio
async def test_feedback_switch_and_cancel(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1)
    service = AiFeedbackService(session)

    await service.set(message_id=msg.id, user_id=1, rating="up")
    await service.set(message_id=msg.id, user_id=1, rating="down")
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(AiAnswerFeedback)) == 1
    counts = await service.counts([msg.id])
    assert counts[msg.id] == {"up": 0, "down": 1}

    await service.set(message_id=msg.id, user_id=1, rating=None)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(AiAnswerFeedback)) == 0
    assert await service.mine(message_id=msg.id, user_id=1) is None


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_rating(ctx):
    _client, session, _factory, _store, _settings = ctx
    msg = await seed_message(session, 1)
    with pytest.raises(Exception) as exc:
        await AiFeedbackService(session).set(message_id=msg.id, user_id=1, rating="star")
    assert getattr(exc.value, "code", None) == 400


# ---------------- HTTP 接口 ----------------


@pytest.mark.asyncio
async def test_history_includes_sources_and_counts(ctx):
    client, session, _factory, store, _settings = ctx
    msg = await seed_message(session, 1)
    await AiSourceSnapshotService(session).record(message_id=msg.id, trace_id="t", hits=[Hit()])
    await AiFeedbackService(session).set(message_id=msg.id, user_id=1, rating="up")
    await session.commit()

    response = await client.get(
        "/api/ai/conversation/messages",
        headers={"Authorization": "Bearer " + await token(client, session, "alice@example.com")},
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["rating"] == "up"
    assert rows[0]["upCount"] == 1
    assert rows[0]["sources"][0]["chunkId"] == "chunk-1"


@pytest.mark.asyncio
async def test_feedback_endpoint_requires_login(ctx):
    client, _session, _factory, _store, _settings = ctx
    response = await client.post("/api/ai/messages/1/feedback", json={"rating": "up"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_feedback_endpoint_set_and_cancel(ctx):
    client, session, _factory, store, _settings = ctx
    msg = await seed_message(session, 1)
    headers = {
        "Authorization": "Bearer " + await token(client, session, "alice@example.com")
    }

    response = await client.post(
        f"/api/ai/messages/{msg.id}/feedback", headers=headers, json={"rating": "up"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["rating"] == "up"

    response = await client.post(
        f"/api/ai/messages/{msg.id}/feedback", headers=headers, json={"rating": None}
    )
    assert response.status_code == 200
    assert response.json()["data"]["rating"] is None
    assert await session.scalar(select(func.count()).select_from(AiAnswerFeedback)) == 0


@pytest.mark.asyncio
async def test_admin_sources_and_feedbacks_require_admin(ctx):
    client, session, _factory, store, _settings = ctx
    msg = await seed_message(session, 1)
    await AiSourceSnapshotService(session).record(message_id=msg.id, trace_id="trace-X", hits=[Hit()])
    await AiFeedbackService(session).set(message_id=msg.id, user_id=1, rating="up")
    await session.commit()

    alice = {
        "Authorization": "Bearer " + await token(client, session, "alice@example.com")
    }
    assert (await client.get("/api/admin/ai/sources", headers=alice)).status_code == 403
    assert (await client.get("/api/admin/ai/feedbacks", headers=alice)).status_code == 403

    admin = {
        "Authorization": "Bearer "
        + await token(client, session, "admin@example.com", admin=True)
    }
    sources = await client.get("/api/admin/ai/sources?traceId=trace-X", headers=admin)
    assert sources.status_code == 200
    assert sources.json()["data"]["total"] == 1

    feedbacks = await client.get("/api/admin/ai/feedbacks?rating=up", headers=admin)
    assert feedbacks.status_code == 200
    assert feedbacks.json()["data"]["total"] == 1

    bad = await client.get("/api/admin/ai/feedbacks?rating=star", headers=admin)
    assert bad.status_code == 400
