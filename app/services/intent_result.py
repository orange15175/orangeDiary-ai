"""Canonical intent representation shared by rules and semantic parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# 统一意图与行为的归属，后续编排层只需关注 chat/query/action。
INTENT_BEHAVIORS = {
    "daily_summary": "query",
    "diet_detail": "query",
    "food_nutrition": "query",
    "weight_progress": "query",
    "user_goal": "query",
    "exercise_calorie": "query",
    "exercise_record": "action",
    "food_record": "action",
    "health_advice": "chat",
    "out_of_scope": "chat",
    "clarification": "chat",
}


@dataclass(frozen=True)
class IntentResult:
    """One normalized intent; it is internal and never shown as the reply."""

    intent: str
    behavior: str
    confidence: float
    slots: Mapping[str, object] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    source: str = "unknown"

    @classmethod
    def from_rule(cls, intent_match) -> "IntentResult":
        # 规则命中视为高确定性结果；参数沿用规则提取结果。
        return cls(
            intent=intent_match.name,
            behavior=INTENT_BEHAVIORS.get(intent_match.name, "chat"),
            confidence=1.0,
            slots=dict(intent_match.params),
            missing_slots=intent_match.missing_fields,
            source="rule",
        )

    @classmethod
    def from_semantic(cls, semantic) -> "IntentResult":
        # 模型只能提供意图和候选参数，行为类型始终由本地映射决定。
        slots = dict(getattr(semantic, "slots", {}) or {})
        for key in ("food_name", "meal_type", "amount_text", "amount_g", "metrics"):
            value = getattr(semantic, key, None)
            if value is not None:
                slots[key] = value
        return cls(
            intent=semantic.intent,
            behavior=INTENT_BEHAVIORS.get(semantic.intent, "chat"),
            confidence=semantic.confidence,
            slots=slots,
            missing_slots=tuple(getattr(semantic, "missing_slots", ()) or ()),
            source="semantic",
        )
