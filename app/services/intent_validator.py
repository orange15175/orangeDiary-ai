"""语义意图结果的本地校验。

模型输出一律不可直接信任：意图必须属于登记集合，槽位只接受白名单键，
数值必须落在业务可接受区间。非法值在这里降级，而不是透传给业务层。
"""

from __future__ import annotations

import json
import re

ALLOWED_INTENTS = {
    "daily_summary",
    "diet_detail",
    "food_nutrition",
    "food_record",
    "weight_progress",
    "user_goal",
    "exercise_calorie",
    "exercise_record",
    "health_advice",
    "out_of_scope",
    "clarification",
}
ALLOWED_MEALS = {"breakfast", "lunch", "dinner", "snack"}
ALLOWED_METRICS = {"intake_calorie", "remaining_calorie", "target_calorie"}
ALLOWED_SLOT_KEYS = {"food_name", "amount_g", "nutrition_metric", "exercise_name", "duration_min"}

MAX_AMOUNT_G = 99999.99
MAX_DURATION_MIN = 1440
MAX_REPLY_LENGTH = 400
MAX_SLOT_TEXT_LENGTH = 200


def strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏，模型偶尔不遵守 json 模式。"""
    if not isinstance(text, str):
        return ""
    value = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value, re.S)
    if fenced:
        return fenced.group(1).strip()
    return value


def parse_semantic_json(content: str) -> dict | None:
    """解析模型返回的 JSON，无法解析时返回 None，不抛异常。"""
    raw = strip_code_fence(content)
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            body = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return body if isinstance(body, dict) else None


def _optional_text(value: object, limit: int = MAX_SLOT_TEXT_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _normalize_slots(raw_slots: object) -> dict:
    slots: dict = {}
    if not isinstance(raw_slots, dict):
        return slots
    for key, value in raw_slots.items():
        if key not in ALLOWED_SLOT_KEYS:
            continue
        if key == "amount_g":
            try:
                amount = float(value)
            except (TypeError, ValueError):
                continue
            if 0 < amount <= MAX_AMOUNT_G:
                slots[key] = amount
        elif key == "duration_min":
            try:
                duration = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < duration <= MAX_DURATION_MIN:
                slots[key] = duration
        elif key == "nutrition_metric":
            if value in ALLOWED_METRICS:
                slots[key] = value
        else:
            text = _optional_text(value)
            if text:
                slots[key] = text
    return slots


def normalize_semantic_result(body: dict) -> dict:
    """把模型输出规范化为可下发结构，非法值降级为安全默认。"""
    intent = body.get("intent")
    if intent not in ALLOWED_INTENTS:
        intent = "clarification"

    try:
        confidence = float(body.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not 0 <= confidence <= 1:
        confidence = 0.0

    slots = _normalize_slots(body.get("slots"))
    meal_type = body.get("mealType")
    if meal_type in ALLOWED_MEALS:
        slots["meal_type"] = meal_type

    missing = body.get("missingSlots")
    if not isinstance(missing, list):
        missing = []
    missing = [item.strip()[:40] for item in missing if isinstance(item, str) and item.strip()][:8]

    return {
        "intent": intent,
        "confidence": confidence,
        "slots": slots,
        "missing_slots": missing,
        "reply": _optional_text(body.get("reply"), MAX_REPLY_LENGTH),
    }
