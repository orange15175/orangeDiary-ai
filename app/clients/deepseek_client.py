from dataclasses import dataclass
import logging

import httpx

from app.config import Settings

logger = logging.getLogger("orange-diary-ai")

@dataclass
class ModelCallResult:
    reply: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class DeepSeekClient:
    """只负责 DeepSeek HTTP 调用，不承担聊天业务流程。"""
    def __init__(self, settings: Settings):
        self.settings = settings

    async def chat(self, messages: list[dict], json_mode: bool = False) -> ModelCallResult:
        payload = {"model": self.settings.deepseek_model, "messages": messages, "temperature":0.6, "stream": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.deepseek_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.deepseek_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
            response.raise_for_status()
            body = response.json()
            reply = body["choices"][0]["message"]["content"].strip()
            usage = body.get("usage") or {}

            if not reply:
                raise ValueError("DeepSeek 返回了空回答")
            return ModelCallResult(
                reply=reply,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )
        except httpx.HTTPStatusError as exc:
            logger.exception("DeepSeek returned HTTP %s", exc.response.status_code)
            raise RuntimeError("DeepSeek 服务调用失败") from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.exception("DeepSeek request failed")
            raise RuntimeError("AI 服务调用失败") from exc
