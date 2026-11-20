"""普通聊天单测不应消耗本地正在使用的 Redis 配额。"""
import pytest


@pytest.fixture(autouse=True)
def isolate_chat_quota(request, monkeypatch):
    if request.node.get_closest_marker("integration") or request.node.get_closest_marker("live"):
        return
    from app.services import ai_service

    class OfflineQuota:
        async def acquire(self, user_id, is_admin=False):
            return None

        async def refund(self, ticket):
            pass

    # 配额自身的测试仍直接使用 AiQuota + 假 Redis，保持其限流断言有效。
    monkeypatch.setattr(ai_service, "build_quota", lambda settings: OfflineQuota())
