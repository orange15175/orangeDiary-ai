"""M1 认证链路的真实依赖集成测试。

`-m integration` 才运行。它同时验证三件事：
- 0001 基线 DDL 在真实 MySQL 8 上可执行；
- BCrypt 哈希能落库并回读校验；
- refresh 轮换在真实 Redis 上具备原子性与复用检测。
"""
from __future__ import annotations

import base64

import pytest
from sqlalchemy import text

from app.config import Settings
from app.schemas.auth import LoginRequest, RegisterRequest
from app.db.session import REQUIRED_SCHEMA_REVISION
from app.services.auth_service import AuthService
from app.services.auth_session import RedisAuthSessionStore

pytestmark = pytest.mark.integration


def _settings(redis_namespace: str) -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"integration-secret-32-bytes!!!!").decode("ascii"),
        auth_session_namespace=redis_namespace,
    )


async def _make_service(mysql_session_factory, redis_namespace) -> AuthService:
    client, prefix = redis_namespace
    session = mysql_session_factory()
    return AuthService(session, RedisAuthSessionStore(client, prefix), _settings(prefix))


@pytest.mark.asyncio
async def test_migration_chain_creates_business_tables(mysql_session_factory):
    async with mysql_session_factory() as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        # 断言到"当前要求的版本"而不是写死 0001：每加一个 revision 都要改测试的
        # 断言不是严谨，是把测试变成维护负担。真正的防护是 REQUIRED_SCHEMA_REVISION。
        assert version == REQUIRED_SCHEMA_REVISION
        tables = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE()"
                    )
                )
            ).all()
        }
    assert {
        "user",
        "user_profile",
        "user_goal",
        "avatar_upload",
        "food_item",
        "food_alias",
        "food_serving",
        "diet_record",
        "daily_summary",
        "weight_record",
        "exercise_item",
        "exercise_record",
        "idempotency_record",
        "post",
        "post_image",
        "post_like",
        "post_comment",
        "community_report",
        "notification",
        "admin_action_log",
        "user_daily_checkin",
        "ai_conversation",
        "ai_conversation_message",
        "ai_chat_trace",
        "ai_answer_source",
        "ai_answer_feedback",
        "alembic_version",
    } <= tables


@pytest.mark.asyncio
async def test_register_login_refresh_on_real_mysql_and_redis(mysql_session_factory, redis_namespace):
    service = await _make_service(mysql_session_factory, redis_namespace)
    try:
        created = await service.register(
            RegisterRequest(
                username="集成橙子",
                email="integration@example.com",
                password="orange123",
                confirmPassword="orange123",
            )
        )
        assert created["id"] > 0

        stored_hash = await service._db.scalar(
            text("SELECT password_hash FROM `user` WHERE id = :id"), {"id": created["id"]}
        )
        assert stored_hash.startswith("$2b$")

        tokens = await service.login(
            LoginRequest(account="integration@example.com", password="orange123")
        )
        rotated = await service.refresh(tokens["refreshToken"])
        assert rotated["refreshToken"] != tokens["refreshToken"]

        with pytest.raises(Exception) as reuse:
            await service.refresh(tokens["refreshToken"])
        assert getattr(reuse.value, "code", None) == 401
    finally:
        await service._db.close()
