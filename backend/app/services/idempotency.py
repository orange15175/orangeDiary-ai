"""写操作幂等（对齐 `IdempotencyServiceImpl` 的对外语义）。

Java 实现是"Redis 抢锁 + MySQL 唯一键兜底 + 事务内执行"。这里用
"MySQL 唯一键占位 + 分两段提交"，原因有两条，都不是偷懒：

1. **不用 Redis 抢锁**：唯一键本身就是 at-most-once 的裁判。加一层 Redis 只会
   多出"Redis 正常但事务回滚""Redis 过期但 MySQL 仍处理中"两类不一致。
2. **不用 SAVEPOINT 包插入**：pysqlite 的隐式事务会让 SAVEPOINT 直接 COMMIT
   外层事务，单测里"失败回滚"根本回滚不掉（实测踩过）。两段式在 MySQL 与
   SQLite 行为一致，测试才代表生产。

代价是"占位已提交、业务未完成"的窗口。用 60s 的 PROCESSING 过期判定兜住：
超过 60s 仍是 PROCESSING，视为进程崩溃留下的占位，删除后允许重新执行。
Java 用事务回滚避开这个窗口，但它的 Redis 锁也有同样的 60s TTL，对外等价。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.health import IdempotencyRecord

# 与 Java 的 `[A-Za-z0-9-]{16,64}` 一致：UUID 36 位、去掉连字符 32 位都合法。
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{16,64}$")

SUCCEEDED = "SUCCEEDED"
PROCESSING = "PROCESSING"

_RETENTION = timedelta(days=7)
# 与 Java `PROCESSING_TTL` 同值：超过这个时间仍是 PROCESSING，判为崩溃残留。
_PROCESSING_STALE = timedelta(seconds=60)
_PURGE_INTERVAL_SECONDS = 3600
_last_purge_at = 0.0


def validate_request_id(request_id: str | None) -> str:
    if not request_id or not _REQUEST_ID_PATTERN.match(request_id):
        raise BusinessError(400, "X-Request-Id格式不合法")
    return request_id


def _json_default(value: Any) -> Any:
    # Decimal 必须落成 JSON 数字：落成字符串会让前端拿到 "174.00"，
    # 之后的求和/绘图全部退化成字符串拼接。
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def payload_hash(payload: Any) -> str:
    """规范化 JSON 再取摘要：字段顺序与空白不影响结果。"""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def execute(
    db: AsyncSession,
    *,
    user_id: int,
    operation: str,
    request_id: str | None,
    payload: Any,
    action: Callable[[], Awaitable[Any]],
) -> Any:
    request_id = validate_request_id(request_id)
    digest = payload_hash(payload)

    existing = await _find(db, user_id, operation, request_id)
    if existing is not None:
        if _is_stale_processing(existing):
            await _release(db, existing.id)
        else:
            return _replay(existing, digest)

    await _maybe_purge(db)

    pending = IdempotencyRecord(
        user_id=user_id,
        operation=operation,
        request_id=request_id,
        request_payload_hash=digest,
        status=PROCESSING,
        created_at=datetime.now(),
        expires_at=datetime.now() + _RETENTION,
    )
    db.add(pending)
    try:
        # 先提交占位：并发同 requestId 的第二条请求会在唯一键上失败，转去重放。
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _find(db, user_id, operation, request_id)
        if existing is None:
            raise BusinessError(409, "请求正在处理中，请稍后重试") from None
        return _replay(existing, digest)

    try:
        result = await action()
    except BaseException:
        # 业务失败：把占位删掉，让用户改完参数能立即用同一个 requestId 重试。
        pending_id = pending.id
        await db.rollback()
        await _release(db, pending_id)
        raise

    encoded = json.dumps(result, ensure_ascii=False, default=_json_default)
    pending.status = SUCCEEDED
    pending.response_json = encoded
    await db.commit()
    # 首次请求也走一遍反序列化，保证"首次响应"与"重放响应"逐字段一致
    #（Decimal → float 的转换只发生一次，形状完全相同）。
    return json.loads(encoded)


def _is_stale_processing(record: IdempotencyRecord) -> bool:
    return record.status == PROCESSING and record.created_at is not None and (
        record.created_at < datetime.now() - _PROCESSING_STALE
    )


async def _release(db: AsyncSession, record_id: int | None) -> None:
    """删掉自己占的位；只删 PROCESSING，避免误删别人刚写入的成功结果。"""
    if record_id is None:
        return
    try:
        await db.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.status == PROCESSING,
            )
        )
        await db.commit()
    except Exception:  # pragma: no cover - 释放失败不应掩盖原始异常
        await db.rollback()


async def _maybe_purge(db: AsyncSession) -> None:
    """每小时最多清一次过期占位。没有调度进程时，写入路径是唯一可靠的触发点。"""
    global _last_purge_at
    now = time.monotonic()
    if now - _last_purge_at < _PURGE_INTERVAL_SECONDS:
        return
    _last_purge_at = now
    try:
        await db.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < datetime.now())
        )
        await db.commit()
    except Exception:  # pragma: no cover - 清理失败不影响主流程
        await db.rollback()


async def _find(
    db: AsyncSession, user_id: int, operation: str, request_id: str
) -> IdempotencyRecord | None:
    return await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.request_id == request_id,
        )
    )


def _replay(record: IdempotencyRecord, digest: str) -> Any:
    if record.request_payload_hash != digest:
        raise BusinessError(409, "requestId与请求内容不一致")
    if record.status != SUCCEEDED or record.response_json is None:
        raise BusinessError(409, "请求正在处理中，请稍后重试")
    return json.loads(record.response_json)
