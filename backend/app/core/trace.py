"""请求级 traceId 上下文。

Java 侧用 `TraceIdContext` 在线程内传递，这里改成 contextvar：FastAPI 的异步任务
在切换协程时仍能读到同一个值，不需要把 traceId 一路当参数传。

响应外壳里必须带 traceId，否则前端排查"这次为什么 500"时无法与后端日志对齐。
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")

# 只接受安全字符，避免把客户端传来的换行/控制符写进日志（日志注入）。
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def normalize_trace_id(raw: str | None) -> str:
    """沿用调用方传入的 X-Request-Id，非法值一律丢弃并重新生成。"""
    if raw and _SAFE_TRACE_ID.match(raw):
        return raw
    return new_trace_id()


def set_trace_id(trace_id: str) -> None:
    _TRACE_ID.set(trace_id)


def get_trace_id() -> str:
    return _TRACE_ID.get()
