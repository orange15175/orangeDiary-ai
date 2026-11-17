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
        """把规则结果转换为统一意图结构。"""
        # 规则结果与模型结果走同一套槽位校验；非法值丢弃后自动转为缺参追问。
        from app.services.intent_validator import canonicalize_slots

        slots, missing_slots = canonicalize_slots(intent_match.name, dict(intent_match.params))
        return cls(
            intent=intent_match.name,
            behavior=INTENT_BEHAVIORS.get(intent_match.name, "chat"),
            confidence=1.0,
            slots=slots,
            missing_slots=missing_slots,
            source="rule",
        )

    @classmethod
    def from_semantic(cls, semantic) -> "IntentResult":
        """把模型分类结果转换为统一意图结构。"""
        from app.services.intent_validator import canonicalize_slots

        # 模型只能提供意图和候选参数，行为类型始终由本地映射决定。
        slots = dict(getattr(semantic, "slots", {}) or {})
        for key in ("food_name", "meal_type", "amount_text", "amount_g", "metrics"):
            value = getattr(semantic, key, None)
            if value is not None:
                slots[key] = value
        slots, canonical_missing = canonicalize_slots(semantic.intent, slots)
        model_missing = tuple(getattr(semantic, "missing_slots", ()) or ())
        missing_slots = tuple(dict.fromkeys([*model_missing, *canonical_missing]))
        return cls(
            intent=semantic.intent,
            behavior=INTENT_BEHAVIORS.get(semantic.intent, "chat"),
            confidence=semantic.confidence,
            slots=slots,
            missing_slots=missing_slots,
            source="semantic",
        )
