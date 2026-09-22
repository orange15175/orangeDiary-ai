from app.prompts.system_prompt import build_memory_context, build_system_prompt


class ModelMessageFactory:
    """Build model input from trusted server memory and optional Java business facts."""

    @staticmethod
    def build(memory_context, current_message: str, user_context: dict | None = None) -> list[dict[str, str]]:
        """按系统约束、长期摘要、短期消息、当前问题的顺序构造模型输入。"""
        system = build_system_prompt(user_context) + "\n\n" + build_memory_context(
            memory_context.conversation.summary, ()
        )
        # 消息表只允许 user/assistant 角色，避免历史记录覆盖系统提示词。
        history = [
            {"role": message.role, "content": message.content}
            for message in memory_context.messages
        ]
        return [{"role": "system", "content": system}, *history, {"role": "user", "content": current_message}]
