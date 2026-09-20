import unittest

from app.services.intent_result import IntentResult
from app.services.semantic_parser import SemanticParser


class SemanticParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = SemanticParser(None)

    def test_accepts_query_intent_and_normalizes_metrics(self):
        result = self.parser._parse_result(
            '{"intent":"daily_summary","confidence":0.91,'
            '"metrics":["intake_calorie","remaining_calorie","unknown"],'
            '"missingSlots":[]}'
        )
        normalized = IntentResult.from_semantic(result)
        self.assertEqual(normalized.intent, "daily_summary")
        self.assertEqual(normalized.behavior, "query")
        self.assertEqual(normalized.slots["metrics"], ("intake_calorie", "remaining_calorie"))

    def test_rejects_unknown_intent(self):
        with self.assertRaises(ValueError):
            self.parser._parse_result('{"intent":"delete_user","confidence":0.99}')

    def test_invalid_confidence_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parser._parse_result('{"intent":"health_advice","confidence":1.2}')


if __name__ == "__main__":
    unittest.main()
