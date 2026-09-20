import unittest

from app.services.intent_router import detect_intents
from app.services.request_plan import Behavior, RequestPlanBuilder


class RequestPlanTest(unittest.TestCase):
    def setUp(self):
        self.builder = RequestPlanBuilder()

    def test_plain_message_is_chat(self):
        plan = self.builder.build(detect_intents("减脂期间能吃火锅吗"))
        self.assertEqual(plan.behaviors, (Behavior.CHAT,))

    def test_query_and_action_are_composable(self):
        plan = self.builder.build(detect_intents("我今天还能吃多少热量，晚饭吃了100克米饭"))
        self.assertEqual(plan.behaviors, (Behavior.QUERY, Behavior.ACTION))

    def test_daily_summary_metrics_are_merged_into_one_intent(self):
        intents = detect_intents("查一下我今天能吃多少热量，吃了多少热量")
        self.assertEqual([intent.name for intent in intents], ["daily_summary"])
        self.assertEqual(
            intents[0].params["metrics"],
            ("intake_calorie", "remaining_calorie"),
        )


if __name__ == "__main__":
    unittest.main()
