import logging
import json
import time
import uuid

from app.clients.deepseek_client import DeepSeekClient
from app.clients.java_backend import JavaBackendClient
from app.config import Settings
from app.prompts.system_prompt import build_system_prompt
from app.schemas.chat import ChatRequest, ChatResponseData

logger = logging.getLogger("orange-diary-ai")


class ChatService:
    """编排一次聊天：读取上下文、组装消息、调用模型。"""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.java_backend = JavaBackendClient(settings)
        self.deepseek = DeepSeekClient(settings)

    async def chat(self, request: ChatRequest, authorization: str | None) -> ChatResponseData:
        trace_id = uuid.uuid4().hex
        total_start = time.perf_counter()
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        context_start = time.perf_counter()
        user_context = await self.java_backend.load_current_user_context(authorization)
        context_duration_ms = round(
            (time.perf_counter() - context_start) * 1000
        )
        messages = [{"role": "system", "content": build_system_prompt(user_context)}]
        messages.extend(message.model_dump() for message in request.history)
        messages.append({"role": "user", "content": request.message})
        model_start = time.perf_counter()
        model_result = await self.deepseek.chat(messages)
        model_duration_ms = round(
            (time.perf_counter() - model_start) * 1000
        )

        estimated_cost = (
                model_result.prompt_tokens
                * self.settings.deepseek_input_price_per_million
                / 1_000_000
                + model_result.completion_tokens
                * self.settings.deepseek_output_price_per_million
                / 1_000_000
        )
        log_data = {
            "追踪编号": trace_id,
            "用户问题": request.message,
            "是否读取用户上下文": bool(user_context),
            "用户上下文": user_context,
            "调用工具": [
                {
                    "工具名称": "load_current_user_context",
                    "耗时毫秒": context_duration_ms,
                    "状态": "成功",
                }
            ],
            "模型": self.settings.deepseek_model,
            "输入Token数": model_result.prompt_tokens,
            "输出Token数": model_result.completion_tokens,
            "总Token数": model_result.total_tokens,
            "预估成本": round(estimated_cost, 8),
            "模型调用耗时毫秒": model_duration_ms,
            "总耗时毫秒": round(
                (time.perf_counter() - total_start) * 1000
            ),
            "AI输出": model_result.reply,
            "状态": "成功",
        }

        logger.info(
            "AI_CALL\n%s",
            json.dumps(log_data, ensure_ascii=False, indent=2),
        )
        return ChatResponseData(reply=model_result.reply, model=self.settings.deepseek_model, context_used=bool(user_context))
