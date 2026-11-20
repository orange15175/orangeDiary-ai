"""认证服务的端到端单测（SQLite + 内存会话，不依赖 MySQL/Redis）。

覆盖的是"迁移语义"而不是实现细节：重复注册 409、密码错 401、禁用 403、
refresh 一次性轮换与复用检测。其中复用检测最关键——它决定 token 泄露后的爆炸半径。
"""
from __future__ import annotations

import base64

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.core.errors import BusinessError
from app.db.base import Base
from app.models.user import User, UserGoal, UserProfile
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore

JWT_SECRET = base64.b64encode(b"unit-test-secret-key-32-bytes!!").decode("ascii")


def build_settings() -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=JWT_SECRET,
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest_asyncio.fixture
async def service() -> AuthService:
    # 内存库每个连接是独立数据库，必须用 StaticPool 让所有会话共享同一个连接。
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield AuthService(session, InMemoryAuthSessionStore(), build_settings())
    await engine.dispose()


def register_payload(**overrides) -> RegisterRequest:
    data = {
        "username": "橙子",
        "email": "orange@example.com",
        "password": "orange123",
        "confirmPassword": "orange123",
    }
    data.update(overrides)
    return RegisterRequest(**data)


@pytest.mark.asyncio
async def test_register_returns_user_with_incomplete_profile(service: AuthService):
    user = await service.register(register_payload())
    assert user["email"] == "orange@example.com"
    assert user["nickname"] == "橙子"
    assert user["role"] == "user"
    assert user["status"] == "active"
    assert user["profileCompleted"] is False
    assert user["profile"] is None


@pytest.mark.asyncio
async def test_register_rejects_mismatched_password(service: AuthService):
    with pytest.raises(BusinessError) as exc:
        await service.register(register_payload(confirmPassword="orange124"))
    assert exc.value.code == 400


@pytest.mark.asyncio
async def test_register_twice_returns_409(service: AuthService):
    await service.register(register_payload())
    with pytest.raises(BusinessError) as exc:
        await service.register(register_payload())
    assert exc.value.code == 409
    assert exc.value.message == "邮箱已注册"


@pytest.mark.asyncio
async def test_login_with_email_and_mobile(service: AuthService):
    await service.register(register_payload())
    tokens = await service.login(LoginRequest(account="orange@example.com", password="orange123"))
    assert tokens["tokenType"] == "Bearer"
    assert tokens["expiresIn"] == 1800
    assert "." in tokens["refreshToken"]
    assert len(tokens["refreshToken"].split(".", 1)[1]) >= 32


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(service: AuthService):
    await service.register(register_payload())
    with pytest.raises(BusinessError) as exc:
        await service.login(LoginRequest(account="orange@example.com", password="wrong-pass"))
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_login_disabled_account_returns_403(service: AuthService):
    user = await service.register(register_payload())
    db_user = await service._db.get(User, user["id"])
    db_user.status = "disabled"
    await service._db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.login(LoginRequest(account="orange@example.com", password="orange123"))
    assert exc.value.code == 403


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_detects_reuse(service: AuthService):
    await service.register(register_payload())
    first = await service.login(LoginRequest(account="orange@example.com", password="orange123"))

    second = await service.refresh(first["refreshToken"])
    assert second["refreshToken"] != first["refreshToken"]

    # 再用第一次的 refresh token：命中 previousRefreshHash，会话应被整体销毁。
    with pytest.raises(BusinessError) as reuse:
        await service.refresh(first["refreshToken"])
    assert reuse.value.code == 401

    # 会话已删除，连第二次拿到的合法 token 也不能再用。
    with pytest.raises(BusinessError):
        await service.refresh(second["refreshToken"])


@pytest.mark.asyncio
async def test_logout_invalidates_session(service: AuthService):
    await service.register(register_payload())
    tokens = await service.login(LoginRequest(account="orange@example.com", password="orange123"))
    await service.logout(tokens["refreshToken"])
    with pytest.raises(BusinessError) as exc:
        await service.refresh(tokens["refreshToken"])
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_malformed_token(service: AuthService):
    with pytest.raises(BusinessError) as exc:
        await service.refresh("not-a-valid-token")
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_profile_completed_requires_profile_and_active_goal(service: AuthService):
    created = await service.register(register_payload())
    user_id = created["id"]

    # 只有档案没有目标：Java 侧也判为未完成。
    service._db.add(UserProfile(user_id=user_id, gender="female", height_cm=165, current_weight_kg=55))
    await service._db.commit()
    user = await service._db.get(User, user_id)
    assert (await service._user_payload(user))["profileCompleted"] is False

    from datetime import date

    service._db.add(
        UserGoal(user_id=user_id, goal_type="lose", start_date=date(2026, 11, 18), status="active")
    )
    await service._db.commit()
    assert (await service._user_payload(user))["profileCompleted"] is True
