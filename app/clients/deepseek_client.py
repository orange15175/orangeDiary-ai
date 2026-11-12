from dataclasses import dataclass

from app.config import Settings
from app.services.model_gateway import ModelGateway


@dataclass(frozen=True)
class ModelCallResult:
    reply: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class DeepSeekClient:
    """Compatibility adapter for the old semantic parser over the unified gateway."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.gateway = ModelGateway(settings)

    async def chat(self, messages: list[dict], json_mode: bool = False) -> ModelCallResult:
        body = {"model": "intent-classification", "messages": messages}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        result = await self.gateway.execute(body, "intent-parser")
        choice = result["choices"][0]
        message = choice.get("message") or {}
        return ModelCallResult(
            reply=(message.get("content") or "").strip(),
            prompt_tokens=(result.get("usage") or {}).get("prompt_tokens", 0),
            completion_tokens=(result.get("usage") or {}).get("completion_tokens", 0),
            total_tokens=(result.get("usage") or {}).get("total_tokens", 0),
        )
