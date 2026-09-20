from functools import lru_cache

from app.config import Settings
from app.services.chat_service import ChatService


@lru_cache
def get_chat_service() -> ChatService:
    """Create one process-local chat service; request state never lives on the service instance."""
    return ChatService(Settings.from_env())
