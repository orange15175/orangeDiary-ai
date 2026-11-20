"""业务异常与统一响应外壳。

对齐 Java 侧 `ApiResponse`：成功 code=0，失败用 HTTP 状态码同值作为业务 code。
前端 `http.js` 只认 `code === 0`，所以这里不能改成 "code: 200" 之类的写法。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.trace import get_trace_id


class BusinessError(Exception):
    """可预期的业务失败：消息可直出给用户，堆栈只进服务端日志。

    `retry_after_seconds` 用于 429：Java 侧会写 `Retry-After` 响应头，
    前端据此显示"再等 N 秒"，比只说"稍后重试"可操作。
    """

    def __init__(self, code: int, message: str, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


def success(data: Any = None) -> dict[str, Any]:
    return {"code": 0, "message": "success", "data": data, "traceId": get_trace_id()}


def failure(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "data": None, "traceId": get_trace_id()}


async def business_error_handler(_request: Request, exc: BusinessError) -> JSONResponse:
    # 4xx 是用户输入问题，不写 error 级日志，避免正常业务被当成故障告警。
    headers = {}
    if exc.retry_after_seconds > 0:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=exc.code, content=failure(exc.code, exc.message), headers=headers or None
    )
