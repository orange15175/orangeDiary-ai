"""Build a small, composable behavior plan before business execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Protocol

from app.services.intent_router import IntentMatch
from app.services.intent_result import IntentResult


class Behavior(StrEnum):
    CHAT = "chat"
    QUERY = "query"
    ACTION = "action"


@dataclass(frozen=True)
class RequestPlan:
    """Internal routing metadata; it is never exposed as the user reply."""

    behaviors: tuple[Behavior, ...]
    intents: tuple[IntentResult, ...]

    @property
    def mode(self) -> str:
        return "+".join(behavior.value for behavior in self.behaviors)


class BehaviorStrategy(Protocol):
    behavior: Behavior

    def matches(self, intents: Iterable[IntentResult]) -> bool:
        ...


class ChatBehaviorStrategy:
    behavior = Behavior.CHAT

    def matches(self, intents: Iterable[IntentResult]) -> bool:
        # 没有业务意图时才进入普通聊天；避免查询误走泛化回答。
        return not tuple(intents)


class QueryBehaviorStrategy:
    behavior = Behavior.QUERY

    def matches(self, intents: Iterable[IntentResult]) -> bool:
        # query 可以与 action 同时存在，组合关系由后续编排器处理。
        return any(intent.intent != "food_record" and intent.behavior == "query" for intent in intents)


class ActionBehaviorStrategy:
    behavior = Behavior.ACTION

    def matches(self, intents: Iterable[IntentResult]) -> bool:
        # 当前只有饮食记录属于写操作，仍需走预览确认而非直接写库。
        return any(intent.behavior == "action" for intent in intents)


class RequestPlanBuilder:
    """Compose independent behaviors instead of enumerating combinations."""

    def __init__(self, strategies: tuple[BehaviorStrategy, ...] | None = None):
        self.strategies = strategies or (
            ChatBehaviorStrategy(),
            QueryBehaviorStrategy(),
            ActionBehaviorStrategy(),
        )

    def build(self, intents: Iterable[IntentResult | IntentMatch]) -> RequestPlan:
        # 兼容旧规则结果，逐步让所有识别路径收敛到 IntentResult。
        intent_tuple = tuple(
            intent if isinstance(intent, IntentResult) else IntentResult.from_rule(intent)
            for intent in intents
        )
        behaviors = tuple(
            strategy.behavior
            for strategy in self.strategies
            if strategy.matches(intent_tuple)
        )
        if not behaviors:
            behaviors = (Behavior.CHAT,)
        return RequestPlan(behaviors=behaviors, intents=intent_tuple)
