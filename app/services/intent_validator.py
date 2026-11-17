"""语义意图结果的本地校验。

模型输出一律不可直接信任：意图必须属于登记集合，槽位只接受白名单键，
数值必须落在业务可接受区间。非法值在这里降级，而不是透传给业务层。
规则路径与语义路径共用这里的规范化函数，避免两套校验标准。
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
# 每日汇总指标：只描述“今天的摄入/剩余/目标”，与食物营养指标不是同一套枚举。
ALLOWED_SUMMARY_METRICS = {"intake_calorie", "remaining_calorie", "target_calorie"}
# 食物营养指标：只用于单食物查询，统一成英文枚举，中文别名在边界归一化。
ALLOWED_NUTRITION_METRICS = {"calorie", "protein", "fat", "carbohydrate"}
NUTRITION_METRIC_ALIASES = {
    "热量": "calorie",
    "卡路里": "calorie",
    "大卡": "calorie",
    "千卡": "calorie",
    "能量": "calorie",
    "蛋白质": "protein",
    "脂肪": "fat",
    "碳水": "carbohydrate",
    "碳水化合物": "carbohydrate",
}
ALLOWED_UNITS = {
    "克", "g", "公克", "千克", "公斤", "kg", "斤", "两",
    "毫升", "ml", "升", "l",
    "个", "只", "碗", "份", "杯", "瓶", "盒", "袋", "片",
    "根", "块", "勺", "盘", "条", "颗", "串", "听", "罐", "张", "把",
}
ALLOWED_SLOT_KEYS = {
    "food_name", "amount_g", "amount_text", "nutrition_metric", "exercise_name", "duration_min",
    "quantity", "unit", "meal_type", "metrics",
}
# 每种意图必须具备的槽位；缺失时统一进入追问，而不是生成可保存的错误预览。
REQUIRED_SLOTS = {
    "food_record": ("food_name", "amount_g"),
    "food_nutrition": ("food_name",),
    "exercise_calorie": ("exercise_name", "duration_min"),
    "exercise_record": ("exercise_name", "duration_min"),
}

MAX_AMOUNT_G = 99999.99
MAX_QUANTITY = 9999.0
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


def _finite_number(value: object) -> float | None:
    """只接受真实数值；布尔值、NaN、Infinity 和字符串数字都不能混入业务槽位。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def normalize_nutrition_metric(value: object) -> str | None:
    """把中文指标别名归一化为枚举；无法识别时丢弃。"""
    if isinstance(value, str):
        value = value.strip()
        if value in ALLOWED_NUTRITION_METRICS:
            return value
        return NUTRITION_METRIC_ALIASES.get(value)
    return None


def _normalize_metrics(value: object) -> list[str]:
    """每日汇总指标只接受登记枚举，保持出现顺序并去重。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() in ALLOWED_SUMMARY_METRICS:
            metric = item.strip()
            if metric not in result:
                result.append(metric)
    return result


def normalize_slots(raw_slots: object) -> dict:
    """白名单、类型、范围三层校验，规则与语义路径共用。"""
    slots: dict = {}
    if not isinstance(raw_slots, dict):
        return slots
    for key, value in raw_slots.items():
        if key not in ALLOWED_SLOT_KEYS:
            continue
        if key == "amount_g":
            amount = _finite_number(value)
            if amount is not None and 0 < amount <= MAX_AMOUNT_G:
                slots[key] = round(amount, 2)
        elif key == "quantity":
            quantity = _finite_number(value)
            if quantity is not None and 0 < quantity <= MAX_QUANTITY:
                slots[key] = quantity
        elif key == "duration_min":
            duration = _finite_number(value)
            if duration is not None and duration.is_integer() and 0 < duration <= MAX_DURATION_MIN:
                slots[key] = int(duration)
        elif key == "nutrition_metric":
            metric = normalize_nutrition_metric(value)
            if metric:
                slots[key] = metric
        elif key == "metrics":
            metrics = _normalize_metrics(value)
            if metrics:
                slots[key] = tuple(metrics)
        elif key == "unit":
            if isinstance(value, str) and value.strip().lower() in ALLOWED_UNITS:
                slots[key] = value.strip()
        elif key == "meal_type":
            if isinstance(value, str) and value.strip() in ALLOWED_MEALS:
                slots[key] = value.strip()
        else:
            text = _optional_text(value)
            if text:
                slots[key] = text
    return slots


def canonicalize_slots(intent: str, raw_slots: object) -> tuple[dict, tuple[str, ...]]:
    """校验槽位并重算缺失项；非法值被丢弃后自动变成缺失参数。"""
    slots = normalize_slots(raw_slots)
    missing = tuple(field for field in REQUIRED_SLOTS.get(intent, ()) if not slots.get(field))
    return slots, missing


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

    # 边界兼容：内部只用 missing_slots，旧字段 missingSlots 仅在这里读取一次。
    raw_missing = body.get("missing_slots")
    if raw_missing is None:
        raw_missing = body.get("missingSlots")
    if not isinstance(raw_missing, list):
        raw_missing = []
    model_missing = [item.strip()[:40] for item in raw_missing if isinstance(item, str) and item.strip()][:8]

    slots, canonical_missing = canonicalize_slots(intent, body.get("slots"))
    # 兼容旧字段名的槽位只在本边界合并，内部统一 snake_case。
    legacy_meal = body.get("meal_type", body.get("mealType"))
    if isinstance(legacy_meal, str) and legacy_meal in ALLOWED_MEALS:
        slots.setdefault("meal_type", legacy_meal)
    legacy_amount = _finite_number(body.get("amount_g", body.get("amountG")))
    if "amount_g" not in slots and legacy_amount is not None and 0 < legacy_amount <= MAX_AMOUNT_G:
        slots["amount_g"] = legacy_amount

    missing = list(dict.fromkeys([*model_missing, *canonical_missing]))
    # 模型报告的缺失项只能在已知槽位里，避免把任意文本当参数名下发。
    missing = [field for field in missing if field in ALLOWED_SLOT_KEYS]

    return {
        "intent": intent,
        "confidence": confidence,
        "slots": slots,
        "missing_slots": missing,
        "reply": _optional_text(body.get("reply"), MAX_REPLY_LENGTH),
    }
