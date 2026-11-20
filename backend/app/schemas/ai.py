"""AI 聊天请求体（对齐 `AiChatRequest`）。

校验文案是中文原文：前端直接展示 `message` 字段，混进 Pydantic 的
"String should have at most 4000 characters" 会让用户看不懂。
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator


class AiChatRequest(BaseModel):
    message: str
    # 已废弃：当前每个用户只有一个默认会话，后端会忽略该字段。
    # 保留解析是为了不让老版本前端（仍带这个字段）被 400 挡掉。
    conversationId: str | None = None

    @field_validator("message")
    @classmethod
    def _check_message(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("消息内容不能为空")
        if len(value) > 4000:
            raise ValueError("消息内容不能超过4000个字符")
        return value

    @field_validator("conversationId")
    @classmethod
    def _check_conversation_id(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 36:
            raise ValueError("会话ID不能超过36个字符")
        return value


class AiFeedbackRequest(BaseModel):
    """赞踩提交体。`rating` 为 null 表示取消，不新增一个"无感"状态。

    取值校验放在服务层，让非法值走统一的 400 中文错误壳；放在这里会变成
    Pydantic 的 422，前端会当系统错误弹窗。
    """

    rating: str | None = None
