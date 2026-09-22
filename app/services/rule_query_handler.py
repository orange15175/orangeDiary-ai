"""Run rule-confirmed read-only queries and let the model phrase trusted results."""

from __future__ import annotations

import asyncio
from datetime import date

from app.services.model_message_factory import ModelMessageFactory


class RuleQueryHandler:
    """Handles only rule-confirmed queries; action previews and semantic fallback stay elsewhere."""

    def __init__(self, settings, deepseek, tool_executor, responses):
        """组合规则查询需要的工具、模型和响应构造器。"""
        self.settings = settings
        self.deepseek = deepseek
        self.tool_executor = tool_executor
        self.responses = responses

    async def handle(self, trace_id, total_start, message, authorization, intents, request_plan, memory_context):
        """并行读取规则确认的数据，再让模型基于真实结果组织回答。"""
        # Independent read-only tools can run concurrently. No action endpoint is included in this handler.
        values = await asyncio.gather(*(self.tool_executor.execute(intent, authorization) for intent in intents))
        tool_results = {intent.name: value for intent, value in zip(intents, values)}
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        user_context = {"date": date.today().isoformat(), "data": tool_results}
        model_result = await self.deepseek.chat(ModelMessageFactory.build(memory_context, message, user_context))
        return self.responses.model_response(
            trace_id, total_start, message, [intent.name for intent in intents], model_result,
            model_result.reply, "rule_tool_context", [behavior.value for behavior in request_plan.behaviors],
            source="rule", confidence=1.0,
            tool_calls=[{"工具名称": intent.tool_name, "状态": "成功"} for intent in intents],
            context_used=True,
        )
