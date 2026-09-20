import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.schemas.chat import ChatRequest
from app.services.memory_service import MemoryContext
from app.services.chat_service import ChatService
from app.services.semantic_parser import SemanticResult
from app.services.tool_executor import ToolExecutor


class FakeJavaBackend:
    def __init__(self):
        self.calls = []
        self.match_response = {"matchType": "EXACT", "normalizedName": "", "candidates": []}
        self.exercise_response = {"candidates": []}

    async def validate_token(self, authorization):
        self.calls.append("validate")
        return 7

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
        return self.exercise_response

    async def match_food(self, authorization, food_name):
        self.calls.append("match_food")
        response = dict(self.match_response)
        response["normalizedName"] = response["normalizedName"] or food_name
        return response


class FakeMemoryService:
    def __init__(self):
        self.saved_turns = []
        self.context = MemoryContext(
            conversation=SimpleNamespace(summary="用户偏好简短回答。"),
            messages=(),
        )

    async def load_context(self, user_id):
        self.user_id = user_id
        return self.context

    async def save_completed_turn(self, context, user_message, assistant_message):
        self.saved_turns.append((user_message, assistant_message))


class FakeDeepSeek:
    def __init__(self):
        self.called = False
        self.requests = []

    async def chat(self, messages, json_mode=False):
        self.called = True
        self.requests.append({"messages": messages, "json_mode": json_mode})
        return SimpleNamespace(reply="模型回答", prompt_tokens=10, completion_tokens=5, total_tokens=15)


class FakeSemanticParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="out_of_scope", confidence=0.99, reply="这是简短回答。"),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeHealthAdviceParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="health_advice", confidence=0.98, reply="分类器简短回答。"),
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


class FakeSemanticDailySummaryParser:
    async def parse(self, message):
        return (
            SemanticResult(
                intent="daily_summary",
                confidence=0.92,
                metrics=("remaining_calorie",),
            ),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeSemanticFoodParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="food_nutrition", confidence=0.92),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeLowConfidenceSummaryParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="daily_summary", confidence=0.69),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeLowConfidenceFoodRecordParser:
    async def parse(self, message):
        return (
            SemanticResult(intent="food_record", confidence=0.69, food_name="米饭"),
            SimpleNamespace(reply="{}", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FailingSummaryJavaBackend(FakeJavaBackend):
    async def get_today_summary(self, authorization):
        self.calls.append("summary")
        raise HTTPException(status_code=502, detail="无法读取用户健康数据")


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
    service.tool_executor = ToolExecutor(service.java_backend)
    service.deepseek = FakeDeepSeek()
    service.semantic_parser = FakeSemanticParser()
    service.memory_service = FakeMemoryService()
    return service


class ChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_chat_uses_persisted_memory_without_business_tool(self):
        service = build_service()
        service.memory_service.context = MemoryContext(
            conversation=SimpleNamespace(summary="用户希望减脂。"),
            messages=(
                SimpleNamespace(role="user", content="我前面问过热量。"),
                SimpleNamespace(role="assistant", content="你今天还可摄入 1600 kcal。"),
            ),
        )

        result = await service.chat(ChatRequest(message="\u4f60\u597d"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.reply, "模型回答")
        self.assertFalse(result.context_used)
        self.assertEqual(
            [item["role"] for item in service.deepseek.requests[0]["messages"]],
            ["system", "user", "assistant", "user"],
        )
        self.assertIn("用户希望减脂。", service.deepseek.requests[0]["messages"][0]["content"])
        self.assertEqual(service.deepseek.requests[0]["messages"][-1]["content"], "你好")
        self.assertEqual(service.memory_service.saved_turns, [("你好", result.reply)])

    async def test_health_advice_uses_memory_chat_without_business_tool(self):
        service = build_service()
        service.semantic_parser = FakeHealthAdviceParser()

        result = await service.chat(ChatRequest(message="怎么养成规律饮食习惯"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.reply, "模型回答")
        self.assertTrue(service.deepseek.called)
        self.assertFalse(service.deepseek.requests[0]["json_mode"])

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

    async def test_semantic_query_reads_java_data_then_generates_response(self):
        service = build_service()
        service.semantic_parser = FakeSemanticDailySummaryParser()

        result = await service.chat(ChatRequest(message="我的热量额度还剩多少"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate", "summary"])
        self.assertTrue(service.deepseek.called)
        self.assertTrue(result.context_used)
        self.assertEqual(result.reply, "模型回答")

    async def test_semantic_query_with_missing_required_slot_does_not_call_java_tool(self):
        service = build_service()
        service.semantic_parser = FakeSemanticFoodParser()

        result = await service.chat(ChatRequest(message="这个营养如何"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.model, "rule-based")
        self.assertIn("哪一种食物", result.reply)

    async def test_low_confidence_semantic_query_does_not_call_java_tool(self):
        service = build_service()
        service.semantic_parser = FakeLowConfidenceSummaryParser()

        result = await service.chat(ChatRequest(message="今天大概咋样"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertEqual(result.model, "rule-based")

    async def test_low_confidence_semantic_action_does_not_create_preview_or_call_match(self):
        service = build_service()
        service.semantic_parser = FakeLowConfidenceFoodRecordParser()

        result = await service.chat(ChatRequest(message="我可能吃了点米饭"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate"])
        self.assertIsNone(result.action)

    async def test_semantic_query_backend_failure_does_not_fabricate_answer(self):
        service = build_service()
        service.semantic_parser = FakeSemanticDailySummaryParser()
        service.java_backend = FailingSummaryJavaBackend()
        service.tool_executor = ToolExecutor(service.java_backend)

        result = await service.chat(ChatRequest(message="我的热量额度还剩多少"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate", "summary"])
        self.assertEqual(result.model, "rule-based")
        self.assertIn("暂时无法读取", result.reply)

    async def test_completed_exercise_returns_confirmation_without_writing_database(self):
        service = build_service()
        service.java_backend.exercise_response = {"candidates": [{"id": 3, "name": "跑步", "category": "有氧"}]}

        result = await service.chat(ChatRequest(message="晚上跑步30分钟"), "Bearer valid-token")

        self.assertEqual(service.java_backend.calls, ["validate", "exercise"])
        self.assertEqual(result.action, "confirm_exercise_record")
        self.assertEqual(result.exercisePreview["selectedExerciseId"], 3)
        self.assertEqual(result.exercisePreview["durationMin"], 30)


if __name__ == "__main__":
    unittest.main()
