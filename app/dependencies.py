from functools import lru_cache

from app.config import Settings
from app.services.chat_service import ChatService


@lru_cache
def get_chat_service() -> ChatService:
    """Create one process-local chat service; request state never lives on the service instance."""
    # 依赖注入缓存共享客户端，单次请求状态仍放在 ChatExecutionContext。
    return ChatService(Settings.from_env())
