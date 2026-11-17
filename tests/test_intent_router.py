"""规则意图回归测试：单位识别、缺参追问、指标枚举与统一校验。

这些用例对应 M0 已修复的实际问题，改动规则或校验时必须同步维护。
"""

import unittest

from app.services.intent_result import IntentResult
from app.services.intent_router import (
    build_clarification,
    detect_intents,
    normalize_intents,
    parse_quantity,
)
from app.services.intent_validator import (
    canonicalize_slots,
    normalize_semantic_result,
)


def classify(message: str):
    """返回 [(intent, slots, missing_slots)]，便于逐项断言。"""
    return [(item.intent, dict(item.slots), item.missing_slots) for item in normalize_intents(message)]


class UnitRecognitionTests(unittest.TestCase):
    def test_count_unit_is_not_treated_as_grams(self):
        # 原问题：“我吃了2个鸡蛋”被解析成 2 克、「个鸡蛋」。
        results = classify("我吃了2个鸡蛋")
        self.assertEqual(1, len(results))
        intent, slots, missing = results[0]
        self.assertEqual("food_record", intent)
        self.assertEqual("鸡蛋", slots["food_name"])
        self.assertEqual(2.0, slots["quantity"])
        self.assertEqual("个", slots["unit"])
        self.assertNotIn("amount_g", slots)
        self.assertIn("amount_g", missing)

    def test_mass_units_convert_to_grams(self):
        for message, expected in (
            ("我吃了100克米饭", 100.0),
            ("我吃了0.2公斤米饭", 200.0),
            ("我吃了2两米饭", 100.0),
        ):
            with self.subTest(message=message):
                _, slots, missing = classify(message)[0]
                self.assertEqual(expected, slots["amount_g"])
                self.assertEqual((), missing)

    def test_half_bowl_asks_for_grams(self):
        _, slots, missing = classify("我吃了半碗米饭")[0]
        self.assertEqual(0.5, slots["quantity"])
        self.assertEqual("碗", slots["unit"])
        self.assertIn("amount_g", missing)

    def test_volume_is_not_silently_converted(self):
        _, slots, missing = classify("我喝了250毫升牛奶")[0]
        self.assertEqual("牛奶", slots["food_name"])
        self.assertNotIn("amount_g", slots)
        self.assertIn("amount_g", missing)

    def test_chinese_numerals_are_parsed(self):
        self.assertEqual(2.0, parse_quantity("两"))
        self.assertEqual(0.5, parse_quantity("半"))
        self.assertEqual(12.0, parse_quantity("十二"))
        self.assertIsNone(parse_quantity("很多"))


class ClarificationTests(unittest.TestCase):
    def test_missing_amount_produces_natural_question(self):
        results = normalize_intents("我吃了2个鸡蛋")
        reply = build_clarification(results)
        self.assertIsNotNone(reply)
        self.assertIn("克", reply)
        self.assertIn("鸡蛋", reply)

    def test_missing_duration_produces_duration_question(self):
        results = normalize_intents("我今天跑步了")
        self.assertEqual(("duration_min",), results[0].missing_slots)
        self.assertIn("分钟", build_clarification(results))

    def test_no_clarification_when_slots_complete(self):
        self.assertIsNone(build_clarification(normalize_intents("跑步30分钟消耗多少")))


class NutritionMetricTests(unittest.TestCase):
    def test_chinese_metric_is_normalized_to_enum(self):
        _, slots, _ = classify("鸡蛋的热量是多少")[0]
        self.assertEqual("calorie", slots["nutrition_metric"])
        _, slots, _ = classify("鸡蛋的蛋白质")[0]
        self.assertEqual("protein", slots["nutrition_metric"])

    def test_summary_metric_is_not_a_nutrition_metric(self):
        # 每日汇总枚举与食物营养枚举不能混用。
        slots, _ = canonicalize_slots("food_nutrition", {"food_name": "鸡蛋", "nutrition_metric": "intake_calorie"})
        self.assertNotIn("nutrition_metric", slots)


class SharedValidationTests(unittest.TestCase):
    def test_rule_slots_are_canonicalized(self):
        # 规则路径也必须经过同一套范围校验，非法值被丢弃后转为缺参。
        from app.services.intent_router import IntentMatch

        match = IntentMatch("food_record", "prepare_diet_record", 95, None, {"food_name": "米饭", "amount_g": -5})
        result = IntentResult.from_rule(match)
        self.assertNotIn("amount_g", result.slots)
        self.assertIn("amount_g", result.missing_slots)

    def test_boolean_amount_is_rejected(self):
        result = normalize_semantic_result({
            "intent": "food_record",
            "slots": {"food_name": "米饭", "amount_g": True},
        })
        self.assertNotIn("amount_g", result["slots"])
        self.assertIn("amount_g", result["missing_slots"])

    def test_infinite_amount_is_rejected(self):
        result = normalize_semantic_result({
            "intent": "food_record",
            "slots": {"food_name": "米饭", "amount_g": float("inf")},
        })
        self.assertNotIn("amount_g", result["slots"])

    def test_missing_slots_uses_snake_case_and_accepts_legacy_field(self):
        modern = normalize_semantic_result({
            "intent": "food_record",
            "slots": {"food_name": "鸡蛋", "quantity": 2, "unit": "个"},
            "missing_slots": ["amount_g"],
        })
        self.assertEqual(["amount_g"], modern["missing_slots"])

        legacy = normalize_semantic_result({
            "intent": "food_record",
            "slots": {"food_name": "鸡蛋"},
            "missingSlots": ["amount_g"],
        })
        self.assertEqual(["amount_g"], legacy["missing_slots"])

    def test_missing_slots_is_derived_when_model_omits_it(self):
        result = normalize_semantic_result({
            "intent": "food_record",
            "slots": {"food_name": "鸡蛋"},
        })
        self.assertIn("amount_g", result["missing_slots"])

    def test_rule_intent_keeps_daily_summary_unaffected(self):
        results = classify("今天还能吃多少热量")
        self.assertEqual("daily_summary", results[0][0])
        self.assertEqual(("remaining_calorie",), results[0][1]["metrics"])


if __name__ == "__main__":
    unittest.main()
