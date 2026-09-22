from dataclasses import dataclass
import time
import uuid

from app.schemas.chat import ChatRequest
from app.services.memory_service import MemoryContext


@dataclass(frozen=True)
class ChatExecutionContext:
    """Values shared by one authenticated chat request and its handlers."""

    request: ChatRequest
    authorization: str
    user_id: int
    memory_context: MemoryContext
    trace_id: str
    started_at: float

    @classmethod
    def create(cls, request: ChatRequest, authorization: str, user_id: int, memory_context: MemoryContext):
        """封装单次请求的共享数据、追踪编号和计时起点。"""
        return cls(request, authorization, user_id, memory_context, uuid.uuid4().hex, time.perf_counter())
