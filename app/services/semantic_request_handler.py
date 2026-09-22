"""Safely turn model classification into a bounded query, preview, or memory chat."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from app.services.intent_result import IntentResult
from app.services.intent_router import IntentMatch
from app.services.model_message_factory import ModelMessageFactory


# The model never supplies a callable function name. Code maps an approved intent to an approved tool.
SEMANTIC_TOOL_NAMES = {
    "daily_summary": "get_today_summary",
    "diet_detail": "get_today_diet_detail",
    "food_nutrition": "search_food_nutrition",
    "weight_progress": "get_weight_progress",
    "user_goal": "get_user_goal",
    "exercise_calorie": "search_exercise_calorie",
}
SEMANTIC_REQUIRED_SLOTS = {
    "food_nutrition": ("food_name",),
    "exercise_calorie": ("exercise_name", "duration_min"),
}


class SemanticRequestHandler:
    """Owns the model-assisted branch after deterministic rules cannot classify a message."""

    def __init__(self, settings, deepseek, semantic_parser, tool_executor, record_previews, responses):
        """组合语义分支的解析、工具、预览和响应依赖。"""
        self.settings = settings
        self.deepseek = deepseek
        self.semantic_parser = semantic_parser
        self.tool_executor = tool_executor
        self.record_previews = record_previews
        self.responses = responses

    async def handle(self, trace_id, total_start, message, authorization, memory_context):
        """处理规则未命中的消息：追问、预览、查询或普通聊天。"""
        try:
            semantic, classifier_result = await self.semantic_parser.parse(message)
        except ValueError:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], "我没有完全理解你的意思，可以换一种更具体的说法吗？"
            )

        canonical = IntentResult.from_semantic(semantic)
        # Low-confidence classification must not read private data or create a record preview.
        if semantic.intent == "clarification" or semantic.confidence < 0.70:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], semantic.reply or "我没有完全理解你的意思，可以换一种更具体的说法吗？",
                source=canonical.source, confidence=canonical.confidence,
            )
        if semantic.intent == "food_record":
            return await self.record_previews.build_diet_preview(
                trace_id, total_start, message, authorization, semantic, classifier_result
            )
        if semantic.intent == "exercise_record":
            return await self.record_previews.build_exercise_preview(
                trace_id, total_start, message, authorization, semantic, classifier_result
            )
        if canonical.behavior == "query":
            return await self._handle_query(trace_id, total_start, message, authorization, canonical, memory_context)
        return await self._handle_memory_chat(trace_id, total_start, message, canonical, memory_context)

    async def _handle_query(self, trace_id, total_start, message, authorization, canonical, memory_context):
        """校验模型参数后，通过白名单工具读取一次用户数据。"""
        missing = self._missing_slots(canonical)
        if missing:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], self._missing_slot_reply(missing),
                source=canonical.source, confidence=canonical.confidence,
            )
        intent = self._to_tool_intent(canonical)
        if intent is None:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], "我暂时无法确定该查询需要读取哪些数据，请换一种更具体的说法。",
                source=canonical.source, confidence=canonical.confidence,
            )
        try:
            tool_result = await self.tool_executor.execute(intent, authorization)
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], "暂时无法读取你的健康数据，请稍后再试。",
                source=canonical.source, confidence=canonical.confidence,
                tool_calls=[{"工具名称": intent.tool_name, "状态": "失败"}],
            )
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        user_context = {"date": date.today().isoformat(), "data": {intent.name: tool_result}}
        model_result = await self.deepseek.chat(ModelMessageFactory.build(memory_context, message, user_context))
        return self.responses.model_response(
            trace_id, total_start, message, [canonical.intent], model_result, model_result.reply,
            "semantic_tool_context", [canonical.behavior], source=canonical.source,
            confidence=canonical.confidence, tool_calls=[{"工具名称": intent.tool_name, "状态": "成功"}],
            context_used=True,
        )

    async def _handle_memory_chat(self, trace_id, total_start, message, canonical, memory_context):
        """普通聊天只使用持久记忆，不读取实时健康数据。"""
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        model_result = await self.deepseek.chat(ModelMessageFactory.build(memory_context, message))
        return self.responses.model_response(
            trace_id, total_start, message, [canonical.intent], model_result, model_result.reply,
            "memory_chat", ["chat"], source=canonical.source, confidence=canonical.confidence,
            context_used=False,
        )

    @staticmethod
    def _missing_slots(canonical: IntentResult) -> tuple[str, ...]:
        """合并模型声明和本地规则要求的缺失参数。"""
        missing = set(canonical.missing_slots)
        for slot in SEMANTIC_REQUIRED_SLOTS.get(canonical.intent, ()):
            if not canonical.slots.get(slot):
                missing.add(slot)
        return tuple(sorted(missing))

    @staticmethod
    def _missing_slot_reply(missing: tuple[str, ...]) -> str:
        """将缺失参数转换为下一句明确追问。"""
        if "food_name" in missing:
            return "你想查询哪一种食物的营养信息？"
        if "exercise_name" in missing:
            return "你想查询哪一种运动的热量消耗？"
        if "duration_min" in missing:
            return "你大约进行了多少分钟？"
        return "请补充更具体的信息，我才能准确查询。"

    @staticmethod
    def _to_tool_intent(canonical: IntentResult) -> IntentMatch | None:
        """把语义意图映射为本地允许的工具和参数。"""
        tool_name = SEMANTIC_TOOL_NAMES.get(canonical.intent)
        if tool_name is None:
            return None
        allowed_slots = {
            "food_nutrition": ("food_name", "amount_g", "nutrition_metric"),
            "exercise_calorie": ("exercise_name", "duration_min"),
            "daily_summary": ("metrics",),
        }
        params = {
            key: value for key, value in canonical.slots.items()
            if key in allowed_slots.get(canonical.intent, ())
        }
        # Drop every model-produced field outside the selected tool's published parameter list.
        return IntentMatch(canonical.intent, tool_name, 0, None, params)
