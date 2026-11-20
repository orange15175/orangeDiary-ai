"""集成测试夹具：真实 MySQL + Redis，用完不留数据。

三条硬规则（与 deploy/README.md 一致）：
1. `APP_ENV` 必须是 `test`，且库名必须 `orange_diary_test_` 前缀，否则拒绝建库；
2. `TEST_DATABASE_URL` 缺失时 skip，并明确报"集成验收未完成"，不静默连业务库；
3. 只删自己建的临时 key 前缀，不对 Redis 做 FLUSHDB——测试实例与开发实例常常共用。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "").strip()
BOOTSTRAP_DB = os.environ.get("TEST_MYSQL_BOOTSTRAP_DB", "orange_diary_test_bootstrap")

_SKIP_REASON = (
    "集成验收未完成：未设置 TEST_DATABASE_URL。"
    "先运行 `docker compose -f deploy/compose.test.yml up -d --wait`"
)


def _require_safe_target() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip(_SKIP_REASON)
    if os.environ.get("APP_ENV") != "test":
        pytest.fail("集成测试必须设置 APP_ENV=test，防止误连业务库")
    database = make_url(TEST_DATABASE_URL).database or ""
    if not database.startswith("orange_diary_test_"):
        pytest.fail(f"测试库名必须以 orange_diary_test_ 开头，当前是 {database!r}")
    return database


async def _recreate_database(database: str) -> None:
    url = make_url(TEST_DATABASE_URL).set(database=BOOTSTRAP_DB)
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
        await conn.execute(
            text(
                f"CREATE DATABASE `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    await engine.dispose()


def _run_migrations() -> None:
    # env.py 从 Settings 读 DATABASE_URL，因此必须在调用前写入进程环境。
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def mysql_session_factory():
    database = _require_safe_target()
    await _recreate_database(database)
    # 迁移在独立线程里跑：alembic 的 env.py 自己会 asyncio.run，不能嵌在当前事件循环里。
    await asyncio.to_thread(_run_migrations)

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def redis_namespace():
    if not TEST_REDIS_URL:
        pytest.skip(_SKIP_REASON)
    prefix = f"auth:test:{uuid.uuid4().hex}:"
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - 环境问题不是测试失败
        await client.aclose()
        pytest.skip(f"Redis 不可用：{exc}")
    try:
        yield client, prefix
    finally:
        keys = [key async for key in client.scan_iter(f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
