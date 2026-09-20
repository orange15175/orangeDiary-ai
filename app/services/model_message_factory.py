from app.prompts.system_prompt import build_memory_context, build_system_prompt


class ModelMessageFactory:
    """Build model input from trusted server memory and optional Java business facts."""

    @staticmethod
    def build(memory_context, current_message: str, user_context: dict | None = None) -> list[dict[str, str]]:
        system = build_system_prompt(user_context) + "\n\n" + build_memory_context(
            memory_context.conversation.summary, ()
        )
        history = [
            {"role": message.role, "content": message.content}
            for message in memory_context.messages
        ]
        return [{"role": "system", "content": system}, *history, {"role": "user", "content": current_message}]
