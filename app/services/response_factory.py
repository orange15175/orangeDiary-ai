"""Keep API responses and AI business logs consistent across request handlers."""

from __future__ import annotations

import json
import logging
import time

from app.schemas.chat import ChatResponseData
from app.services.request_plan import RequestPlanBuilder

logger = logging.getLogger("orange-diary-ai")


class ChatResponseFactory:
    """Creates the existing response contract and records one structured trace per request."""

    def __init__(self, settings):
        """保存模型与计费配置，用于统一写入请求日志。"""
        self.settings = settings

    def rule_based_response(
            self, trace_id: str, total_start: float, question: str, intents, reply: str,
            tool_results: dict[str, dict] | None = None, source: str | None = None,
            confidence: float | None = None, tool_calls: list[dict] | None = None,
            action: str | None = None, record_preview: dict | None = None,
            exercise_preview: dict | None = None,
    ) -> ChatResponseData:
        """构造追问和安全兜底响应，并记录未调用或失败工具。"""
        # Clarifications and safe failures share one response shape so the frontend never needs special cases.
        request_plan = RequestPlanBuilder().build(intents)
        log_data = {
            "追踪编号": trace_id,
            "用户问题": question,
            "识别意图": [intent.name for intent in intents],
            "行为": [behavior.value for behavior in request_plan.behaviors],
            "请求模式": request_plan.mode,
            "识别来源": source,
            "置信度": confidence,
            "调用工具": tool_calls or (
                [{"工具名称": intent.tool_name, "状态": "未调用"} for intent in intents]
                if not tool_results else [{"工具名称": name, "状态": "成功"} for name in tool_results]
            ),
            "用户上下文": tool_results or {},
            "模型": "rule-based",
            "总耗时毫秒": round((time.perf_counter() - total_start) * 1000),
            "AI输出": reply,
            "状态": "成功",
        }
        logger.info("AI_CALL\n%s", json.dumps(log_data, ensure_ascii=False, indent=2))
        return ChatResponseData(
            reply=reply,
            model="rule-based",
            context_used=bool(tool_results),
            action=action,
            recordPreview=record_preview,
            exercisePreview=exercise_preview,
        )

    def model_response(
            self, trace_id: str, total_start: float, message: str, intents: list[str],
            model_result, reply: str, flow: str, behaviors: list[str] | None = None,
            source: str | None = None, confidence: float | None = None,
            tool_calls: list[dict] | None = None, context_used: bool = False,
            action: str | None = None, record_preview: dict | None = None,
            exercise_preview: dict | None = None,
    ) -> ChatResponseData:
        """构造模型响应并记录 Token、耗时、流程和工具结果。"""
        # Token and latency fields stay centralized, which keeps traces comparable after refactoring.
        estimated_cost = (
            model_result.prompt_tokens * self.settings.deepseek_input_price_per_million / 1_000_000
            + model_result.completion_tokens * self.settings.deepseek_output_price_per_million / 1_000_000
        )
        logger.info("AI_CALL\n%s", json.dumps({
            "追踪编号": trace_id,
            "用户问题": message,
            "识别意图": intents,
            "行为": behaviors or [],
            "请求模式": "+".join(behaviors or []),
            "识别来源": source,
            "置信度": confidence,
            "处理流程": flow,
            "调用工具": tool_calls or [],
            "模型": self.settings.deepseek_model,
            "输入Token数": model_result.prompt_tokens,
            "输出Token数": model_result.completion_tokens,
            "总Token数": model_result.total_tokens,
            "预估成本": round(estimated_cost, 8),
            "总耗时毫秒": round((time.perf_counter() - total_start) * 1000),
            "AI输出": reply,
            "状态": "成功",
        }, ensure_ascii=False, indent=2))
        return ChatResponseData(
            reply=reply,
            model=self.settings.deepseek_model,
            context_used=context_used,
            action=action,
            recordPreview=record_preview,
            exercisePreview=exercise_preview,
        )
