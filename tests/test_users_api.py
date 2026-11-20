"""用户中心 HTTP 契约测试：走真实 JWT + 内存会话，只把数据库换成 SQLite。

这里刻意不用 mock 掉 `get_current_user`——鉴权本身就是要验的东西。
覆盖的拒绝路径（无令牌 / 令牌损坏 / 会话被销毁 / 账号禁用）正是 Java
拦截器里的四道判定，任何一条没对齐，切流时就会出现"该登出的没登出"。
"""
from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_session_store, get_settings
from app.main import create_app
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore


def build_settings() -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"users-api-secret-key-32-bytes!").decode("ascii"),
    )


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
    settings = build_settings()

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_store] = lambda: store

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, session, store

    await session.close()
    await engine.dispose()


async def _login(http: AsyncClient) -> tuple[dict, str]:
    body = {
        "username": "接口橙子",
        "email": "users@example.com",
        "password": "orange123",
        "confirmPassword": "orange123",
    }
    await http.post("/api/auth/register", json=body)
    response = await http.post("/api/auth/login", json={"account": "users@example.com", "password": "orange123"})
    data = response.json()["data"]
    return data, data["token"]


@pytest.mark.asyncio
async def test_requires_bearer_token(context):
    http, _, _ = context
    response = await http.get("/api/users/me")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 401
    assert body["message"] == "请先登录"
    assert body["traceId"]  # 外壳与 Java ApiResponse 对齐，前端要读 traceId


@pytest.mark.asyncio
async def test_rejects_malformed_token(context):
    http, _, _ = context
    response = await http.get("/api/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["message"] == "登录状态已失效，请重新登录"


@pytest.mark.asyncio
async def test_returns_current_user(context):
    http, _, _ = context
    _, token = await _login(http)

    response = await http.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["email"] == "users@example.com"
    assert data["nickname"] == "接口橙子"
    assert data["profile"] is None
    assert data["profileCompleted"] is False


@pytest.mark.asyncio
async def test_validate_endpoint(context):
    http, _, _ = context
    _, token = await _login(http)
    response = await http.get("/api/users/validate", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["data"] == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_destroyed_after_logout(context):
    http, session, store = context
    tokens, token = await _login(http)

    await http.post("/api/auth/logout", json={"refreshToken": tokens["refreshToken"]})
    response = await http.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["message"] == "登录状态已失效，请重新登录"


@pytest.mark.asyncio
async def test_disabled_account_is_rejected(context):
    http, session, _ = context
    _, token = await _login(http)

    user = await session.scalar(select(User).where(User.email == "users@example.com"))
    user.status = "disabled"
    await session.commit()

    response = await http.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["message"] == "账号不存在或已被禁用"


@pytest.mark.asyncio
async def test_profile_update_returns_updated_user(context):
    http, _, _ = context
    _, token = await _login(http)
    headers = {"Authorization": f"Bearer {token}"}

    response = await http.put(
        "/api/users/profile",
        headers=headers,
        json={"nickname": "小橙", "gender": "female", "heightCm": 165.5, "currentWeightKg": 55},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["nickname"] == "小橙"
    assert data["profile"]["gender"] == "female"
    assert data["profile"]["heightCm"] == "165.50" or float(data["profile"]["heightCm"]) == 165.5


@pytest.mark.asyncio
async def test_profile_validation_message_is_chinese(context):
    http, _, _ = context
    _, token = await _login(http)

    response = await http.put(
        "/api/users/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"gender": "unknown"},
    )
    assert response.status_code == 400
    assert response.json()["message"] == "性别只能是male或female"


@pytest.mark.asyncio
async def test_goal_end_before_start_returns_400(context):
    http, _, _ = context
    _, token = await _login(http)

    response = await http.post(
        "/api/users/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={"goalType": "lose", "startDate": "2026-11-18", "endDate": "2026-11-01"},
    )
    assert response.status_code == 400
    assert response.json()["message"] == "目标结束日期不能早于开始日期"


@pytest.mark.asyncio
async def test_goal_creation_activates_and_marks_completed(context):
    http, _, _ = context
    _, token = await _login(http)
    headers = {"Authorization": f"Bearer {token}"}

    await http.put(
        "/api/users/profile",
        headers=headers,
        json={"gender": "male", "heightCm": 178, "currentWeightKg": 70, "targetWeightKg": 66},
    )
    response = await http.post(
        "/api/users/goals",
        headers=headers,
        json={"goalType": "lose", "dailyIntakeTarget": 1800, "startDate": "2026-11-18"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["activeGoal"]["goalType"] == "lose"
    assert data["activeGoal"]["status"] == "active"
    assert data["profileCompleted"] is True


@pytest.mark.asyncio
async def test_active_user_count(context):
    http, _, _ = context
    _, token = await _login(http)
    response = await http.get("/api/users/count", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["data"]["totalUsers"] == 1
