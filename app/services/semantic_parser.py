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
    reply: str | None = None


class SemanticParser:
    """规则无法准确处理时，让模型做意图分类与单食物实体提取。"""

    _ALLOWED_INTENTS = {"food_record", "health_advice", "out_of_scope", "clarification"}
    _ALLOWED_MEALS = {"breakfast", "lunch", "dinner", "snack"}

    def __init__(self, deepseek: DeepSeekClient):
        self.deepseek = deepseek

    async def parse(self, message: str) -> tuple[SemanticResult, ModelCallResult]:
        result = await self.deepseek.chat([
            {
                "role": "system",
                "content": (
                    "你是健康饮食应用的意图分类器。只输出 JSON，不要 Markdown。"
                    "只能选择 intent: food_record, health_advice, out_of_scope, clarification。"
                    "food_record 仅限用户明确表示已经吃了或喝了一个食物；一次只允许一个食物。"
                    "若一句话含多个食物，intent 必须是 clarification。"
                    "health_advice 用于健康饮食建议，但不读取任何个人数据。"
                    "out_of_scope 用于与健康饮食、运动、体重、目标无关的问题。"
                    "字段固定为 intent, confidence, foodName, mealType, amountText, amountG, reply。"
                    "mealType 只能是 breakfast/lunch/dinner/snack 或 null；amountG 只能是数字或 null。"
                    "reply 仅在 health_advice 或 out_of_scope 时填写一到两句简短回答，其他情况为 null。"
                    "不确定时使用 clarification，并在 reply 中简短追问。"
                ),
            },
            {"role": "user", "content": message},
        ], json_mode=True)
        return self._parse_result(result.reply), result

    def _parse_result(self, raw: str) -> SemanticResult:
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
        meal_type = body.get("mealType")
        if meal_type is not None and meal_type not in self._ALLOWED_MEALS:
            meal_type = None
        amount_g = body.get("amountG")
        if amount_g is not None:
            amount_g = float(amount_g)
            if amount_g <= 0 or amount_g > 99999.99:
                amount_g = None
        return SemanticResult(
            intent=intent,
            confidence=confidence,
            food_name=self._optional_text(body.get("foodName")),
            meal_type=meal_type,
            amount_text=self._optional_text(body.get("amountText")),
            amount_g=amount_g,
            reply=self._optional_text(body.get("reply")),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
