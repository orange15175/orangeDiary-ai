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
from app.repositories.conversation_repository import ConversationRepository
from app.services.chat_context import ChatExecutionContext
from app.services.memory_service import MemoryService
from app.services.model_message_factory import ModelMessageFactory
from app.services.tool_executor import ToolExecutor
from app.schemas.chat import ChatRequest, ChatResponseData
from app.services.intent_router import IntentMatch, build_clarification, detect_intents
from app.services.intent_result import IntentResult
from app.services.request_plan import RequestPlanBuilder
from app.services.semantic_parser import SemanticParser, SemanticResult

logger = logging.getLogger("orange-diary-ai")


# 模型只能返回统一意图；实际工具名称必须由本地白名单映射。
SEMANTIC_TOOL_NAMES = {
    "daily_summary": "get_today_summary",
    "diet_detail": "get_today_diet_detail",
    "food_nutrition": "search_food_nutrition",
    "weight_progress": "get_weight_progress",
    "user_goal": "get_user_goal",
    "exercise_calorie": "search_exercise_calorie",
}

# 查询参数不足时直接追问，禁止让模型猜测后请求 Java。
SEMANTIC_REQUIRED_SLOTS = {
    "food_nutrition": ("food_name",),
    "exercise_calorie": ("exercise_name", "duration_min"),
}


class ChatService:
    """编排一次聊天：读取上下文、组装消息、调用模型。"""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.java_backend = JavaBackendClient(settings)
        self.deepseek = DeepSeekClient(settings)
        self.semantic_parser = SemanticParser(self.deepseek)
        self.request_plan_builder = RequestPlanBuilder()
        self.memory_service = MemoryService(ConversationRepository(settings), self.deepseek)
        self.tool_executor = ToolExecutor(self.java_backend)

    async def chat(self, request: ChatRequest, authorization: str | None) -> ChatResponseData:
        if not authorization:
            raise HTTPException(status_code=401, detail="请先登录")
        # Java 验证 JWT、Redis 会话和账号状态，确保 Python 不访问用户范围的对话。
        user_id = await self.java_backend.validate_token(authorization)
        memory_context = await self.memory_service.load_context(user_id)
        context = ChatExecutionContext.create(request, authorization, user_id, memory_context)
        result = await self._chat_authenticated(
            context.request, context.authorization, context.memory_context.messages, context.memory_context
        )
        # Only completed request/response pairs become persistent memory; failed calls cannot leave a half turn.
        await self.memory_service.save_completed_turn(context, request.message, result.reply)
        return result

    async def _chat_authenticated(self, request: ChatRequest, authorization: str, memory_messages, memory_context) -> ChatResponseData:
        trace_id = uuid.uuid4().hex
        total_start = time.perf_counter()
        intents = detect_intents(request.message)
        plan_builder = getattr(self, "request_plan_builder", RequestPlanBuilder())
        # 规则路径也转换为统一结构，便于和语义路径使用同一套行为计划。
        canonical_intents = tuple(IntentResult.from_rule(intent) for intent in intents)
        request_plan = plan_builder.build(canonical_intents)
        food_record_intent = next((intent for intent in intents if intent.name == "food_record"), None)
        if food_record_intent:
            # “吃了/喝了”只说明可能要记录，仍需模型提取食物和份量，且不会直接写库。
            return await self._prepare_semantic_record(trace_id, total_start, request.message, authorization)

        exercise_record_intent = next((intent for intent in intents if intent.name == "exercise_record"), None)
        if exercise_record_intent:
            # A rule match may identify the activity, but never writes a record without confirmation.
            return await self._build_exercise_preview(trace_id, total_start, request.message, authorization, exercise_record_intent)

        clarification = build_clarification(intents)
        if clarification:
            # 规则已锁定意图但关键参数缺失时，优先追问而不是让模型猜参数。
            return self._rule_based_response(trace_id, total_start, request.message, intents, clarification)

        context_start = time.perf_counter()
        tool_results: dict[str, dict] = {}
        if not intents:
            # 只有规则完全无法判断时，才增加一次模型调用做语义识别。
            return await self._handle_unmatched_message(
                trace_id, total_start, request.message, authorization, memory_messages, memory_context
            )

        # 不同意图互不阻塞，分别读取各自需要的只读数据。
        values = await asyncio.gather(*(self._execute_tool(intent, authorization) for intent in intents))
        tool_results = {intent.name: value for intent, value in zip(intents, values)}
        user_context = {"date": date.today().isoformat(), "data": tool_results}
        context_duration_ms = round(
            (time.perf_counter() - context_start) * 1000
        )
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        messages = ModelMessageFactory.build(memory_context, request.message, user_context)
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
            "行为": [behavior.value for behavior in request_plan.behaviors],
            "请求模式": request_plan.mode,
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
            self, trace_id: str, total_start: float, message: str, authorization: str, history, memory_context
    ) -> ChatResponseData:
        """规则无法确定时使用语义识别，再由代码决定是否查询个人数据。"""
        try:
            semantic, model_result = await self.semantic_parser.parse(message)
        except ValueError:
            return self._rule_based_response(
                trace_id, total_start, message, [], "我没有完全理解你的意思，可以换一种更具体的说法吗？"
            )

        semantic_intent = IntentResult.from_semantic(semantic)
        # 低置信度的查询和记录都只追问，不能访问数据或生成记录预览。
        if semantic.intent == "clarification" or semantic.confidence < 0.70:
            return self._rule_based_response(
                trace_id,
                total_start,
                message,
                [],
                semantic.reply or "我没有完全理解你的意思，可以换一种更具体的说法吗？",
                source=semantic_intent.source,
                confidence=semantic_intent.confidence,
            )
        if semantic.intent == "food_record":
            return await self._build_diet_preview(trace_id, total_start, message, authorization, semantic, model_result)
        if semantic.intent == "exercise_record":
            return await self._build_exercise_preview(trace_id, total_start, message, authorization, semantic)
        if semantic_intent.behavior == "query":
            # 语义查询进入受控工具链，而不是落入普通 semantic_fallback。
            return await self._handle_semantic_query(
                trace_id, total_start, message, authorization, semantic_intent, history, memory_context
            )
        return await self._handle_memory_chat(
            trace_id, total_start, message, semantic_intent, history, memory_context
        )

    async def _handle_semantic_query(
            self,
            trace_id: str,
            total_start: float,
            message: str,
            authorization: str,
            semantic_intent: IntentResult,
            history,
            memory_context,
    ) -> ChatResponseData:
        # 先校验模型给出的参数，再将意图转换为本地允许的工具调用。
        missing = self._semantic_missing_slots(semantic_intent)
        if missing:
            return self._rule_based_response(
                trace_id, total_start, message, [], self._missing_slot_reply(missing),
                source=semantic_intent.source, confidence=semantic_intent.confidence,
            )

        intent = self._semantic_to_tool_intent(semantic_intent)
        if intent is None:
            return self._rule_based_response(
                trace_id, total_start, message, [], "我暂时无法确定该查询需要读取哪些数据，请换一种更具体的说法。",
                source=semantic_intent.source, confidence=semantic_intent.confidence,
            )
        try:
            # authorization 来自当前请求，Java 负责校验其对应的用户身份。
            tool_result = await self._execute_tool(intent, authorization)
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            return self._rule_based_response(
                trace_id, total_start, message, [], "暂时无法读取你的健康数据，请稍后再试。",
                source=semantic_intent.source,
                confidence=semantic_intent.confidence,
                tool_calls=[{"工具名称": intent.tool_name, "状态": "失败"}],
            )

        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        # 真实业务数据作为唯一上下文来源，模型只负责组织自然语言答案。
        user_context = {"date": date.today().isoformat(), "data": {intent.name: tool_result}}
        messages = ModelMessageFactory.build(memory_context, message, user_context)
        model_result = await self.deepseek.chat(messages)
        self._log_model_response(
            trace_id,
            total_start,
            message,
            [semantic_intent.intent],
            model_result,
            model_result.reply,
            "semantic_tool_context",
            [semantic_intent.behavior],
            source=semantic_intent.source,
            confidence=semantic_intent.confidence,
            tool_calls=[{"工具名称": intent.tool_name, "状态": "成功"}],
        )
        return ChatResponseData(
            reply=model_result.reply,
            model=self.settings.deepseek_model,
            context_used=True,
        )

    @staticmethod
    def _semantic_missing_slots(semantic_intent: IntentResult) -> tuple[str, ...]:
        # 同时采信模型声明的缺失项和代码定义的必填项，防止漏校验。
        missing = set(semantic_intent.missing_slots)
        for slot in SEMANTIC_REQUIRED_SLOTS.get(semantic_intent.intent, ()):
            if not semantic_intent.slots.get(slot):
                missing.add(slot)
        return tuple(sorted(missing))

    @staticmethod
    def _missing_slot_reply(missing: tuple[str, ...]) -> str:
        if "food_name" in missing:
            return "你想查询哪一种食物的营养信息？"
        if "exercise_name" in missing:
            return "你想查询哪一种运动的热量消耗？"
        if "duration_min" in missing:
            return "你大约进行了多少分钟？"
        return "请补充更具体的信息，我才能准确查询。"

    @staticmethod
    def _semantic_to_tool_intent(semantic_intent: IntentResult) -> IntentMatch | None:
        # 不接受模型传入的工具名，只允许由意图映射到固定工具。
        tool_name = SEMANTIC_TOOL_NAMES.get(semantic_intent.intent)
        if tool_name is None:
            return None
        allowed_slots = {
            "food_nutrition": ("food_name", "amount_g", "nutrition_metric"),
            "exercise_calorie": ("exercise_name", "duration_min"),
            "daily_summary": ("metrics",),
        }
        params = {
            key: value
            for key, value in semantic_intent.slots.items()
            if key in allowed_slots.get(semantic_intent.intent, ())
        }
        # 只透传当前工具声明过的参数，丢弃模型额外生成的字段。
        return IntentMatch(
            name=semantic_intent.intent,
            tool_name=tool_name,
            priority=0,
            conflict_group=None,
            params=params,
        )

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
        # 记录请求先变成可编辑预览；真正保存仍由前端确认后的既有接口完成。
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

        # 只有精确命中或别名命中且候选唯一时，才可预选食物，避免替用户猜测。
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

    async def _build_exercise_preview(self, trace_id, total_start, message, authorization, intent, model_result=None):
        """Build a confirmation payload only; the client still saves through Java after user approval."""
        params = getattr(intent, "params", None) or getattr(intent, "slots", {}) or {}
        exercise_name = params.get("exercise_name")
        duration_min = params.get("duration_min")
        if not exercise_name:
            return self._rule_based_response(trace_id, total_start, message, [], "你完成的是哪一种运动？")
        if not duration_min:
            return self._rule_based_response(trace_id, total_start, message, [], "你大约进行了多少分钟？")
        result = await self.java_backend.search_exercise_calorie(
            authorization, exercise_name=exercise_name, duration_min=int(duration_min)
        )
        candidates = result.get("candidates") or []
        if not candidates:
            return self._rule_based_response(trace_id, total_start, message, [], f"没有找到“{exercise_name}”，请从运动列表中选择。")
        preview = {
            "candidates": candidates,
            "selectedExerciseId": candidates[0].get("id") if len(candidates) == 1 else None,
            "durationMin": int(duration_min),
            "recordTime": datetime.now().replace(microsecond=0).isoformat(),
        }
        reply = f"已找到“{candidates[0].get('name')}”，请确认时长和时间后再保存。" if len(candidates) == 1 else "我找到了多个可能的运动项目，请先选择实际完成的是哪一种。"
        if model_result is not None:
            self._log_model_response(trace_id, total_start, message, ["exercise_record"], model_result, reply, "exercise_record_preview", ["action"], source="semantic", confidence=getattr(intent, "confidence", None))
            model = self.settings.deepseek_model
        else:
            model = "rule-based"
        return ChatResponseData(reply=reply, model=model, context_used=False, action="confirm_exercise_record", exercisePreview=preview)

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

    async def _handle_memory_chat(
            self,
            trace_id: str,
            total_start: float,
            message: str,
            semantic_intent: IntentResult,
            history,
            memory_context,
    ) -> ChatResponseData:
        """Generate ordinary chat answers from persisted memory without fetching live Java business data."""
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")

        messages = ModelMessageFactory.build(memory_context, message)
        model_result = await self.deepseek.chat(messages)
        self._log_model_response(
            trace_id,
            total_start,
            message,
            [semantic_intent.intent],
            model_result,
            model_result.reply,
            "memory_chat",
            ["chat"],
            source="semantic",
            confidence=semantic_intent.confidence,
        )
        return ChatResponseData(reply=model_result.reply, model=self.settings.deepseek_model, context_used=False)

    def _preview_response(
            self, trace_id: str, total_start: float, message: str, semantic: SemanticResult,
            model_result, reply: str, preview: dict
    ) -> ChatResponseData:
        self._log_model_response(
            trace_id, total_start, message, [semantic.intent], model_result, reply,
            "diet_record_preview", ["action"], source="semantic", confidence=semantic.confidence,
        )
        return ChatResponseData(
            reply=reply,
            model=self.settings.deepseek_model,
            context_used=False,
            action="confirm_diet_record",
            recordPreview=preview,
        )

    def _log_model_response(
            self, trace_id: str, total_start: float, message: str, intents: list[str],
            model_result, reply: str, flow: str, behaviors: list[str] | None = None,
            source: str | None = None, confidence: float | None = None,
            tool_calls: list[dict] | None = None,
    ) -> None:
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

    async def _execute_tool(self, intent: IntentMatch, authorization: str) -> dict:
        return await self.tool_executor.execute(intent, authorization)

    def _rule_based_response(
            self,
            trace_id: str,
            total_start: float,
            question: str,
            intents: list[IntentMatch],
            reply: str,
            tool_results: dict[str, dict] | None = None,
            source: str | None = None,
            confidence: float | None = None,
            tool_calls: list[dict] | None = None,
    ) -> ChatResponseData:
        # 追问、参数不足和工具失败统一走这里，保持前端响应协议不变。
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
