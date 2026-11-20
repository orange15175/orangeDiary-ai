"""AI 助手 HTTP 契约测试。

重点不在"能不能聊"，而在几个前端会直接踩到的边界：

- 未登录取历史必须 401（Java 的拦截器先于控制器）。
- 消息为空 / 超长必须 400 且文案是中文原文——422 会让前端把它当成系统错误弹窗。
- 流式响应的 `Content-Type` 必须带 `charset=utf-8`；少了它中文 token 会被按
  ISO-8859-1 解成问号，而且只在流式出现。
- `FEATURES_AI_CHAT_ENABLED=false` 时三个端点都 404，与 Java 的
  `@ConditionalOnProperty` 一致。
"""
from __future__ import annotations

import base64
import json
import re
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_session_store, get_settings
from app.main import create_app
from app.models.food import FoodItem
from app.services import ai_service
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore


def build_settings(**overrides) -> Settings:
    base = Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"ai-api-secret-key-32-bytes!!").decode("ascii"),
    )
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


@pytest_asyncio.fixture
async def context():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    store = InMemoryAuthSessionStore()
    settings = build_settings()

    session.add(
        FoodItem(
            name="米饭",
            category="谷物",
            unit="g",
            calorie_per_100g=Decimal("116.00"),
            protein_per_100g=Decimal("2.60"),
            fat_per_100g=Decimal("0.30"),
            carb_per_100g=Decimal("25.90"),
            status="active",
        )
    )
    await session.commit()

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_store] = lambda: store

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http

    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def headers(context):
    http = context
    body = {
        "username": "AI橙子",
        "email": "ai-api@example.com",
        "password": "orange123",
        "confirmPassword": "orange123",
    }
    await http.post("/api/auth/register", json=body)
    tokens = (
        await http.post(
            "/api/auth/login", json={"account": "ai-api@example.com", "password": "orange123"}
        )
    ).json()["data"]
    return {"Authorization": f"Bearer {tokens['token']}"}


def use_text_model(monkeypatch, text: str = "建议多吃蔬菜。") -> list[dict]:
    calls: list[dict] = []

    class Gateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            calls.append(body)
            return {"choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(ai_service, "ModelGateway", Gateway)
    return calls


def use_stream_model(monkeypatch, tokens: list[str]) -> None:
    class Gateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            frames = [
                "data: " + json.dumps({"choices": [{"delta": {"content": token}}]}, ensure_ascii=False) + "\n\n"
                for token in tokens
            ] + ["data: [DONE]\n\n"]

            async def generator():
                for frame in frames:
                    yield frame

            return generator()

    monkeypatch.setattr(ai_service, "ModelGateway", Gateway)


# ---------------- 鉴权与校验 ----------------


@pytest.mark.asyncio
async def test_chat_requires_login(context):
    response = await context.post("/api/ai/chat", json={"message": "你好"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_history_requires_login(context):
    response = await context.get("/api/ai/conversation/messages")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_blank_message_returns_400(context, headers):
    response = await context.post("/api/ai/chat", headers=headers, json={"message": "   "})
    assert response.status_code == 400
    assert response.json()["message"] == "消息内容不能为空"


@pytest.mark.asyncio
async def test_too_long_message_returns_400(context, headers):
    response = await context.post("/api/ai/chat", headers=headers, json={"message": "字" * 4001})
    assert response.status_code == 400
    assert response.json()["message"] == "消息内容不能超过4000个字符"


@pytest.mark.asyncio
async def test_disabled_feature_returns_404(context, headers, monkeypatch):
    app = context._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: build_settings(ai_chat_enabled=False)

    response = await context.post("/api/ai/chat", headers=headers, json={"message": "你好"})
    assert response.status_code == 404

    history = await context.get("/api/ai/conversation/messages", headers=headers)
    assert history.status_code == 404


# ---------------- 非流式 ----------------


@pytest.mark.asyncio
async def test_chat_returns_reply_and_conversation_id(context, headers, monkeypatch):
    use_text_model(monkeypatch, "今天还可以吃 300 千卡。")

    response = await context.post(
        "/api/ai/chat", headers=headers, json={"message": "我今天还能吃多少"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["reply"] == "今天还可以吃 300 千卡。"
    # 会话 ID 是用户 ID 派生的 UUID v3，格式固定 36 位。
    assert re.fullmatch(r"[0-9a-f-]{36}", body["data"]["conversationId"])
    assert body["data"]["knowledgeStatus"] == "skipped"


@pytest.mark.asyncio
async def test_deprecated_conversation_id_is_ignored(context, headers, monkeypatch):
    """老前端仍会带 conversationId，不能因此 400，也不能用它换会话。"""
    use_text_model(monkeypatch, "好的。")

    response = await context.post(
        "/api/ai/chat",
        headers=headers,
        json={"message": "你好", "conversationId": "legacy-session-id"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["conversationId"] != "legacy-session-id"


@pytest.mark.asyncio
async def test_model_failure_returns_502(context, headers, monkeypatch):
    class Broken:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            raise ai_service.ModelFailure("upstream_timeout", 504)

    monkeypatch.setattr(ai_service, "ModelGateway", Broken)

    response = await context.post("/api/ai/chat", headers=headers, json={"message": "你好"})

    assert response.status_code == 502
    assert response.json()["message"] == "AI 服务调用失败"


@pytest.mark.asyncio
async def test_diet_preview_is_json_serializable(context, headers, monkeypatch):
    """预览里的 Decimal 必须能被编码成 JSON 数字，否则前端拿到 500。"""
    use_text_model(monkeypatch, "已找到米饭，请确认。")

    response = await context.post(
        "/api/ai/chat", headers=headers, json={"message": "我吃了 150 克米饭"}
    )

    assert response.status_code == 200
    preview = response.json()["data"]["recordPreview"]
    assert preview["quantityG"] == 150
    assert preview["candidates"][0]["name"] == "米饭"
    assert preview["selectedFoodId"] == preview["candidates"][0]["id"]


# ---------------- 历史 ----------------


@pytest.mark.asyncio
async def test_history_starts_empty(context, headers):
    response = await context.get("/api/ai/conversation/messages", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_history_returns_role_content_and_time(context, headers, monkeypatch):
    use_text_model(monkeypatch, "建议多喝水。")
    await context.post("/api/ai/chat", headers=headers, json={"message": "你好"})

    response = await context.get("/api/ai/conversation/messages", headers=headers)

    rows = response.json()["data"]
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "你好"
    assert rows[1]["content"] == "建议多喝水。"
    # Java LocalDateTime.toString 形态：日期 + T + 时分，秒为 0 时省略。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?", rows[0]["createdAt"])


@pytest.mark.asyncio
async def test_history_limit_is_clamped(context, headers, monkeypatch):
    use_text_model(monkeypatch, "好")
    await context.post("/api/ai/chat", headers=headers, json={"message": "你好"})

    response = await context.get("/api/ai/conversation/messages?limit=0", headers=headers)
    assert len(response.json()["data"]) == 1


# ---------------- 流式 ----------------


@pytest.mark.asyncio
async def test_stream_returns_sse_with_utf8_charset(context, headers, monkeypatch):
    use_stream_model(monkeypatch, ["控制", "钠的摄入"])

    response = await context.post(
        "/api/ai/chat/stream", headers=headers, json={"message": "怎么控制钠"}
    )

    assert response.status_code == 200
    content_type = response.headers["content-type"]
    # 少了 charset，代理会按 ISO-8859-1 解中文，变成问号。
    assert "text/event-stream" in content_type
    assert "charset=utf-8" in content_type.lower()
    body = response.text
    assert "event: message\ndata: 控制\n\n" in body
    assert "event: message\ndata: 钠的摄入\n\n" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_stream_clarification_has_no_model_call(context, headers, monkeypatch):
    calls = use_text_model(monkeypatch)

    response = await context.post(
        "/api/ai/chat/stream", headers=headers, json={"message": "我吃了碗面条"}
    )

    body = response.text
    assert "event: message" in body
    assert "大约有多少克" in body or "大约吃了多少克" in body
    assert calls == []
