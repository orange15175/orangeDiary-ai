import unittest

from app.services.intent_router import build_clarification, detect_intents


class IntentRouterTest(unittest.TestCase):
    def test_multiple_independent_intents_are_kept(self):
        intents = detect_intents("我今天吃了啥，还能吃多少热量？")
        self.assertEqual([intent.name for intent in intents], ["daily_summary", "diet_detail"])

    def test_food_nutrition_extracts_name_amount_and_metric(self):
        intents = detect_intents("200克鸡胸肉多少热量？")
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].name, "food_nutrition")
        self.assertEqual(intents[0].params["food_name"], "鸡胸肉")
        self.assertEqual(intents[0].params["amount_g"], 200.0)

    def test_exercise_without_duration_returns_clarification(self):
        intents = detect_intents("跑步消耗多少热量？")
        self.assertEqual(intents[0].name, "exercise_calorie")
        self.assertEqual(build_clarification(intents), "你大约进行了多少分钟？")

    def test_daily_question_does_not_be_mistaken_for_food_lookup(self):
        intents = detect_intents("我今天还能吃多少热量？")
        self.assertEqual([intent.name for intent in intents], ["daily_summary"])

    def test_no_intent_returns_empty_list(self):
        self.assertEqual(detect_intents("你好"), [])


if __name__ == "__main__":
    unittest.main()
