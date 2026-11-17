"""第二层语义识别：仅输出结构化信息，不能直接决定数据库写入。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.clients.deepseek_client import DeepSeekClient, ModelCallResult


@dataclass(frozen=True)
class SemanticResult:
    intent: str
    confidence: float
    food_name: str | None = None
    meal_type: str | None = None
    amount_text: str | None = None
    amount_g: float | None = None
    # 下面三个字段由统一意图层消费，不直接决定 Java 工具调用。
    metrics: tuple[str, ...] = ()
    missing_slots: tuple[str, ...] = ()
    slots: dict[str, object] | None = None
    reply: str | None = None


class SemanticParser:
    """规则无法准确处理时，让模型做意图分类与单食物实体提取。"""

    _ALLOWED_INTENTS = {
        "daily_summary", "diet_detail", "food_nutrition", "food_record",
        "weight_progress", "user_goal", "exercise_calorie", "exercise_record",
        "health_advice", "out_of_scope", "clarification",
    }
    _ALLOWED_MEALS = {"breakfast", "lunch", "dinner", "snack"}

    def __init__(self, deepseek: DeepSeekClient):
        """注入只负责模型 HTTP 调用的客户端。"""
        self.deepseek = deepseek

    async def parse(self, message: str) -> tuple[SemanticResult, ModelCallResult]:
        """请求模型输出受限 JSON，再交给本地代码校验。"""
        result = await self.deepseek.chat([
            {
                "role": "system",
                "content": (
                    "你是健康饮食应用的意图分类器。只输出 JSON，不要 Markdown。"
                    "只能选择 intent: daily_summary, diet_detail, food_nutrition, food_record, "
                    "weight_progress, user_goal, exercise_calorie, exercise_record, health_advice, out_of_scope, clarification。"
                    "food_record 仅限用户明确表示已经吃了或喝了一个食物；一次只允许一个食物。"
                    "若一句话含多个食物，intent 必须是 clarification。"
                    "daily_summary 用于查询今日摄入、目标或剩余热量；diet_detail 用于查询饮食明细；"
                    "food_nutrition 用于查询食物营养；weight_progress 用于查询体重变化；"
                    "user_goal 用于查询用户目标；exercise_calorie 用于查询运动消耗；exercise_record 用于用户明确已完成运动并希望记录。"
                    "health_advice 用于通用健康饮食建议，不读取个人数据，除非请求同时包含查询行为。"
                    "若用户要求根据今天、当前、我的饮食或热量给建议，优先选择对应的查询意图，"
                    "后续系统会读取真实数据再生成建议。"
                    "out_of_scope 用于与健康饮食、运动、体重、目标无关的问题。"
                    "字段固定为 intent, confidence, food_name, meal_type, amount_text, amount_g, metrics, slots, missing_slots, reply；字段名必须是 snake_case。"
                    "meal_type 只能是 breakfast/lunch/dinner/snack 或 null；amount_g 只能是数字或 null。"
                    "metrics 只能包含 intake_calorie, remaining_calorie, target_calorie；没有时返回空数组。"
                    "slots 只能使用 food_name, quantity, unit, amount_g, amount_text, nutrition_metric, exercise_name, duration_min；没有时返回空对象。"
                    "计数单位（个、碗、份、杯等）不能换算为克；没有质量单位时把 amount_g 放入 missing_slots。"
                    "nutrition_metric 只能取 calorie, protein, fat, carbohydrate。"
                    "missing_slots 返回当前意图缺少的必要参数名称。"
                    "reply 仅在 health_advice 或 out_of_scope 时填写一到两句简短回答，其他情况为 null。"
                    "不确定时使用 clarification，并在 reply 中简短追问。"
                ),
            },
            {"role": "user", "content": message},
        ], json_mode=True)
        return self._parse_result(result.reply), result

    def _parse_result(self, raw: str) -> SemanticResult:
        """校验模型 JSON 的意图、置信度和可接受参数范围。"""
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("模型没有返回合法 JSON") from exc
        intent = body.get("intent")
        if intent not in self._ALLOWED_INTENTS:
            raise ValueError("模型返回了不支持的意图")
        confidence = float(body.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise ValueError("模型置信度不合法")
        # 内部统一 snake_case；旧 camelCase 字段只在这个边界读取一次。
        meal_type = body.get("meal_type", body.get("mealType"))
        if meal_type is not None and meal_type not in self._ALLOWED_MEALS:
            meal_type = None
        amount_g = body.get("amount_g", body.get("amountG"))
        if amount_g is not None:
            if isinstance(amount_g, bool) or not isinstance(amount_g, (int, float)):
                amount_g = None
            else:
                amount_g = float(amount_g)
                if amount_g <= 0 or amount_g > 99999.99 or amount_g != amount_g:
                    amount_g = None
        metrics = body.get("metrics")
        if not isinstance(metrics, list):
            metrics = []
        # 过滤模型编造的指标，后续只接受代码支持的枚举值。
        metrics = tuple(item for item in metrics if item in {"intake_calorie", "remaining_calorie", "target_calorie"})
        missing_slots = body.get("missing_slots", body.get("missingSlots"))
        if not isinstance(missing_slots, list):
            missing_slots = []
        missing_slots = tuple(item.strip() for item in missing_slots if isinstance(item, str) and item.strip())
        return SemanticResult(
            intent=intent,
            confidence=confidence,
            food_name=self._optional_text(body.get("food_name", body.get("foodName"))),
            meal_type=meal_type,
            amount_text=self._optional_text(body.get("amount_text", body.get("amountText"))),
            amount_g=amount_g,
            metrics=metrics,
            missing_slots=missing_slots,
            slots=body.get("slots") if isinstance(body.get("slots"), dict) else None,
            reply=self._optional_text(body.get("reply")),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """将空白或非字符串字段规范为 None。"""
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
