from pydantic import BaseModel, Field


# 单条消息上限与 Java 侧 AiChatRequest(@Size 4000) 一致：
# 早先这里写 8000，前端也按 8000 放行，输入 4001~8000 字符时会被 Java 直接 400 打回。
class ChatMessage(BaseModel):
    """一条允许带入模型上下文的历史消息。"""
    # 只允许历史对话角色，防止客户端伪造 system 消息。
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # 当前先由客户端传最近消息；后续可以改为 Redis/数据库加载。
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ChatResponseData(BaseModel):
    reply: str
    model: str
    context_used: bool
    action: str | None = None
    recordPreview: dict | None = None
    exercisePreview: dict | None = None
