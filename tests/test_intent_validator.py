import json
import unittest

from app.services.intent_validator import (
    ALLOWED_INTENTS,
    normalize_semantic_result,
    parse_semantic_json,
    strip_code_fence,
)


class IntentValidatorTests(unittest.TestCase):
    def test_plain_json_is_parsed(self):
        body = parse_semantic_json('{"intent": "food_record", "confidence": 0.9}')
        self.assertEqual("food_record", body["intent"])

    def test_code_fence_is_stripped(self):
        raw = '```json\n{"intent": "daily_summary", "confidence": 0.8}\n```'
        self.assertEqual('{"intent": "daily_summary", "confidence": 0.8}', strip_code_fence(raw))
        body = parse_semantic_json(raw)
        self.assertEqual("daily_summary", body["intent"])

    def test_noise_around_json_is_tolerated(self):
        raw = '好的，结果如下：{"intent": "user_goal"} 以上'
        body = parse_semantic_json(raw)
        self.assertEqual("user_goal", body["intent"])

    def test_unparsable_content_returns_none(self):
        self.assertIsNone(parse_semantic_json("抱歉，我无法判断"))
        self.assertIsNone(parse_semantic_json(""))

    def test_unknown_intent_falls_back_to_clarification(self):
        result = normalize_semantic_result({"intent": "eaten_food", "confidence": 0.9})
        self.assertEqual("clarification", result["intent"])
        self.assertIn(result["intent"], ALLOWED_INTENTS)

    def test_out_of_range_amount_and_duration_are_dropped(self):
        result = normalize_semantic_result({
            "intent": "food_record",
            "confidence": 0.7,
            "slots": {"food_name": "米饭", "amount_g": 999999, "duration_min": 99999},
        })
        self.assertEqual("米饭", result["slots"]["food_name"])
        self.assertNotIn("amount_g", result["slots"])
        self.assertNotIn("duration_min", result["slots"])

    def test_invalid_meal_and_unknown_slot_keys_are_filtered(self):
        result = normalize_semantic_result({
            "intent": "diet_detail",
            "mealType": "midnight",
            "slots": {"sql": "drop table user", "food_name": "燕麦"},
        })
        self.assertEqual({"food_name": "燕麦"}, result["slots"])

    def test_valid_slots_and_meal_are_kept(self):
        result = normalize_semantic_result({
            "intent": "food_record",
            "confidence": 0.95,
            "mealType": "lunch",
            "slots": {"food_name": "米饭", "amount_g": 200},
        })
        self.assertEqual(200.0, result["slots"]["amount_g"])
        self.assertEqual("lunch", result["slots"]["meal_type"])
        self.assertEqual(0.95, result["confidence"])

    def test_confidence_out_of_range_is_reset(self):
        result = normalize_semantic_result({"intent": "daily_summary", "confidence": 7})
        self.assertEqual(0.0, result["confidence"])

    def test_non_dict_slots_do_not_crash(self):
        result = normalize_semantic_result({"intent": "daily_summary", "slots": "oops"})
        self.assertEqual({}, result["slots"])


if __name__ == "__main__":
    unittest.main()
