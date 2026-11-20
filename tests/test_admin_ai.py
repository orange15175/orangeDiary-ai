"""问答追踪与管理端 AI 接口测试。

追踪表是"出问题时才看"的表，所以测试的重点不是功能多完整，而是：

- 普通用户拿不到（403 而不是空列表——空列表会掩盖越权）；
- 同一个 traceId 的多条记录按写入顺序返回（重试链路要能按时间读）；
- 保留期清理只删过期的；
- RAG 关闭时 `/rag-stats` 是 503 且文案说明原因，不是 500。
"""
from __future__ import annotations

import base64
import uuid
from dataclasses import replace
from datetime import datetime, timedelta

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
from app.models.ai import AiChatTrace
from app.models.user import User
from app.services.ai_trace_service import AiChatTraceService
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def build_settings(**overrides) -> Settings:
    base = Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"admin-ai-secret-key-32-byte!!").decode("ascii"),
    )
    return replace(base, **overrides)


async def _trace(
    db,
    *,
    user_id: int | None = 1,
    trace_id: str = "t-1",
    question_excerpt: str = "今天吃了多少热量",
    created_at=None,
):
    trace = AiChatTrace(
        trace_id=trace_id,
        user_id=user_id,
        conversation_id="c-1",
        intent="diet_query",
        question_excerpt=question_excerpt,
        question_hash="abc" * 10,
        source_count=3,
        retrieval_ms=120,
        rerank_state="rerank",
        degraded=None,
        total_ms=800,
        created_at=created_at or datetime.now(),
    )
    db.add(trace)
    await db.commit()
    return trace


# ==================== 服务层 ====================


@pytest.mark.asyncio
async def test_page_filters_by_user_and_trace(db):
    await _trace(db, user_id=1, trace_id="t-1")
    await _trace(db, user_id=2, trace_id="t-2")

    service = AiChatTraceService(db)
    all_rows = await service.page(page=1, size=20)
    assert all_rows["total"] == 2
    # 默认按 id 倒序：最新一次问答在最上面。
    assert all_rows["records"][0]["userId"] == 2

    by_user = await service.page(page=1, size=20, user_id=1)
    assert by_user["total"] == 1

    by_trace = await service.page(page=1, size=20, trace_id="  t-2  ")
    assert by_trace["total"] == 1
    assert by_trace["records"][0]["traceId"] == "t-2"


@pytest.mark.asyncio
async def test_page_size_is_capped_at_100(db):
    await _trace(db)
    page = await AiChatTraceService(db).page(page=1, size=5000)
    assert page["size"] == 100


@pytest.mark.asyncio
async def test_by_trace_id_returns_in_write_order(db):
    for index in range(3):
        await _trace(db, trace_id="retry-chain", question_excerpt=f"第{index}次")
    rows = await AiChatTraceService(db).by_trace_id("retry-chain")
    assert [row["questionExcerpt"] for row in rows] == ["第0次", "第1次", "第2次"]


@pytest.mark.asyncio
async def test_row_carries_no_full_text_fields(db):
    trace = await _trace(db)
    row = (await AiChatTraceService(db).by_trace_id("t-1"))[0]
    assert row["questionExcerpt"] == "今天吃了多少热量"
    # 只有摘要与哈希，没有"完整问题/回答正文"字段。
    assert not {"question", "answer", "content"} & set(row)
    assert row["questionHash"].startswith("abc")


@pytest.mark.asyncio
async def test_purge_only_removes_expired(db):
    await _trace(db, trace_id="old", created_at=datetime.now() - timedelta(days=40))
    await _trace(db, trace_id="fresh")

    removed = await AiChatTraceService(db).purge_older_than()
    await db.commit()

    assert removed == 1
    assert await db.scalar(select(func.count()).select_from(AiChatTrace)) == 1


# ==================== 接口层 ====================


@pytest_asyncio.fixture
async def context():
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
    # RAG 关闭：/rag-stats 应该给 503，而不是去连 Qdrant。
    settings = build_settings(rag_enabled=False)

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_store] = lambda: store

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, session

    await session.close()
    await engine.dispose()


async def _login(http: AsyncClient, session, *, admin: bool = False) -> dict[str, str]:
    email = f"admin-ai-{uuid.uuid4().hex[:8]}@example.com"
    await http.post(
        "/api/auth/register",
        json={
            "username": "追踪橙子",
            "email": email,
            "password": "orange123",
            "confirmPassword": "orange123",
        },
    )
    tokens = (
        await http.post("/api/auth/login", json={"account": email, "password": "orange123"})
    ).json()["data"]
    headers = {"Authorization": f"Bearer {tokens['token']}"}
    if admin:
        me = (await http.get("/api/users/me", headers=headers)).json()["data"]
        user = await session.get(User, me["id"])
        user.role = "admin"
        await session.commit()
        assert user.role == "admin"
    return headers


@pytest.mark.asyncio
async def test_normal_user_cannot_read_traces(context):
    http, session = context
    headers = await _login(http, session)
    response = await http.get("/api/admin/ai/traces", headers=headers)
    assert response.status_code == 403
    assert response.json()["message"] == "仅管理员可访问"


@pytest.mark.asyncio
async def test_admin_can_read_traces(context):
    http, session = context
    headers = await _login(http, session, admin=True)
    await _trace(session, user_id=1, trace_id="t-admin")

    listing = await http.get("/api/admin/ai/traces", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1

    detail = await http.get("/api/admin/ai/traces/t-admin", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["data"]) == 1
    assert detail.json()["data"][0]["traceId"] == "t-admin"


@pytest.mark.asyncio
async def test_rag_stats_reports_disabled(context):
    http, session = context
    headers = await _login(http, session, admin=True)
    response = await http.get("/api/admin/ai/rag-stats", headers=headers)
    assert response.status_code == 503
    assert response.json()["message"] == "知识检索未启用"
