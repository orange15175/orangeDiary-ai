"""Top-level orchestration for one authenticated Orange Diary chat request."""

from __future__ import annotations

from fastapi import HTTPException

from app.clients.deepseek_client import DeepSeekClient
from app.clients.java_backend import JavaBackendClient
from app.config import Settings
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.chat import ChatRequest, ChatResponseData
from app.services.chat_context import ChatExecutionContext
from app.services.intent_result import IntentResult
from app.services.intent_router import build_clarification, detect_intents
from app.services.memory_service import MemoryService
from app.services.record_preview_handler import RecordPreviewHandler
from app.services.request_plan import RequestPlanBuilder
from app.services.response_factory import ChatResponseFactory
from app.services.rule_query_handler import RuleQueryHandler
from app.services.semantic_parser import SemanticParser
from app.services.semantic_request_handler import SemanticRequestHandler
from app.services.tool_executor import ToolExecutor


class ChatService:
    """Coordinates stable request stages; business branches live in dedicated handlers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.java_backend = JavaBackendClient(settings)
        self.deepseek = DeepSeekClient(settings)
        self.semantic_parser = SemanticParser(self.deepseek)
        self.request_plan_builder = RequestPlanBuilder()
        self.memory_service = MemoryService(ConversationRepository(settings), self.deepseek)
        self.tool_executor = ToolExecutor(self.java_backend)

    async def chat(self, request: ChatRequest, authorization: str | None) -> ChatResponseData:
        """Run authentication, memory, dispatch, and persistence in that fixed order."""
        if not authorization:
            raise HTTPException(status_code=401, detail="请先登录")

        # Java remains the sole authority for JWT, Redis session, and user identity.
        user_id = await self.java_backend.validate_token(authorization)
        memory_context = await self.memory_service.load_context(user_id)
        context = ChatExecutionContext.create(request, authorization, user_id, memory_context)

        # A response must be successfully produced before this turn becomes durable memory.
        result = await self._dispatch(context)
        await self.memory_service.save_completed_turn(memory_context, request.message, result.reply)
        return result

    async def _dispatch(self, context: ChatExecutionContext) -> ChatResponseData:
        """Choose exactly one request branch after the deterministic rules have been evaluated."""
        handlers = self._build_handlers()
        message = context.request.message
        rule_intents = detect_intents(message)
        canonical_intents = tuple(IntentResult.from_rule(intent) for intent in rule_intents)
        request_plan = getattr(self, "request_plan_builder", RequestPlanBuilder()).build(canonical_intents)

        food_record = next((item for item in rule_intents if item.name == "food_record"), None)
        if food_record:
            # The rule indicates a likely record, but the parser still validates the concrete food data.
            return await handlers.records.prepare_diet_record(
                context.trace_id, context.started_at, message, context.authorization
            )

        exercise_record = next((item for item in rule_intents if item.name == "exercise_record"), None)
        if exercise_record:
            # Rule-detected exercises still return a preview; no handler writes a record directly.
            return await handlers.records.build_exercise_preview(
                context.trace_id, context.started_at, message, context.authorization, exercise_record
            )

        clarification = build_clarification(rule_intents)
        if clarification:
            # Known intent plus missing parameters is safer as a question than as a model guess.
            return handlers.responses.rule_based_response(
                context.trace_id, context.started_at, message, rule_intents, clarification
            )

        if not rule_intents:
            # The semantic handler is intentionally the only fallback that calls the classifier model.
            return await handlers.semantic.handle(
                context.trace_id, context.started_at, message, context.authorization, context.memory_context
            )

        return await handlers.rule_query.handle(
            context.trace_id, context.started_at, message, context.authorization,
            rule_intents, request_plan, context.memory_context,
        )

    def _build_handlers(self):
        """Create request-scoped collaborators from current dependencies to keep tests and debugging explicit."""
        responses = ChatResponseFactory(self.settings)
        records = RecordPreviewHandler(self.java_backend, self.semantic_parser, responses)
        semantic = SemanticRequestHandler(
            self.settings, self.deepseek, self.semantic_parser, self.tool_executor, records, responses
        )
        rule_query = RuleQueryHandler(self.settings, self.deepseek, self.tool_executor, responses)
        return _RequestHandlers(responses, records, semantic, rule_query)


class _RequestHandlers:
    """Small internal container that makes ChatService dispatch dependencies visible at one breakpoint."""

    def __init__(self, responses, records, semantic, rule_query):
        self.responses = responses
        self.records = records
        self.semantic = semantic
        self.rule_query = rule_query
