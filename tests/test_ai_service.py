"""AI 聊天编排测试（SQLite + 假模型网关）。

这里钉的是**顺序与降级方向**，不是文案：

- 配额必须比意图识别先扣（意图识别本身也调模型，放后面等于留免费口子）。
- 用户消息必须在模型调用前落库（模型失败的 502 不该连带回滚提问）。
- 检索的三种失败要分开：`unavailable` / `busy` / `no_evidence`。混成一个
  "没有资料"会让用户在服务挂掉时被告知"知识库没有相关内容"。
- 模型失败必须退还配额，否则一次上游抖动就白扣用户一次额度。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.core.errors import BusinessError
from app.db.base import Base
from app.models.ai import AiAnswerSource, AiChatTrace, AiConversationMessage
from app.models.health import ExerciseItem
from app.models.food import FoodItem
from app.models.user import User
from app.services import ai_conversation, ai_service, knowledge_client
from app.services.ai_conversation import ConversationService, default_conversation_id
from app.services.ai_service import (
    Knowledge,
    AiChatService,
    clarification_reply,
    needs_daily_summary,
    needs_knowledge,
    resolve_needs,
)
from app.services.intent_router import is_future_plan
from app.services.knowledge_client import RagHit, RagResult
from app.services.model_gateway import ModelFailure

SETTINGS = Settings.from_env()


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session


class FakeQuota:
    """记录扣/退次数；`order` 与 classify 共用，用来验证先后顺序。"""

    def __init__(self, order: list[str] | None = None) -> None:
        self.acquired = 0
        self.refunded = 0
        self.order = order if order is not None else []

    async def acquire(self, user_id: int, is_admin: bool = False):
        self.acquired += 1
        self.order.append("quota")
        return f"ticket-{user_id}"

    async def refund(self, ticket) -> None:
        self.refunded += 1


def reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def use_gateway(monkeypatch, replies: list) -> list[dict]:
    """把模型调用换成一串预定回复；返回调用体列表供断言。"""
    calls: list[dict] = []

    class FakeGateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            calls.append(body)
            item = replies[min(len(calls) - 1, len(replies) - 1)]
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(ai_service, "ModelGateway", FakeGateway)
    return calls


def patch_classify(monkeypatch, result: dict, order: list[str] | None = None) -> None:
    async def fake_classify(settings, message, trace_id="", history=None):
        if order is not None:
            order.append("classify")
        return result

    monkeypatch.setattr(ai_service, "classify_message", fake_classify)


def make_service(db, factory, quota: FakeQuota | None = None) -> AiChatService:
    return AiChatService(
        db,
        SETTINGS,
        1,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=quota or FakeQuota(),
    )


async def make_user(db) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        nickname="橙子",
        status="active",
        role="user",
    )
    db.add(user)
    await db.commit()
    return user


async def make_food(db, name: str = "米饭") -> FoodItem:
    item = FoodItem(
        name=name,
        category="主食",
        calorie_per_100g=Decimal("116.00"),
        protein_per_100g=Decimal("2.60"),
        fat_per_100g=Decimal("0.30"),
        carb_per_100g=Decimal("25.90"),
        status="active",
    )
    db.add(item)
    await db.commit()
    return item


# ---------------- 纯函数 ----------------


def test_keyword_fallbacks() -> None:
    assert needs_daily_summary("我今天还能吃多少")
    assert needs_daily_summary("今天摄入多少热量")
    # "鸡蛋的热量"只是问食物，不该当成今日汇总。
    assert not needs_daily_summary("鸡蛋的热量是多少")
    assert needs_knowledge("中国居民膳食指南推荐每天吃多少主食")
    # 单纯问热量不该拉到文献检索上。
    assert not needs_knowledge("100 克米饭多少卡")


def test_resolve_needs_from_intents() -> None:
    classification = {
        "intents": [
            {"intent": "daily_summary"},
            {"intent": "food_nutrition"},
            {"intent": "user_goal"},
            {"intent": "health_advice"},
        ]
    }
    assert resolve_needs(classification, "随便问问") == {
        "daily_summary",
        "food_nutrition",
        "user_profile",
        "knowledge",
    }


def test_resolve_needs_adds_knowledge_for_evidence_questions() -> None:
    """命中意图不等于不需要文献：weight_progress 也要补一次检索。"""
    classification = {"intents": [{"intent": "weight_progress"}]}
    assert resolve_needs(classification, "减重期间主食应该吃多少克") == {
        "user_profile",
        "knowledge",
    }


def test_clarification_prefers_intent_reply() -> None:
    assert clarification_reply({"intents": [{"intent": "food_record", "missing_slots": ["amount_g"]}], "reply": "大约多少克？"}) == "大约多少克？"
    assert clarification_reply({"intents": [{"intent": "food_record", "missing_slots": ["amount_g"]}]}) == "请补充更具体的信息，我才能准确回答。"
    assert clarification_reply({"intents": [{"intent": "clarification"}]}) == "请再具体描述一下你想记录或查询的内容。"
    assert clarification_reply({"intents": [{"intent": "daily_summary"}]}) is None
    assert clarification_reply({}) is None


def test_knowledge_defaults_are_skipped() -> None:
    knowledge = Knowledge()
    assert knowledge.status == "skipped"
    assert knowledge.sources == []


# ---------------- 主链路 ----------------


@pytest.mark.asyncio
async def test_chat_returns_reply_and_persists_history(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(),
    )
    patch_classify(monkeypatch, {"intents": [{"intent": "out_of_scope"}]})
    calls = use_gateway(monkeypatch, [reply("建议多吃蔬菜。")])

    result = await svc.chat("给我一点建议")

    assert result["reply"] == "建议多吃蔬菜。"
    assert result["conversationId"] == default_conversation_id(user.id)
    # 没有命中检索/预取时不该带 sources 与用户数据段落。
    assert "sources" not in result
    assert result["knowledgeStatus"] == "skipped"
    assert "【用户真实数据】" not in calls[0]["messages"][-1]["content"]

    rows = await ConversationService(db, SETTINGS, session_factory=factory).recent_messages(user.id, 10)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].content == "建议多吃蔬菜。"


@pytest.mark.asyncio
async def test_quota_is_acquired_before_intent(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    order: list[str] = []
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(order),
    )
    patch_classify(monkeypatch, {}, order)
    use_gateway(monkeypatch, [reply("好")])

    await svc.chat("你好")

    assert order[:2] == ["quota", "classify"]


@pytest.mark.asyncio
async def test_today_calorie_question_prefetches_summary(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(),
    )
    patch_classify(monkeypatch, {"intents": [{"intent": "daily_summary"}]})
    calls = use_gateway(monkeypatch, [reply("还能吃一些。")])

    await svc.chat("我今天还能吃多少热量")

    context = calls[0]["messages"][-1]["content"]
    assert "【用户真实数据】" in context
    assert "今日热量汇总" in context
    assert "请优先依据以上信息回答本次用户问题" in context


@pytest.mark.asyncio
async def test_missing_slot_asks_clarification_without_model(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    quota = FakeQuota()
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=quota,
    )
    patch_classify(
        monkeypatch,
        {
            "intents": [{"intent": "food_record", "missing_slots": ["amount_g"]}],
            "reply": "「面条」大约吃了多少克？",
        },
    )
    calls = use_gateway(monkeypatch, [reply("不该被调用")])

    result = await svc.chat("我吃了碗面条")

    assert result == {"reply": "「面条」大约吃了多少克？", "conversationId": default_conversation_id(user.id)}
    assert calls == []
    # 非流式通道在追问时不退额度——与 Java 一致（流式通道会退）。
    assert quota.refunded == 0

    rows = await ConversationService(db, SETTINGS, session_factory=factory).recent_messages(user.id, 10)
    assert [row.role for row in rows] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_blank_reply_is_502_and_refunds(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    quota = FakeQuota()
    svc = AiChatService(db, SETTINGS, user.id, quota=quota)
    patch_classify(monkeypatch, {})
    use_gateway(monkeypatch, [reply("   ")])

    with pytest.raises(BusinessError) as error:
        await svc.chat("你好")

    assert error.value.code == 502
    assert error.value.message == "AI 返回了空回答"
    assert quota.refunded == 1


@pytest.mark.asyncio
async def test_model_failure_refunds_quota(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    quota = FakeQuota()
    svc = AiChatService(db, SETTINGS, user.id, quota=quota)
    patch_classify(monkeypatch, {})
    use_gateway(monkeypatch, [ModelFailure("upstream_timeout", 504)])

    with pytest.raises(BusinessError) as error:
        await svc.chat("你好")

    assert error.value.code == 502
    assert error.value.message == "AI 服务调用失败"
    assert quota.refunded == 1


@pytest.mark.asyncio
async def test_tool_call_round_feeds_result_back(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "daily_summary"}]})
    calls = use_gateway(
        monkeypatch,
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "getTodaySummary", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            reply("今天还能吃一些。"),
        ],
    )

    result = await svc.chat("我今天还能吃多少")

    assert result["reply"] == "今天还能吃一些。"
    assert len(calls) == 2
    second_messages = calls[1]["messages"]
    assert second_messages[1]["role"] == "assistant"
    assert second_messages[2]["role"] == "tool"
    assert "今日热量汇总" in second_messages[2]["content"]


@pytest.mark.asyncio
async def test_unknown_tool_name_does_not_break_answer(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {})
    calls = use_gateway(
        monkeypatch,
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {"name": "getWeather", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            reply("我不确定。"),
        ],
    )

    result = await svc.chat("明天天气")

    assert result["reply"] == "我不确定。"
    assert "未知工具" in calls[1]["messages"][2]["content"]


# ---------------- 知识检索三态 ----------------

@pytest.mark.asyncio
async def test_knowledge_search_uses_rewritten_query(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "health_advice"}]})

    seen: dict[str, str] = {}

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        return RagResult(answerable=True, hits=[], rerank=None, took_ms=0, cache="miss")

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        return "11月18日最多吃多少"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)
    use_gateway(monkeypatch, [reply("好。")])

    await svc.chat("那一天最多吃多少")

    assert seen["query"] == "11月18日最多吃多少"


@pytest.mark.asyncio
async def test_knowledge_search_falls_back_to_original_query(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "health_advice"}]})

    seen: dict[str, str] = {}

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        return RagResult(answerable=True, hits=[], rerank=None, took_ms=0, cache="miss")

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        return None  # 改写失败/不需要时退回原问题

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)
    use_gateway(monkeypatch, [reply("好。")])

    await svc.chat("膳食指南推荐主食吃多少")

    assert seen["query"] == "膳食指南推荐主食吃多少"


@pytest.mark.asyncio
async def test_cooking_followup_rewrites_query_before_retrieval(db, factory, monkeypatch) -> None:
    """「没有生抽了怎么办」自身不含菜名，必须先补全再检索，否则必然零命中。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "cooking", "slots": {"subtype": "followup"}}]})

    calls: dict = {}

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        calls["message"] = message
        calls["force"] = force
        return "宫保鸡丁没有生抽怎么办"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)

    seen: dict = {}

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        seen["domain"] = domain
        return RagResult(
            answerable=True,
            hits=[
                RagHit(
                    chunk_id="recipe-c-000",
                    document_id="recipe-c",
                    title="红烧肉（家常焖烧）",
                    heading_path=("红烧肉（家常焖烧）",),
                    text="【食材】五花肉 500 克",
                    source_url="",
                    needs_review=True,
                    score=0.9,
                    vector_score=0.8,
                    rank=1,
                )
            ],
            rerank=None,
            took_ms=0,
            cache="miss",
        )

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)
    use_gateway(monkeypatch, [reply("用盐补咸味。")])

    await svc.chat("没有生抽了怎么办")

    assert calls["force"] is True, "做菜追问要强制改写，指代词表抓不到这类句子"
    assert seen["query"] == "宫保鸡丁没有生抽怎么办"
    assert seen["domain"] == "cooking"


@pytest.mark.asyncio
async def test_named_dish_does_not_trigger_rewrite(db, factory, monkeypatch) -> None:
    """点了菜名就句子自身够用，不必多打一次改写模型（多一次调用就多一段延迟）。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {"intents": [{"intent": "cooking", "slots": {"dish_name": "红烧肉", "subtype": "dish"}}]},
    )

    async def boom(*args, **kwargs):
        raise AssertionError("明确菜名不应触发改写")

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", boom)

    seen: dict = {}

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        return RagResult(answerable=True, hits=[], rerank=None, took_ms=0, cache="miss")

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)
    use_gateway(monkeypatch, [reply("红烧肉做法如下。")])

    await svc.chat("红烧肉怎么做")

    assert seen["query"] == "红烧肉怎么做"


def test_ordinal_dish_counts_as_followup() -> None:
    """「第二道」是相对说法，不能当菜名直接去检索。"""
    assert ai_service._needs_history_for_dish("第二道", [], "第二道怎么做") is True
    assert ai_service._needs_history_for_dish("红烧肉", [], "红烧肉怎么做") is False
    # 菜名来自历史（本句没提它）时仍要补全
    assert ai_service._needs_history_for_dish("红烧肉", [], "不放辣") is True
    assert ai_service._needs_history_for_dish(None, ["辣椒", "五花肉"], "有辣椒、五花肉") is False


@pytest.mark.asyncio
async def test_ordinal_followup_keeps_retrieved_hits(db, factory, monkeypatch) -> None:
    """「第二道怎么做」补全检索后，序数不能被当成菜名过滤掉全部命中。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {"intents": [{"intent": "cooking", "slots": {"dish_name": "第二道", "subtype": "followup"}}]},
    )

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        return "宫保鸡丁怎么做"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)

    seen: dict = {}

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        return RagResult(
            answerable=True,
            hits=[
                knowledge_client.RagHit(
                    chunk_id="recipe-gongbaojiding-000",
                    document_id="recipe-gongbaojiding",
                    title="宫保鸡丁", heading_path=("宫保鸡丁",),
                    text="【食材】鸡腿肉 300 克", source_url="", needs_review=True,
                    score=0.9, vector_score=0.8, rank=1,
                )
            ],
            rerank=None, took_ms=0, cache="miss",
        )

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)
    use_gateway(monkeypatch, [reply("宫保鸡丁做法如下。")])

    await svc.chat("第二道怎么做")

    assert seen["query"] == "宫保鸡丁怎么做"


def fake_search(result):
    # 生产侧 search 现在必带 domain（营养文献固定 nutrition），替身必须同签名，
    # 否则报的是 TypeError 而不是断言失败。
    async def search(settings, query, top_k, user_id=None, *, domain=None):
        return result

    return search


@pytest.mark.asyncio
async def test_knowledge_ok_returns_sources(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "health_advice"}]})
    hit = RagHit(
        chunk_id="c1",
        document_id="d1",
        title="中国居民膳食指南",
        heading_path=("谷类", "摄入量"),
        text="每天摄入谷类 200~300 克。",
        source_url="",
        needs_review=False,
        score=0.82,
        vector_score=0.79,
        rank=1,
    )
    monkeypatch.setattr(
        ai_service.knowledge_client,
        "search",
        fake_search(RagResult(answerable=True, hits=[hit], rerank="local:bge", took_ms=37, cache="miss")),
    )
    calls = use_gateway(monkeypatch, [reply("建议每天 200~300 克。")])

    result = await svc.chat("膳食指南推荐主食吃多少")

    assert result["knowledgeStatus"] == "ok"
    assert result["sources"][0]["title"] == "中国居民膳食指南"
    assert result["sources"][0]["excerpt"].startswith("每天摄入谷类")
    assert "【知识库资料】" in calls[0]["messages"][-1]["content"]

    # 来源快照必须在回答落库时一起写入，否则索引更新后旧回答解释不了引用。
    rows = (await db.scalars(select(AiAnswerSource).order_by(AiAnswerSource.id))).all()
    assert len(rows) == 1
    assert rows[0].chunk_id == "c1"
    assert rows[0].title == "中国居民膳食指南"
    assert rows[0].index_version == SETTINGS.rag_index_version
    assert rows[0].message_id is not None
    assert "200~300" in calls[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_knowledge_unavailable_is_not_no_evidence(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "health_advice"}]})
    monkeypatch.setattr(ai_service.knowledge_client, "search", fake_search(None))
    calls = use_gateway(monkeypatch, [reply("暂时无法核实。")])

    result = await svc.chat("膳食指南推荐主食吃多少")

    assert result["knowledgeStatus"] == "unavailable"
    assert "sources" not in result
    assert "检索服务不可用" in calls[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_knowledge_busy_asks_to_retry(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "health_advice"}]})
    monkeypatch.setattr(
        ai_service.knowledge_client, "search", fake_search(RagResult.saturated(1.5))
    )
    calls = use_gateway(monkeypatch, [reply("稍后再问。")])

    result = await svc.chat("膳食指南推荐主食吃多少")

    assert result["knowledgeStatus"] == "busy"
    assert "排队已满" in calls[0]["messages"][-1]["content"]


# ---------------- 记录预览 ----------------


@pytest.mark.asyncio
async def test_diet_preview_from_intent(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    food = await make_food(db, "米饭")
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {
            "intents": [
                {
                    "intent": "food_record",
                    "slots": {"food_name": "米饭", "amount_g": 150},
                    "missing_slots": [],
                }
            ]
        },
    )
    use_gateway(monkeypatch, [reply("已找到米饭，确认后保存。")])

    result = await svc.chat("我吃了 150 克米饭")

    assert result["action"] == "confirm_diet_record"
    preview = result["recordPreview"]
    assert preview["quantityG"] == Decimal("150")
    assert preview["selectedFoodId"] == food.id
    assert preview["candidates"][0]["name"] == "米饭"
    assert preview["mealType"] in {"breakfast", "lunch", "snack", "dinner"}
    # recordTime 是 Java LocalDateTime.toString 形态，不带时区后缀。
    assert "T" in preview["recordTime"]
    assert "+08:00" not in preview["recordTime"]


@pytest.mark.asyncio
async def test_diet_preview_regex_fallback(db, factory, monkeypatch) -> None:
    """意图层挂了（空结果）时，正则兜底仍要给出预览。"""
    user = await make_user(db)
    await make_food(db, "米饭")
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {})
    use_gateway(monkeypatch, [reply("好的。")])

    result = await svc.chat("我吃了 150 克米饭")

    assert result["action"] == "confirm_diet_record"
    assert result["recordPreview"]["quantityG"] == Decimal("150")


@pytest.mark.asyncio
async def test_diet_preview_without_match_returns_plain_reply(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {
            "intents": [
                {
                    "intent": "food_record",
                    "slots": {"food_name": "不存在的食物", "amount_g": 100},
                    "missing_slots": [],
                }
            ]
        },
    )
    use_gateway(monkeypatch, [reply("没找到这种食物。")])

    result = await svc.chat("我吃了 100 克不存在的食物")

    assert "action" not in result
    assert result["reply"] == "没找到这种食物。"


@pytest.mark.asyncio
async def test_exercise_preview_unknown_item_replaces_reply(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {
            "intents": [
                {
                    "intent": "exercise_record",
                    "slots": {"exercise_name": "倒立跑", "duration_min": 30},
                    "missing_slots": [],
                }
            ]
        },
    )
    use_gateway(monkeypatch, [reply("已记录。")])

    result = await svc.chat("我倒立跑了 30 分钟")

    assert "action" not in result
    assert result["reply"] == "没有找到「倒立跑」对应的运动项目，请换一个名称再试。"


@pytest.mark.asyncio
async def test_exercise_preview_matches_item(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    db.add(
        ExerciseItem(
            name="慢跑",
            category="cardio",
            calorie_per_unit=Decimal("8.00"),
            unit_type="minute",
            met_value=Decimal("7.00"),
            status="active",
        )
    )
    await db.commit()
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {
            "intents": [
                {
                    "intent": "exercise_record",
                    "slots": {"exercise_name": "慢跑", "duration_min": 30},
                    "missing_slots": [],
                }
            ]
        },
    )
    # 回答不能为空：非流式通道对空回答会直接 502，走不到预览组装。
    # （Java 里 buildExercisePreview 那个"reply 为空就补一句"的分支因此在非流式下不可达，
    #   只有流式通道可能触发；这里保留该分支并锁住可达路径。）
    use_gateway(monkeypatch, [reply("好的，请确认时长。")])

    result = await svc.chat("我慢跑了 30 分钟")

    assert result["action"] == "confirm_exercise_record"
    assert result["reply"] == "好的，请确认时长。"
    assert result["exercisePreview"]["durationMin"] == Decimal("30")
    assert result["exercisePreview"]["candidates"][0]["name"] == "慢跑"


# ---------------- 追踪 ----------------


@pytest.mark.asyncio
async def test_trace_is_written(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "daily_summary"}]})
    use_gateway(monkeypatch, [reply("还能吃 300 千卡。")])

    await svc.chat("我今天还能吃多少热量")

    trace = await db.scalar(select(AiChatTrace))
    assert trace is not None
    assert trace.user_id == user.id
    assert trace.intent == "daily_summary"
    assert trace.question_excerpt == "我今天还能吃多少热量"
    assert trace.source_count == 0
    assert trace.total_ms is not None


@pytest.mark.asyncio
async def test_trace_failure_does_not_break_answer(db, factory, monkeypatch) -> None:
    """追踪是运维数据，写失败也不能把已经生成好的回答变成错误。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {})
    use_gateway(monkeypatch, [reply("好的。")])

    async def boom(self, **kwargs):
        raise RuntimeError("库挂了")

    from app.services.ai_trace_service import AiChatTraceService

    monkeypatch.setattr(AiChatTraceService, "record", boom)

    result = await svc.chat("你好")
    assert result["reply"] == "好的。"


# ---------------- 流式 ----------------


def stream_frames(*chunks: dict, done: bool = True) -> list[str]:
    frames = [f"data: {__import__('json').dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    if done:
        frames.append("data: [DONE]\n\n")
    return frames


def token_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


class FakeStreamGateway:
    """流式网关：按顺序把帧吐出来。"""

    def __init__(self, settings) -> None:
        self.bodies: list[dict] = []

    async def execute(self, body, trace_id):
        self.bodies.append(body)
        frames = body.pop("__frames")

        async def generator():
            for frame in frames:
                yield frame

        return generator()


@pytest.mark.asyncio
async def test_stream_yields_tokens_and_persists(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(),
    )
    patch_classify(monkeypatch, {})
    gateway = FakeStreamGateway(SETTINGS)
    monkeypatch.setattr(ai_service, "ModelGateway", lambda settings: gateway)

    async def fake_execute(body, trace_id):
        gateway.bodies.append(body)
        frames = stream_frames(token_chunk("多"), token_chunk("吃蔬菜"))

        async def generator():
            for frame in frames:
                yield frame

        return generator()

    gateway.execute = fake_execute

    frames = [frame async for frame in svc.chat_stream("给点建议")]

    assert any("event: knowledge" in frame for frame in frames)
    assert "event: message\ndata: 多\n\n" in frames
    assert "event: message\ndata: 吃蔬菜\n\n" in frames
    assert any(frame.startswith("event: done") for frame in frames)
    assert gateway.bodies[0]["stream"] is True

    rows = await ConversationService(db, SETTINGS, session_factory=factory).recent_messages(user.id, 10)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].content == "多吃蔬菜"


@pytest.mark.asyncio
async def test_stream_clarification_refunds_quota(db, factory, monkeypatch) -> None:
    """流式通道在追问时会退额度（Java 的两条通道在这里不一致，照原样保留）。"""
    user = await make_user(db)
    quota = FakeQuota()
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=quota,
    )
    patch_classify(
        monkeypatch,
        {
            "intents": [{"intent": "food_record", "missing_slots": ["amount_g"]}],
            "reply": "大约多少克？",
        },
    )

    frames = [frame async for frame in svc.chat_stream("我吃了碗面条")]

    assert frames == [
        "event: message\ndata: 大约多少克？\n\n",
        f"event: done\ndata: {default_conversation_id(user.id)}\n\n",
    ]
    assert quota.refunded == 1


@pytest.mark.asyncio
async def test_stream_error_frame_on_model_failure(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    quota = FakeQuota()
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=quota,
    )
    patch_classify(monkeypatch, {})

    class BrokenGateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            raise ModelFailure("upstream_timeout", 504)

    monkeypatch.setattr(ai_service, "ModelGateway", BrokenGateway)

    frames = [frame async for frame in svc.chat_stream("你好")]

    assert "event: error\ndata: AI 服务调用失败\n\n" in frames
    assert quota.refunded == 1


@pytest.mark.asyncio
async def test_stream_tool_round_then_tokens(db, factory, monkeypatch) -> None:
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(monkeypatch, {"intents": [{"intent": "daily_summary"}]})

    rounds = [
        stream_frames(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "getTodaySummary", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        stream_frames(token_chunk("还能吃一些。")),
    ]
    bodies: list[dict] = []

    class Gateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            bodies.append(body)
            frames = rounds[len(bodies) - 1]

            async def generator():
                for frame in frames:
                    yield frame

            return generator()

    monkeypatch.setattr(ai_service, "ModelGateway", Gateway)

    frames = [frame async for frame in svc.chat_stream("我今天还能吃多少")]
    text = "".join(frame.split("data: ", 1)[1].split("\n")[0] for frame in frames if frame.startswith("event: message"))

    assert text == "还能吃一些。"
    assert len(bodies) == 2
    assert bodies[1]["messages"][2]["role"] == "tool"
    assert "今日热量汇总" in bodies[1]["messages"][2]["content"]


@pytest.mark.asyncio
async def test_stream_disconnect_persists_partial_reply_once(db, factory, monkeypatch) -> None:
    """客户端断开时已生成的部分要落库，且只能落一条。

    生成器被 `aclose()` 会在 yield 处抛 GeneratorExit，这时不能 await 数据库，
    所以走的是"投递后台任务"的路径——这条用例就是钉那个路径真的写进去了。
    """
    user = await make_user(db)
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(),
    )
    patch_classify(monkeypatch, {})

    class Gateway:
        def __init__(self, settings) -> None:
            pass

        async def execute(self, body, trace_id):
            frames = stream_frames(token_chunk("多"), token_chunk("喝水"))

            async def generator():
                for frame in frames:
                    yield frame

            return generator()

    monkeypatch.setattr(ai_service, "ModelGateway", Gateway)

    stream = svc.chat_stream("给点建议")
    async for frame in stream:
        if frame.startswith("event: message"):
            break
    await stream.aclose()

    # 等后台落库任务结束（拿模块级引用等，避免竞态）。
    pending = list(ai_service._background)
    assert pending
    await asyncio.gather(*pending)

    async with factory() as fresh:
        rows = list(
            await fresh.scalars(
                select(AiConversationMessage)
                .where(AiConversationMessage.conversation_id.is_not(None))
                .order_by(AiConversationMessage.message_sequence)
            )
        )
    assistant = [row for row in rows if row.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "多"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_previous_question_uses_exact_transcript_even_after_summary(db, factory, monkeypatch, stream):
    user = await make_user(db)
    conversations = ConversationService(db, SETTINGS, session_factory=factory)
    conversation = await conversations.get_or_create(user.id, default_conversation_id(user.id))
    for role, text in [("user", "减脂期间应该怎么吃"), ("assistant", "均衡搭配"),
                       ("user", "跑步30分钟"), ("assistant", "请确认")]:
        await conversations.append_message(conversation, role, text)
    conversation.summary = "用户关心减脂期间应该怎么吃"
    conversation.summary_covered_sequence = 4
    await db.commit()
    calls = use_gateway(monkeypatch, [AssertionError("回看历史不应调用模型")])
    svc = AiChatService(db, SETTINGS, user.id, conversation_service=conversations, quota=FakeQuota())
    if stream:
        frames = [frame async for frame in svc.chat_stream("我上一个问题是啥")]
        assert "event: message\ndata: 你上一条消息是：跑步30分钟\n\n" in frames
    else:
        assert (await svc.chat("我上一个问题是啥"))["reply"] == "你上一条消息是：跑步30分钟"
    assert calls == []
    # 相同文本的消息也是真实的一轮，不能按内容去重。
    assert (await svc.chat("我上一个问题是啥"))["reply"] == "你上一条消息是：我上一个问题是啥"


@pytest.mark.asyncio
async def test_stream_record_card_uses_real_rules_and_never_saves(db, factory, monkeypatch):
    import json
    from app.models.health import ExerciseRecord
    from app.models.diet import DietRecord
    user = await make_user(db)
    db.add(ExerciseItem(name="跑步", category="cardio", calorie_per_unit=Decimal("8"),
                        unit_type="minute", status="active"))
    await make_food(db)
    await db.commit()
    calls = use_gateway(monkeypatch, [AssertionError("确认卡不应调用生成模型")])
    svc = AiChatService(db, SETTINGS, user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory), quota=FakeQuota())
    for message, action, key, field, amount in [
        ("跑步30分钟", "confirm_exercise_record", "exercisePreview", "durationMin", 30),
        ("我吃了100克米饭", "confirm_diet_record", "recordPreview", "quantityG", 100),
    ]:
        frames = [frame async for frame in svc.chat_stream(message)]
        payload = json.loads(next(frame.split("data: ", 1)[1] for frame in frames if frame.startswith("event: action")))
        assert payload["action"] == action
        assert Decimal(str(payload[key][field])) == amount
        assert any("尚未写入记录" in frame for frame in frames)
        assert frames[-1].startswith("event: done")
    assert calls == []
    assert list(await db.scalars(select(ExerciseRecord))) == []
    assert list(await db.scalars(select(DietRecord))) == []


@pytest.mark.asyncio
async def test_previous_question_empty_history_does_not_use_another_account(db, factory, monkeypatch):
    other = await make_user(db)
    user = await make_user(db)
    conversations = ConversationService(db, SETTINGS, session_factory=factory)
    conversation = await conversations.get_or_create(other.id, default_conversation_id(other.id))
    await conversations.append_message(conversation, "user", "另一账号的消息")
    calls = use_gateway(monkeypatch, [AssertionError("不应调用模型")])
    svc = AiChatService(db, SETTINGS, user.id, conversation_service=conversations, quota=FakeQuota())
    assert (await svc.chat("我上一个问题是啥"))["reply"] == "当前会话中还没有更早的用户消息。"
    assert calls == []


@pytest.mark.asyncio
async def test_stream_composite_keeps_cooking_after_record_card(db, factory, monkeypatch):
    """记录参数齐全时也要照样答做菜，不能出一张记录卡就收尾。"""
    user = await make_user(db)
    await make_food(db, "米饭")
    conversations = ConversationService(db, SETTINGS, session_factory=factory)
    quota = FakeQuota()
    svc = AiChatService(db, SETTINGS, user.id, conversation_service=conversations, quota=quota)
    patch_classify(monkeypatch, {
        "intents": [
            {"intent": "food_record", "slots": {"food_name": "米饭", "amount_g": 200, "quantity": 200, "unit": "克"}, "missing_slots": []},
            {"intent": "cooking", "slots": {"dish_name": "鸡腿", "subtype": "dish"}, "missing_slots": []},
        ],
        "reply": None,
    })

    cooking_calls: list[str] = []

    async def fake_cooking_context(self, message, classification, knowledge, history):
        cooking_calls.append(message)
        return "【候选食谱】鸡腿"

    monkeypatch.setattr(AiChatService, "_cooking_context", fake_cooking_context)

    gateway = FakeStreamGateway(SETTINGS)
    monkeypatch.setattr(ai_service, "ModelGateway", lambda settings: gateway)

    async def fake_execute(body, trace_id):
        gateway.bodies.append(body)
        frames = stream_frames(token_chunk("鸡腿"))

        async def generator():
            for frame in frames:
                yield frame

        return generator()

    gateway.execute = fake_execute

    frames = [frame async for frame in svc.chat_stream("中午吃了200克米饭，晚上鸡腿怎么做")]

    assert cooking_calls, "复合消息里的做菜子请求被吞掉了"
    assert any(frame.startswith("event: action") for frame in frames), "记录卡仍要发给用户"
    assert "event: message\ndata: 鸡腿\n\n" in frames
    assert gateway.bodies[0]["model"] == "cooking-stream"
    # 用了模型就不该退额度。
    assert quota.refunded == 0


@pytest.mark.asyncio
async def test_future_exercise_plan_does_not_build_record_card(db, factory):
    """语义模型误判成 exercise_record 时，出卡前仍要拦住"还没发生"的运动。"""
    user = await make_user(db)
    svc = AiChatService(
        db,
        SETTINGS,
        user.id,
        conversation_service=ConversationService(db, SETTINGS, session_factory=factory),
        quota=FakeQuota(),
    )
    classification = {
        "intents": [
            {"intent": "exercise_record", "slots": {"exercise_name": "跑步", "duration_min": 30}, "missing_slots": []}
        ]
    }
    planned = await svc._build_response("我准备跑步30分钟", "好的", "c1", classification)
    assert "action" not in planned

    # 已完成的句子不能被这道兜底误伤（出卡还依赖用户档案，此处只断言判断本身）。
    assert is_future_plan("我跑完了30分钟") is False
    assert is_future_plan("我准备跑步30分钟") is True


@pytest.mark.asyncio
async def test_history_ingredients_do_not_disable_followup_rewrite(db, factory, monkeypatch) -> None:
    """语义模型会把上文食材塞进本句槽位；不能因此认为"这句自带食材"而跳过补全。

    「不放辣」的真实分类是 ingredients=[辣椒, 五花肉, 鸡腿]（来自历史）+ excluded=[辣椒]，
    若照槽位判断就会拿「不放辣」原句去检索，必然零命中。
    """
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {
            "intents": [
                {
                    "intent": "cooking",
                    "slots": {
                        "ingredients": ["辣椒", "五花肉", "鸡腿"],
                        "excluded_ingredients": ["辣椒"],
                        "subtype": "followup",
                    },
                }
            ]
        },
    )

    seen: dict = {}

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        seen["force"] = force
        return "红烧肉不放辣怎么做"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)

    queries: list[str] = []

    async def recording_search(settings, query, top_k, user_id=None, *, domain=None):
        queries.append(query)
        return RagResult(
            answerable=True,
            hits=[
                RagHit(
                    chunk_id="recipe-hongshaorou-000",
                    document_id="recipe-hongshaorou",
                    title="红烧肉（家常焖烧）",
                    heading_path=("红烧肉（家常焖烧）",),
                    text="【食材】五花肉 500 克",
                    source_url="",
                    needs_review=True,
                    score=0.9,
                    vector_score=0.8,
                    rank=1,
                )
            ],
            rerank=None,
            took_ms=0,
            cache="miss",
        )

    monkeypatch.setattr(ai_service.knowledge_client, "search", recording_search)
    use_gateway(monkeypatch, [reply("红烧肉本身不辣。")])

    await svc.chat("不放辣")

    assert seen.get("force") is True, "槽位里的历史食材不能让追问跳过补全"
    assert queries == ["红烧肉不放辣怎么做"]


@pytest.mark.asyncio
async def test_rewritten_query_falls_back_to_original_when_empty(db, factory, monkeypatch) -> None:
    """补全会猜菜（把"不放辣"补成"宫保鸡丁不放辣"）；猜错零命中时要按原句再查一次。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {"intents": [{"intent": "cooking", "slots": {"dish_name": "红烧肉", "subtype": "followup"}}]},
    )

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        return "宫保鸡丁不放辣怎么做"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)

    queries: list[str] = []

    async def branching_search(settings, query, top_k, user_id=None, *, domain=None):
        queries.append(query)
        if query == "宫保鸡丁不放辣怎么做":
            return RagResult(answerable=False, hits=[], rerank=None, took_ms=0, cache="miss")
        return RagResult(
            answerable=True,
            hits=[
                RagHit(
                    chunk_id="cooking-pairing-guide-001",
                    document_id="cooking-pairing-guide",
                    title="常见食材搭配与替换", heading_path=("常见食材搭配与替换",),
                    text="不吃辣：干辣椒、小米椒直接省略，花椒可以保留提香。",
                    source_url="", needs_review=True, score=0.9, vector_score=0.7, rank=1,
                )
            ],
            rerank=None, took_ms=0, cache="miss",
        )

    monkeypatch.setattr(ai_service.knowledge_client, "search", branching_search)
    use_gateway(monkeypatch, [reply("不放辣就省掉辣椒。")])

    await svc.chat("不放辣")

    assert queries == ["宫保鸡丁不放辣怎么做", "不放辣"], "补全零命中后要用原句重试"


@pytest.mark.asyncio
async def test_rewrite_retries_with_dish_name_when_both_empty(db, factory, monkeypatch) -> None:
    """纯指代句补全后仍零命中时，用本句确认过的菜名兜底，避免"资料里没有"的误报。"""
    user = await make_user(db)
    svc = AiChatService(db, SETTINGS, user.id)
    patch_classify(
        monkeypatch,
        {"intents": [{"intent": "cooking", "slots": {"dish_name": "宫保鸡丁", "subtype": "followup"}}]},
    )

    async def fake_rewrite(settings, message, history, trace_id="", *, force=False):
        return "宫保鸡丁不放辣版怎么做"

    monkeypatch.setattr(ai_service.query_rewriter, "rewrite_query", fake_rewrite)

    queries: list[str] = []

    async def branching_search(settings, query, top_k, user_id=None, *, domain=None):
        queries.append(query)
        if query == "宫保鸡丁怎么做":
            return RagResult(
                answerable=True,
                hits=[
                    RagHit(
                        chunk_id="recipe-gongbaojiding-000",
                        document_id="recipe-gongbaojiding",
                        title="宫保鸡丁", heading_path=("宫保鸡丁",),
                        text="【食材】鸡腿肉 300 克", source_url="", needs_review=True,
                        score=0.9, vector_score=0.8, rank=1,
                    )
                ],
                rerank=None, took_ms=0, cache="miss",
            )
        return RagResult(answerable=False, hits=[], rerank=None, took_ms=0, cache="miss")

    monkeypatch.setattr(ai_service.knowledge_client, "search", branching_search)
    use_gateway(monkeypatch, [reply("宫保鸡丁做法如下。")])

    await svc.chat("第二道怎么做")

    assert queries == ["宫保鸡丁不放辣版怎么做", "第二道怎么做", "宫保鸡丁怎么做"]


@pytest.mark.asyncio
async def test_fill_amount_from_serving_silences_gram_question(db, factory) -> None:
    """「2 个鸡蛋」有份量映射时应直接能出卡，不能反过来问用户多少克。"""
    from decimal import Decimal

    from app.models.food import FoodServing

    user = await make_user(db)
    db.add(FoodItem(name="鸡蛋", category="protein", unit="个",
                    calorie_per_100g=Decimal("143"), status="active"))
    await db.flush()
    food = (await db.execute(select(FoodItem).where(FoodItem.name == "鸡蛋"))).scalar_one()
    db.add(FoodServing(food_item_id=food.id, serving_name="个", gram_weight=Decimal("50")))
    await db.flush()

    svc = AiChatService(db, SETTINGS, user.id)
    classification = {
        "intents": [
            {
                "intent": "food_record",
                "slots": {"food_name": "鸡蛋", "quantity": 2, "unit": "个"},
                "missing_slots": ["amount_g"],
            }
        ],
        "reply": "你说的「2个鸡蛋」大约有多少克？",
    }

    await svc._fill_amount_from_serving(classification)

    assert classification["intents"][0]["slots"]["amount_g"] == 100
    assert classification["intents"][0]["missing_slots"] == []
    assert clarification_reply(classification) is None, "换算出来后不该再追问克数"


@pytest.mark.asyncio
async def test_fill_amount_keeps_question_without_serving_mapping(db, factory) -> None:
    """没有份量映射就保持追问，不按经验值猜克重。"""
    from decimal import Decimal

    user = await make_user(db)
    db.add(FoodItem(name="燕麦片", category="grain", unit="克",
                    calorie_per_100g=Decimal("377"), status="active"))
    await db.flush()

    svc = AiChatService(db, SETTINGS, user.id)
    classification = {
        "intents": [
            {
                "intent": "food_record",
                "slots": {"food_name": "燕麦片", "quantity": 1, "unit": "碗"},
                "missing_slots": ["amount_g"],
            }
        ],
        "reply": "你说的「1碗燕麦片」大约有多少克？",
    }

    await svc._fill_amount_from_serving(classification)

    assert "amount_g" not in classification["intents"][0]["slots"]
    assert clarification_reply(classification) is not None
