import logging
import json
import time
import uuid
import asyncio
from datetime import datetime, date

from fastapi import HTTPException

from app.clients.deepseek_client import DeepSeekClient
from app.clients.java_backend import JavaBackendClient
from app.config import Settings
from app.prompts.system_prompt import build_system_prompt
from app.schemas.chat import ChatRequest, ChatResponseData
from app.services.intent_router import IntentMatch, build_clarification, detect_intents
from app.services.semantic_parser import SemanticParser, SemanticResult

logger = logging.getLogger("orange-diary-ai")


class ChatService:
    """编排一次聊天：读取上下文、组装消息、调用模型。"""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.java_backend = JavaBackendClient(settings)
        self.deepseek = DeepSeekClient(settings)
        self.semantic_parser = SemanticParser(self.deepseek)

    async def chat(self, request: ChatRequest, authorization: str | None) -> ChatResponseData:
        trace_id = uuid.uuid4().hex
        total_start = time.perf_counter()
        if not authorization:
            raise HTTPException(status_code=401, detail="请先登录")

        # 所有聊天均要求登录；Java 统一校验 JWT、Redis 会话和账号状态。
        await self.java_backend.validate_token(authorization)
        intents = detect_intents(request.message)
        food_record_intent = next((intent for intent in intents if intent.name == "food_record"), None)
        if food_record_intent:
            return await self._prepare_semantic_record(trace_id, total_start, request.message, authorization)

        clarification = build_clarification(intents)
        if clarification:
            return self._rule_based_response(trace_id, total_start, request.message, intents, clarification)

        context_start = time.perf_counter()
        tool_results: dict[str, dict] = {}
        if not intents:
            return await self._handle_unmatched_message(trace_id, total_start, request.message, authorization)

        # 不同意图互不阻塞，分别读取各自需要的只读数据。
        values = await asyncio.gather(*(self._execute_tool(intent, authorization) for intent in intents))
        tool_results = {intent.name: value for intent, value in zip(intents, values)}
        user_context = {"date": date.today().isoformat(), "data": tool_results}
        context_duration_ms = round(
            (time.perf_counter() - context_start) * 1000
        )
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
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
            "识别意图": [intent.name for intent in intents],
            "调用工具": [
                {"工具名称": intent.tool_name, "状态": "成功"}
                for intent in intents
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

    async def _handle_unmatched_message(
            self, trace_id: str, total_start: float, message: str, authorization: str
    ) -> ChatResponseData:
        """规则无法确定时再使用模型；模型只分类和回答，绝不读取或写入个人数据。"""
        try:
            semantic, model_result = await self.semantic_parser.parse(message)
        except ValueError:
            return self._rule_based_response(
                trace_id, total_start, message, [], "我没有完全理解你的意思，可以换一种更具体的说法吗？"
            )

        if semantic.intent == "food_record":
            return await self._build_diet_preview(trace_id, total_start, message, authorization, semantic, model_result)
        if semantic.intent == "clarification" or semantic.confidence < 0.70:
            return self._rule_based_response(
                trace_id, total_start, message, [], semantic.reply or "我没有完全理解你的意思，可以换一种更具体的说法吗？"
            )
        return self._semantic_text_response(trace_id, total_start, message, semantic, model_result)

    async def _prepare_semantic_record(
            self, trace_id: str, total_start: float, message: str, authorization: str
    ) -> ChatResponseData:
        """“吃了/喝了”只表示可能记录；仍要让模型提取一个食物及其份量。"""
        try:
            semantic, model_result = await self.semantic_parser.parse(message)
        except ValueError:
            return self._rule_based_response(
                trace_id, total_start, message, [], "我没能识别出要记录的食物，请补充食物名称、重量或份量。"
            )
        if semantic.intent != "food_record" or semantic.confidence < 0.70:
            return self._rule_based_response(
                trace_id, total_start, message, [], semantic.reply or "请一次告诉我一种食物，例如“晚饭吃了100克黄瓜”。"
            )
        return await self._build_diet_preview(trace_id, total_start, message, authorization, semantic, model_result)

    async def _build_diet_preview(
            self,
            trace_id: str,
            total_start: float,
            message: str,
            authorization: str,
            semantic: SemanticResult,
            model_result,
    ) -> ChatResponseData:
        if not semantic.food_name:
            return self._rule_based_response(
                trace_id, total_start, message, [], "你想记录哪一种食物？请一次输入一种食物。"
            )
        food_match = await self.java_backend.match_food(authorization, semantic.food_name)
        candidates = food_match.get("candidates") or []
        if not candidates:
            return self._rule_based_response(
                trace_id, total_start, message, [], f"没有找到“{semantic.food_name}”，请换一个更具体的食物名称。"
            )

        is_exact = food_match.get("matchType") in {"EXACT", "ALIAS"} and len(candidates) == 1
        selected = candidates[0] if is_exact else None
        quantity_g, estimated = self._resolve_quantity(selected, semantic.amount_g, semantic.amount_text)
        preview = {
            "matchType": food_match.get("matchType"),
            "normalizedName": food_match.get("normalizedName"),
            "candidates": candidates,
            "selectedFoodId": selected.get("id") if selected else None,
            "mealType": semantic.meal_type,
            "quantityG": quantity_g,
            "quantityEstimated": estimated,
            "amountText": semantic.amount_text,
            "recordTime": datetime.now().replace(microsecond=0).isoformat(),
        }
        reply = (
            f"已找到“{selected.get('name')}”，请确认食用重量、餐次和时间后再保存。"
            if selected else f"我找到了多个“{semantic.food_name}”相关食物，请先选择实际吃的是哪一种。"
        )
        return self._preview_response(trace_id, total_start, message, semantic, model_result, reply, preview)

    @staticmethod
    def _resolve_quantity(candidate: dict | None, amount_g: float | None, amount_text: str | None) -> tuple[float | None, bool]:
        if amount_g is not None:
            return amount_g, False
        if not candidate or not amount_text:
            return None, False
        for serving in candidate.get("servings") or []:
            name = serving.get("servingName") or ""
            if name and name in amount_text:
                multiplier = 0.5 if "半" in amount_text else 1
                gram_weight = serving.get("gramWeight")
                if gram_weight is not None:
                    return round(float(gram_weight) * multiplier, 2), True
        return None, False

    def _semantic_text_response(
            self, trace_id: str, total_start: float, message: str,
            semantic: SemanticResult, model_result
    ) -> ChatResponseData:
        reply = (semantic.reply or "这个问题暂时无法准确回答。") + "\n\n" + self._fallback_guidance()
        self._log_model_response(trace_id, total_start, message, [], model_result, reply, "semantic_fallback")
        return ChatResponseData(reply=reply, model=self.settings.deepseek_model, context_used=False)

    def _preview_response(
            self, trace_id: str, total_start: float, message: str, semantic: SemanticResult,
            model_result, reply: str, preview: dict
    ) -> ChatResponseData:
        self._log_model_response(trace_id, total_start, message, [semantic.intent], model_result, reply, "diet_record_preview")
        return ChatResponseData(
            reply=reply,
            model=self.settings.deepseek_model,
            context_used=False,
            action="confirm_diet_record",
            recordPreview=preview,
        )

    def _log_model_response(
            self, trace_id: str, total_start: float, message: str, intents: list[str],
            model_result, reply: str, flow: str
    ) -> None:
        estimated_cost = (
            model_result.prompt_tokens * self.settings.deepseek_input_price_per_million / 1_000_000
            + model_result.completion_tokens * self.settings.deepseek_output_price_per_million / 1_000_000
        )
        logger.info("AI_CALL\n%s", json.dumps({
            "追踪编号": trace_id,
            "用户问题": message,
            "识别意图": intents,
            "处理流程": flow,
            "调用工具": [],
            "模型": self.settings.deepseek_model,
            "输入Token数": model_result.prompt_tokens,
            "输出Token数": model_result.completion_tokens,
            "总Token数": model_result.total_tokens,
            "预估成本": round(estimated_cost, 8),
            "总耗时毫秒": round((time.perf_counter() - total_start) * 1000),
            "AI输出": reply,
            "状态": "成功",
        }, ensure_ascii=False, indent=2))

    async def _execute_tool(self, intent: IntentMatch, authorization: str) -> dict:
        tool_factories = {
            "daily_summary": self.java_backend.get_today_summary,
            "diet_detail": self.java_backend.get_today_diet_detail,
            "weight_progress": self.java_backend.get_weight_progress,
            "user_goal": self.java_backend.get_user_goal,
            "food_nutrition": self.java_backend.search_food_nutrition,
            "exercise_calorie": self.java_backend.search_exercise_calorie,
        }
        return await tool_factories[intent.name](authorization, **intent.params)

    def _rule_based_response(
            self,
            trace_id: str,
            total_start: float,
            question: str,
            intents: list[IntentMatch],
            reply: str,
            tool_results: dict[str, dict] | None = None,
    ) -> ChatResponseData:
        log_data = {
            "追踪编号": trace_id,
            "用户问题": question,
            "识别意图": [intent.name for intent in intents],
            "调用工具": (
                [{"工具名称": intent.tool_name, "状态": "未调用"} for intent in intents]
                if not tool_results
                else [{"工具名称": name, "状态": "成功"} for name in tool_results]
            ),
            "用户上下文": tool_results or {},
            "模型": "rule-based",
            "总耗时毫秒": round((time.perf_counter() - total_start) * 1000),
            "AI输出": reply,
            "状态": "成功",
        }
        logger.info("AI_CALL\n%s", json.dumps(log_data, ensure_ascii=False, indent=2))
        return ChatResponseData(reply=reply, model="rule-based", context_used=bool(tool_results))

    @staticmethod
    def _fallback_guidance() -> str:
        """方案 B 的固定收束语，避免无关回答被误解为健康专业结论。"""
        return "我是 Orange Diary 健康饮食助手，也可以继续帮你记录饮食、查询热量、运动和体重目标。"

    @staticmethod
    def _build_goal_guidance(goal_context: dict) -> str:
        goal = goal_context.get("goal") or {}
        profile = goal_context.get("profile") or {}
        goal_type_text = {
            "lose": "减脂",
            "gain": "增肌",
            "maintain": "维持",
            # 兼容早期可能写入过的值，避免历史数据直接显示内部枚举。
            "lose_fat": "减脂",
            "gain_muscle": "增肌",
        }
        goal_type = goal_type_text.get(goal.get("goalType"), goal.get("goalType"))
        target_weight = profile.get("targetWeightKg")
        intake_target = goal.get("dailyIntakeTarget")
        if not goal_type and target_weight is None and intake_target is None:
            return "先完善个人资料和目标后，我可以给你更准确的建议。"
        details = []
        if goal_type:
            details.append(f"当前目标是{goal_type}")
        if target_weight is not None:
            details.append(f"目标体重为{target_weight}kg")
        if intake_target is not None:
            details.append(f"每日摄入目标为{intake_target}千卡")
        return "你好，我是你的健康饮食助手。" + "，".join(details) + "。你可以继续问我今天吃了什么、还能吃多少热量、体重变化或运动安排。"
