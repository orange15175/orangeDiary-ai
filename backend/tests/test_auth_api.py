"""HTTP 层契约测试：响应外壳、traceId 头、中文校验文案。

不启动 lifespan（httpx 的 ASGITransport 不会触发），所以不会去加载 rerank 模型，
单测可以保持秒级。认证服务的依赖被替换成内存实现。
"""
from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.config import Settings
from app.db.base import Base
from app.main import create_app
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
        jwt_secret=base64.b64encode(b"api-test-secret-key-32-bytes!").decode("ascii"),
    )


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()

    app = create_app()
    service = AuthService(session, InMemoryAuthSessionStore(), build_settings())
    app.dependency_overrides[get_auth_service] = lambda: service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http

    await session.close()
    await engine.dispose()


REGISTER_BODY = {
    "username": "接口橙子",
    "email": "api@example.com",
    "password": "orange123",
    "confirmPassword": "orange123",
}


@pytest.mark.asyncio
async def test_register_returns_api_response_envelope(client: AsyncClient):
    response = await client.post("/api/auth/register", json=REGISTER_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["email"] == "api@example.com"
    # traceId 同时出现在响应体与响应头，前端报障时可直接引用。
    assert body["traceId"] == response.headers["X-Request-Id"]
    assert len(body["traceId"]) >= 8


@pytest.mark.asyncio
async def test_duplicate_register_returns_409_with_original_message(client: AsyncClient):
    await client.post("/api/auth/register", json=REGISTER_BODY)
    response = await client.post("/api/auth/register", json=REGISTER_BODY)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == 409
    assert body["message"] == "邮箱已注册"
    assert body["data"] is None


@pytest.mark.asyncio
async def test_validation_error_is_chinese_and_wrapped(client: AsyncClient):
    payload = {**REGISTER_BODY, "password": "short", "confirmPassword": "short"}
    response = await client.post("/api/auth/register", json=payload)
    # Java 对 @Valid 失败返回 400（ApiExceptionResolver），这里保持一致。
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 400
    # 不能出现 Pydantic 的英文前缀，前端会把它原样弹给用户。
    assert body["message"] == "密码长度必须在8到64位之间"


@pytest.mark.asyncio
async def test_incoming_request_id_is_echoed(client: AsyncClient):
    response = await client.post(
        "/api/auth/login",
        json={"account": "nobody@example.com", "password": "orange123"},
        headers={"X-Request-Id": "trace-abc-123456"},
    )
    assert response.headers["X-Request-Id"] == "trace-abc-123456"
    assert response.json()["traceId"] == "trace-abc-123456"


@pytest.mark.asyncio
async def test_login_and_refresh_via_http(client: AsyncClient):
    await client.post("/api/auth/register", json=REGISTER_BODY)
    login = await client.post(
        "/api/auth/login", json={"account": "api@example.com", "password": "orange123"}
    )
    assert login.status_code == 200
    tokens = login.json()["data"]
    assert tokens["tokenType"] == "Bearer"

    refreshed = await client.post(
        "/api/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["token"]
