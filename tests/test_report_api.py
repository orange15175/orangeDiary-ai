"""报告接口契约测试。

只验三件事：要登录、返回真 PDF、文件名是中文且用 `filename*` 编码。
内容正确性在 `test_diet_report.py` 里测过，不重复。
"""
from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_session_store, get_settings
from app.main import create_app
from app.models.diet import DietRecord
from app.reports.diet_report import REPORT_ZONE
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore


def build_settings() -> Settings:
    # 不给 DeepSeek key：报告要能在完全离线时生成，建议走固定文案。
    return Settings(
        deepseek_api_key="",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"report-api-secret-key-32-byte").decode("ascii"),
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
        yield http, session

    await session.close()
    await engine.dispose()


async def _login(http: AsyncClient) -> tuple[dict[str, str], int]:
    email = "report-api@example.com"
    await http.post(
        "/api/auth/register",
        json={
            "username": "报告橙子",
            "email": email,
            "password": "orange123",
            "confirmPassword": "orange123",
        },
    )
    tokens = (
        await http.post("/api/auth/login", json={"account": email, "password": "orange123"})
    ).json()["data"]
    headers = {"Authorization": f"Bearer {tokens['token']}"}
    me = await http.get("/api/users/me", headers=headers)
    return headers, me.json()["data"]["id"]


@pytest.mark.asyncio
async def test_report_requires_login(context):
    http, _session = context
    response = await http.get("/api/reports/diet/today.pdf")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_report_returns_pdf_with_chinese_filename(context):
    http, session = context
    headers, user_id = await _login(http)

    now = datetime.now(REPORT_ZONE)
    session.add(
        DietRecord(
            user_id=user_id,
            food_item_id=1,
            meal_type="lunch",
            food_name_snapshot="米饭",
            quantity_g=Decimal("150.00"),
            calorie_snapshot=Decimal("174.00"),
            protein_snapshot=Decimal("3.90"),
            fat_snapshot=Decimal("0.45"),
            carb_snapshot=Decimal("38.85"),
            record_time=now.replace(tzinfo=None),
            record_date=now.date(),
        )
    )
    await session.commit()

    response = await http.get("/api/reports/diet/today.pdf", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["cache-control"] == "private, no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # 中文文件名必须走 RFC 5987 的 filename*，否则浏览器会退化成乱码。
    assert "filename*=UTF-8''" in disposition
    assert response.content[:5] == b"%PDF-"
