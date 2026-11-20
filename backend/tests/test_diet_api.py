"""饮食记录 HTTP 契约测试。

从注册开始一路走到"记一条午餐"，只把库换成 SQLite——为了验证真实链路：
JWT → 档案闸门(428) → 食物校验(404) → 快照计算 → 汇总刷新 → 幂等重放。
"""
from __future__ import annotations

import base64
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
from app.models.food import FoodItem
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore

REQUEST_ID = "diet-api-test-0001"


def build_settings() -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"diet-api-secret-key-32-bytes!").decode("ascii"),
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

    rice = FoodItem(
        name="米饭",
        category="谷物",
        unit="g",
        calorie_per_100g=Decimal("116.00"),
        protein_per_100g=Decimal("2.60"),
        fat_per_100g=Decimal("0.30"),
        carb_per_100g=Decimal("25.90"),
        status="active",
    )
    session.add(rice)
    await session.commit()

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_store] = lambda: store

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, rice.id

    await session.close()
    await engine.dispose()


async def _headers(http: AsyncClient, complete_profile: bool = True) -> dict[str, str]:
    body = {
        "username": "饮食橙子",
        "email": "diet-api@example.com",
        "password": "orange123",
        "confirmPassword": "orange123",
    }
    await http.post("/api/auth/register", json=body)
    tokens = (
        await http.post(
            "/api/auth/login", json={"account": "diet-api@example.com", "password": "orange123"}
        )
    ).json()["data"]
    headers = {"Authorization": f"Bearer {tokens['token']}"}

    if complete_profile:
        await http.put(
            "/api/users/profile",
            headers=headers,
            json={"gender": "female", "heightCm": 165, "currentWeightKg": 55, "targetWeightKg": 52},
        )
        await http.post(
            "/api/users/goals",
            headers=headers,
            json={"goalType": "lose", "dailyIntakeTarget": 1600, "startDate": "2026-11-01"},
        )
    return headers


def _body(food_id: int, **overrides) -> dict:
    body = {
        "foodItemId": food_id,
        "mealType": "lunch",
        "quantityG": 150,
        "recordTime": "2026-11-18T12:30:00",
        "remark": "午饭",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_missing_request_id_returns_400(context):
    http, food_id = context
    headers = await _headers(http)

    response = await http.post(
        "/api/diet-records", headers=headers, json=_body(food_id)
    )
    assert response.status_code == 400
    assert response.json()["message"] == "X-Request-Id格式不合法"


@pytest.mark.asyncio
async def test_incomplete_profile_returns_428(context):
    http, food_id = context
    headers = await _headers(http, complete_profile=False)

    response = await http.post(
        "/api/diet-records",
        headers={**headers, "X-Request-Id": REQUEST_ID},
        json=_body(food_id),
    )
    assert response.status_code == 428
    assert response.json()["message"] == (
        "Please complete gender, height, current weight, and goal before recording data"
    )


@pytest.mark.asyncio
async def test_create_and_list_record(context):
    http, food_id = context
    headers = await _headers(http)

    created = await http.post(
        "/api/diet-records",
        headers={**headers, "X-Request-Id": REQUEST_ID},
        json=_body(food_id),
    )
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["foodNameSnapshot"] == "米饭"
    assert float(data["calorieSnapshot"]) == 174.0
    assert data["recordDate"] == "2026-11-18"
    assert data["foodCategory"] == "谷物"

    listed = await http.get(
        "/api/diet-records", headers=headers, params={"date": "2026-11-18"}
    )
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == data["id"]


@pytest.mark.asyncio
async def test_repeated_request_id_is_idempotent(context):
    http, food_id = context
    headers = await _headers(http)
    request_headers = {**headers, "X-Request-Id": REQUEST_ID}

    first = await http.post("/api/diet-records", headers=request_headers, json=_body(food_id))
    second = await http.post("/api/diet-records", headers=request_headers, json=_body(food_id))

    assert first.json()["data"]["id"] == second.json()["data"]["id"]

    listed = await http.get(
        "/api/diet-records", headers=headers, params={"date": "2026-11-18"}
    )
    assert len(listed.json()["data"]) == 1


@pytest.mark.asyncio
async def test_same_request_id_different_body_returns_409(context):
    http, food_id = context
    headers = await _headers(http)
    request_headers = {**headers, "X-Request-Id": REQUEST_ID}

    await http.post("/api/diet-records", headers=request_headers, json=_body(food_id))
    conflict = await http.post(
        "/api/diet-records", headers=request_headers, json=_body(food_id, quantityG=200)
    )
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "requestId与请求内容不一致"


@pytest.mark.asyncio
async def test_unknown_food_returns_404(context):
    http, _ = context
    headers = await _headers(http)

    response = await http.post(
        "/api/diet-records",
        headers={**headers, "X-Request-Id": REQUEST_ID},
        json=_body(999999),
    )
    assert response.status_code == 404
    assert response.json()["message"] == "食物不存在或已停用"


@pytest.mark.asyncio
async def test_delete_record(context):
    http, food_id = context
    headers = await _headers(http)
    created = await http.post(
        "/api/diet-records",
        headers={**headers, "X-Request-Id": REQUEST_ID},
        json=_body(food_id),
    )
    record_id = created.json()["data"]["id"]

    deleted = await http.delete(f"/api/diet-records/{record_id}", headers=headers)
    assert deleted.status_code == 200

    listed = await http.get(
        "/api/diet-records", headers=headers, params={"date": "2026-11-18"}
    )
    assert listed.json()["data"] == []


@pytest.mark.asyncio
async def test_list_requires_date(context):
    http, _ = context
    headers = await _headers(http)
    response = await http.get("/api/diet-records", headers=headers)
    assert response.status_code == 400
    # Pydantic 的 "Field required" 不能直出，统一成中文兜底文案。
    assert response.json()["message"] == "请求参数不合法"


@pytest.mark.asyncio
async def test_meal_type_validation_message(context):
    http, food_id = context
    headers = await _headers(http)

    response = await http.post(
        "/api/diet-records",
        headers={**headers, "X-Request-Id": REQUEST_ID},
        json=_body(food_id, mealType="brunch"),
    )
    assert response.status_code == 400
    assert response.json()["message"] == "餐别只能是breakfast、lunch、dinner或snack"


@pytest.mark.asyncio
async def test_diet_requires_auth(context):
    http, food_id = context
    response = await http.post(
        "/api/diet-records", headers={"X-Request-Id": REQUEST_ID}, json=_body(food_id)
    )
    assert response.status_code == 401
    assert response.json()["message"] == "请先登录"
