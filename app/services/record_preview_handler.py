"""Build safe, user-confirmed previews for diet and exercise records."""

from __future__ import annotations

from datetime import datetime

from app.services.semantic_parser import SemanticResult


class RecordPreviewHandler:
    """This handler never writes a record; the existing Java save API remains the confirmation boundary."""

    def __init__(self, java_backend, semantic_parser, responses):
        """接收食物/运动查询能力与统一响应构造器。"""
        self.java_backend = java_backend
        self.semantic_parser = semantic_parser
        self.responses = responses

    async def prepare_diet_record(self, trace_id, total_start, message, authorization):
        """先提取饮食实体，再交给预览构造流程。"""
        # A rule only says this might be a record. The parser must still extract one food and a quantity.
        try:
            semantic, model_result = await self.semantic_parser.parse(message)
        except ValueError:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], "我没能识别出要记录的食物，请补充食物名称、重量或份量。"
            )
        if semantic.intent != "food_record" or semantic.confidence < 0.70:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [],
                semantic.reply or "请一次告诉我一种食物，例如“晚饭吃了100克黄瓜”。",
            )
        return await self.build_diet_preview(trace_id, total_start, message, authorization, semantic, model_result)

    async def build_diet_preview(self, trace_id, total_start, message, authorization, semantic: SemanticResult, model_result):
        """根据模型实体和 Java 食物匹配结果创建饮食确认预览。"""
        if not semantic.food_name:
            return self.responses.rule_based_response(trace_id, total_start, message, [], "你想记录哪一种食物？请一次输入一种食物。")
        food_match = await self.java_backend.match_food(authorization, semantic.food_name)
        candidates = food_match.get("candidates") or []
        if not candidates:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], f"没有找到“{semantic.food_name}”，请换一个更具体的食物名称。"
            )

        # Select only one exact/alias result. Ambiguous names must remain editable choices for the user.
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
        return self.responses.model_response(
            trace_id, total_start, message, [semantic.intent], model_result, reply,
            "diet_record_preview", ["action"], source="semantic", confidence=semantic.confidence,
            action="confirm_diet_record", record_preview=preview,
        )

    async def build_exercise_preview(self, trace_id, total_start, message, authorization, intent, model_result=None):
        """查询运动候选后创建运动确认预览。"""
        params = getattr(intent, "params", None) or getattr(intent, "slots", {}) or {}
        exercise_name = params.get("exercise_name")
        duration_min = params.get("duration_min")
        if not exercise_name:
            return self.responses.rule_based_response(trace_id, total_start, message, [], "你完成的是哪一种运动？")
        if not duration_min:
            return self.responses.rule_based_response(trace_id, total_start, message, [], "你大约进行了多少分钟？")
        result = await self.java_backend.search_exercise_calorie(
            authorization, exercise_name=exercise_name, duration_min=int(duration_min)
        )
        candidates = result.get("candidates") or []
        if not candidates:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], f"没有找到“{exercise_name}”，请从运动列表中选择。"
            )
        preview = {
            "candidates": candidates,
            "selectedExerciseId": candidates[0].get("id") if len(candidates) == 1 else None,
            "durationMin": int(duration_min),
            "recordTime": datetime.now().replace(microsecond=0).isoformat(),
        }
        reply = (
            f"已找到“{candidates[0].get('name')}”，请确认时长和时间后再保存。"
            if len(candidates) == 1 else "我找到了多个可能的运动项目，请先选择实际完成的是哪一种。"
        )
        if model_result is None:
            return self.responses.rule_based_response(
                trace_id, total_start, message, [], reply,
                action="confirm_exercise_record", exercise_preview=preview,
            )
        return self.responses.model_response(
            trace_id, total_start, message, ["exercise_record"], model_result, reply,
            "exercise_record_preview", ["action"], source="semantic",
            confidence=getattr(intent, "confidence", None), action="confirm_exercise_record", exercise_preview=preview,
        )

    @staticmethod
    def _resolve_quantity(candidate: dict | None, amount_g: float | None, amount_text: str | None) -> tuple[float | None, bool]:
        """优先使用明确克数，否则按食物常用份量估算。"""
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
