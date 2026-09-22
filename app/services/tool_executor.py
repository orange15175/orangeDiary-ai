from app.services.intent_router import IntentMatch


class ToolExecutor:
    """Execute only locally approved Java read tools."""

    def __init__(self, java_backend):
        """注入 Java 数据读取客户端。"""
        self.java_backend = java_backend

    async def execute(self, intent: IntentMatch, authorization: str) -> dict:
        """按白名单把统一意图转为对应 Java 查询调用。"""
        factories = {
            "daily_summary": self.java_backend.get_today_summary,
            "diet_detail": self.java_backend.get_today_diet_detail,
            "weight_progress": self.java_backend.get_weight_progress,
            "user_goal": self.java_backend.get_user_goal,
            "food_nutrition": self.java_backend.search_food_nutrition,
            "exercise_calorie": self.java_backend.search_exercise_calorie,
        }
        params = dict(intent.params)
        if intent.name == "daily_summary":
            params.pop("metrics", None)
        return await factories[intent.name](authorization, **params)
