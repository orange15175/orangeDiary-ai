from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """一条允许带入模型上下文的历史消息。"""
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    # 当前先由客户端传最近消息；后续可以改为 Redis/数据库加载。
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ChatResponseData(BaseModel):
    reply: str
    model: str
    context_used: bool
    action: str | None = None
    recordPreview: dict | None = None
