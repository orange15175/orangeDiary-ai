"""异步数据库会话。

只建一个全局 engine：FastAPI 的请求依赖按请求开 session，事务边界由服务层控制。
`expire_on_commit=False` 是因为响应序列化发生在 commit 之后，否则会触发已关闭
会话上的懒加载，报 `DetachedInstanceError`。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.dialects import registry
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings

# 覆盖 SQLAlchemy 内置的 mysql.asyncmy 方言：它继承 pymysql 的 do_ping，
# 会依据环境里是否装了 pymysql 决定要不要给 ping() 传参，装了 pymysql 1.1+ 时
# 与 asyncmy 适配器签名冲突（`ping(reconnect)` 无默认值）。原因见该模块注释。
# 注册必须早于 create_async_engine：URL 的 `mysql+asyncmy` 靠这张表解析方言。
registry.register("mysql.asyncmy", "app.db.asyncmy_dialect", "AsyncmyDialect")

# `/ready` 只认这两个条件：库能查、schema 到了这个版本。改基线时同步改这里，
# 否则新副本会带着"就绪"状态跑在不兼容的表结构上。
REQUIRED_SCHEMA_REVISION = "0010"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = Settings.from_env()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL 未配置，无法连接业务数据库")
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    """测试与优雅停机用；模块级单例不 dispose 会留下未关闭的连接。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def database_is_ready() -> bool:
    """就绪探针：能执行 SELECT 1 且 alembic 版本等于要求值。

    失败原因只写服务端日志，不返回给调用方——探测接口不该把内网地址和库名吐出去。
    """
    from sqlalchemy import text

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
            version = await session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        return False
    return version == REQUIRED_SCHEMA_REVISION
