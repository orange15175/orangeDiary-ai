import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.semantic_parser import SemanticResult


class FakeJavaBackend:
    def __init__(self):
        self.calls = []
        self.match_response = {"matchType": "EXACT", "normalizedName": "", "candidates": []}

    async def validate_token(self, authorization):
        self.calls.append("validate")

    async def get_user_goal(self, authorization):
        self.calls.append("goal")
        return {
            "goal": {"goalType": "lose", "dailyIntakeTarget": 1600},
            "profile": {"targetWeightKg": 65},
        }

    async def get_today_summary(self, authorization):
        self.calls.append("summary")
        return {"summary": {"availableIntakeCalorie": 800}}

    async def get_today_diet_detail(self, authorization):
        self.calls.append("diet")
        return {"records": []}

    async def get_weight_progress(self, authorization):
        self.calls.append("weight")
        return {"progress": {}}

    async def search_food_nutrition(self, authorization, **kwargs):
        self.calls.append("food")
        return {"candidates": []}

    async def search_exercise_calorie(self, authorization, **kwargs):
        self.calls.append("exercise")
        return {"candidates": []}

    async def match_food(self, authorization, food_name):
        self.calls.append("match_food")
        response = dict(self.match_response)
        response["normalizedName"] = response["normalizedName"] or food_name
        return response


class FakeDeepSeek:
    def __init__(self):
        self.called = False

    async def chat(self, messages, json_mode=False):
        self.called = True
        return SimpleNamespace(reply="模型回答", prompt_tokens=10, completion_tokens=5, total_tokens=15)


class FakeSemanticParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="out_of_scope", confidence=0.99, reply="这是简短回答。"),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeFoodRecordParser:
    async def parse(self, message):
        return (
            SemanticResult(
                intent="food_record",
                confidence=0.98,
                food_name="黄瓜",
                meal_type="dinner",
                amount_text="一根",
            ),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeClarificationParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="clarification", confidence=0.98, reply="一次只能记录一种食物，请分开说。"),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def build_service():
    # 绕开真实 HTTP 客户端，只验证 ChatService 的编排顺序和分支。
    service = ChatService.__new__(ChatService)
    service.settings = SimpleNamespace(
        deepseek_api_key="test-key",
        deepseek_model="test-model",
        deepseek_input_price_per_million=1,
        deepseek_output_price_per_million=1,
    )
    service.java_backend = FakeJavaBackend()
    service.deepseek = FakeDeepSeek()
    service.semantic_parser = FakeSemanticParser()
    return service


class ChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_intent_validates_then_returns_model_fallback_without_business_tool(self):
        service = build_service()

        result = await service.chat(ChatRequest(message="\u4f60\u597d"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.model, "test-model")
        self.assertIn("\u8fd9\u662f\u7b80\u77ed\u56de\u7b54", result.reply)
        self.assertIn("Orange Diary", result.reply)

    async def test_missing_token_stops_before_any_backend_call(self):
        service = build_service()

        with self.assertRaises(HTTPException) as context:
            await service.chat(ChatRequest(message="\u4f60\u597d"), None)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(service.java_backend.calls, [])

    async def test_multiple_intents_query_data_after_validation_then_call_model(self):
        service = build_service()

        result = await service.chat(
            ChatRequest(message="\u6211\u4eca\u5929\u5403\u4e86\u5565\uff0c\u8fd8\u80fd\u5403\u591a\u5c11\u70ed\u91cf\uff1f"),
            "Bearer valid-token",
        )

        self.assertEqual(service.java_backend.calls[0], "validate")
        self.assertCountEqual(service.java_backend.calls[1:], ["summary", "diet"])
        self.assertTrue(service.deepseek.called)
        self.assertEqual(result.reply, "模型回答")

    async def test_food_record_returns_confirmation_preview_without_writing_database(self):
        service = build_service()
        service.semantic_parser = FakeFoodRecordParser()
        service.java_backend.match_response = {
            "matchType": "EXACT",
            "normalizedName": "黄瓜",
            "candidates": [{
                "id": 7,
                "name": "黄瓜",
                "caloriePer100g": 16,
                "proteinPer100g": 0.7,
                "fatPer100g": 0.1,
                "carbPer100g": 3.6,
                "servings": [{"id": 1, "servingName": "根", "gramWeight": 200}],
            }],
        }

        result = await service.chat(ChatRequest(message="晚饭吃了一根黄瓜"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate", "match_food"])
        self.assertEqual(result.action, "confirm_diet_record")
        self.assertEqual(result.recordPreview["selectedFoodId"], 7)
        self.assertEqual(result.recordPreview["quantityG"], 200)
        self.assertTrue(result.recordPreview["quantityEstimated"])

    async def test_multiple_foods_only_returns_clarification_without_preview_or_write(self):
        service = build_service()
        service.semantic_parser = FakeClarificationParser()

        result = await service.chat(ChatRequest(message="我吃了黄瓜和鸡蛋"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.model, "rule-based")
        self.assertIsNone(result.action)
        self.assertIn("一次只能记录一种食物", result.reply)


if __name__ == "__main__":
    unittest.main()
