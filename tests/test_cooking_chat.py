"""I1：统一聊天里的做菜意图与上下文。

钉住三条语义：
1. 时态不能混：「我想吃红烧肉」是做菜，「我吃了红烧肉」是记录，「红烧肉热量多少」是营养查询。
2. 做菜不缺参数：没有克重也必须能回答，不能被推回"吃了多少克"的追问。
3. 复合请求（记录 + 做菜）不互相截断：做菜先答，记录缺的那一项放末尾补问。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sqlalchemy import select

from app.db.base import Base
from app.models.diet_assistant import Recipe, RecipeIngredient, RecipeStep
from app.models.food import FoodItem, FoodServing
from app.services import cooking_service
from app.services.ai_service import clarification_reply, pending_record_question, resolve_needs
from app.services.cooking_service import CookingService
from app.services.intent_classifier import _intent_payload, classify_message
from app.services.intent_router import normalize_intents
from app.services.intent_validator import normalize_semantic_result


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session


def _intents(text: str) -> list[dict]:
    return [_intent_payload(item) for item in normalize_intents(text)]


def _names(text: str) -> list[str]:
    return [item["intent"] for item in _intents(text)]


# ---------------- 意图边界 ----------------


def test_dish_question_routes_to_cooking():
    result = _intents("红烧肉怎么做")
    assert [item["intent"] for item in result] == ["cooking"]
    assert result[0]["slots"]["dish_name"] == "红烧肉"
    assert result[0]["slots"]["subtype"] == "dish"
    # 做菜没有必填参数，不能产生"还差多少克"的追问。
    assert result[0]["missing_slots"] == []


def test_tense_is_not_confused():
    assert _names("我想吃鸡腿") == ["cooking"]
    assert _names("我吃了鸡腿") == ["food_record"]
    assert _names("鸡腿热量多少") == ["food_nutrition"]


def test_ingredient_mode_keeps_every_ingredient():
    result = _intents("辣椒、五花肉、鸡腿能做什么")
    assert result[0]["intent"] == "cooking"
    assert result[0]["slots"]["subtype"] == "ingredients"
    assert result[0]["slots"]["ingredients"] == ("辣椒", "五花肉", "鸡腿")


def test_followup_and_constraints_are_extracted():
    result = _intents("花生过敏，红烧肉怎么做，2人份")
    slots = result[0]["slots"]
    assert slots["allergens"] == ("花生",)
    assert slots["servings"] == 2
    followup = _intents("就做第二道，没有生抽")[0]
    assert followup["slots"]["subtype"] == "followup"
    # 「没有生抽」是缺料，不是把生抽当成已有食材。
    assert "ingredients" not in followup["slots"]


def test_composite_intents_keep_cooking_and_record():
    assert _names("今天吃了米饭，晚上鸡腿怎么做") == ["food_record", "cooking"]


def test_cooking_never_blocks_on_missing_amount():
    classification = {"intents": _intents("今天吃了米饭，晚上鸡腿怎么做"), "reply": "「米饭」大约吃了多少克？"}
    # 有做菜子请求时不能直接下发追问，否则用户只看到"米饭多少克"。
    assert clarification_reply(classification) is None
    pending = pending_record_question(classification)
    assert pending is not None and "米饭" in pending


def test_semantic_path_accepts_cooking_and_drops_bad_slots():
    normalized = normalize_semantic_result(
        {
            "intent": "cooking",
            "confidence": 0.9,
            "slots": {
                "dish_name": "红烧肉",
                "subtype": "dish",
                "servings": 3,
                "ingredients": "五花肉、 葱、",
                "allergens": ["花生"],
                "amount_g": 500,
            },
        }
    )
    assert normalized["intent"] == "cooking"
    assert normalized["missing_slots"] == []
    assert normalized["slots"]["ingredients"] == ("五花肉", "葱")
    assert normalized["slots"]["servings"] == 3


def test_cooking_need_is_selected():
    classification = {"intents": _intents("红烧肉怎么做")}
    assert "cooking" in resolve_needs(classification, "红烧肉怎么做")
    # 做菜不应该顺带拉营养文献检索。
    assert "knowledge" not in resolve_needs(classification, "红烧肉怎么做")


# ---------------- 上下文构建 ----------------


async def _recipe(db, name: str, ingredients: list[tuple[str, str]]) -> Recipe:
    recipe = Recipe(name=name, scope="system", source_type="sample", servings=2)
    db.add(recipe)
    await db.flush()
    for index, (item_name, grams) in enumerate(ingredients):
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                original_name=item_name,
                gram_equivalent=Decimal(grams),
                sort_order=index,
            )
        )
    db.add(RecipeStep(recipe_id=recipe.id, step_index=1, description="热锅下油，煸香调料。"))
    await db.commit()
    return recipe


@pytest.fixture
def no_knowledge(monkeypatch):
    captured: dict = {}

    async def fake_search(settings, query, top_k, user_id=None, *, domain=None):
        captured.update(query=query, domain=domain)
        return cooking_service.knowledge_client.RagResult.empty()

    monkeypatch.setattr(cooking_service.knowledge_client, "search", fake_search)
    return captured


@pytest.mark.asyncio
async def test_context_uses_recipes_and_marks_missing_evidence(db, no_knowledge):
    await _recipe(db, "红烧肉", [("五花肉", "500")])
    ctx = await CookingService(db, _settings(), 1).build("红烧肉怎么做", dish_name="红烧肉")

    assert ctx.status == "partial"  # 有菜谱、没有烹饪资料
    assert [item["name"] for item in ctx.candidates] == ["红烧肉"]
    assert "红烧肉" in ctx.text
    assert "五花肉 500g" in ctx.text
    assert "没有可引用的烹饪资料" in ctx.text
    # 资料域必须显式指定，不能落回营养文献。
    assert no_knowledge["domain"] == "cooking"


@pytest.mark.asyncio
async def test_no_unrelated_fallback_when_dish_missing(db, no_knowledge):
    await _recipe(db, "红烧肉", [("五花肉", "500")])
    ctx = await CookingService(db, _settings(), 1).build("麻婆豆腐怎么做", dish_name="麻婆豆腐")

    # 关键回归：找不到就返回空，不能拿库里的红烧肉凑数。
    assert ctx.status == "no_evidence"
    assert ctx.candidates == []
    assert "不要凭记忆编一道菜" in ctx.text


@pytest.mark.asyncio
async def test_allergen_candidate_is_excluded(db, no_knowledge):
    await _recipe(db, "花生炖猪蹄", [("花生", "100"), ("猪蹄", "500")])
    ctx = await CookingService(db, _settings(), 1).build(
        "花生炖猪蹄怎么做", dish_name="花生炖猪蹄", allergens=["花生"]
    )
    assert ctx.candidates == []
    assert ctx.status == "no_evidence"


@pytest.mark.asyncio
async def test_followup_skips_retrieval(db, no_knowledge):
    await _recipe(db, "红烧肉", [("五花肉", "500")])
    ctx = await CookingService(db, _settings(), 1).build("就做第二道，没有生抽", subtype="followup")
    assert ctx.status == "skipped"
    assert "沿用对话历史" in ctx.text
    assert no_knowledge == {}  # 纯追问不该再触发一次检索


def _settings():
    from app.config import Settings

    return Settings.from_env()


@pytest.fixture
def mixed_knowledge(monkeypatch):
    """同域但不同菜的资料：cross-encoder 会把它判成能回答，编排层要挡掉。"""
    seen: dict = {}

    async def fake_search(settings, query, top_k, user_id=None, *, domain=None):
        seen["query"] = query
        rag = cooking_service.knowledge_client
        good = rag.RagHit(
            chunk_id="recipe-hongshaorou-000", document_id="recipe-hongshaorou",
            title="红烧肉（家常焖烧）", heading_path=("红烧肉（家常焖烧）",),
            text="【食材】五花肉 500 克", source_url="", needs_review=True,
            score=0.9, vector_score=0.8, rank=1,
        )
        other = rag.RagHit(
            chunk_id="recipe-congbaoniurou-000", document_id="recipe-congbaoniurou",
            title="葱爆牛肉", heading_path=("葱爆牛肉",),
            text="【食材】牛里脊 300 克", source_url="", needs_review=True,
            score=0.85, vector_score=0.6, rank=2,
        )
        return rag.RagResult(answerable=True, hits=[good, other], rerank="local:bge")

    monkeypatch.setattr(cooking_service.knowledge_client, "search", fake_search)
    # 返回本次检索的入参，供用例检查用的到底是原话还是菜名。
    return seen


@pytest.mark.asyncio
async def test_named_dish_drops_other_dishes_from_evidence(db, mixed_knowledge):
    await _recipe(db, "红烧肉", [("五花肉", "500")])
    ctx = await CookingService(db, _settings(), 1).build("红烧肉怎么做", dish_name="红烧肉")

    titles = [hit.title for hit in ctx.hits]
    assert titles == ["红烧肉（家常焖烧）"], "点名红烧肉时不能把葱爆牛肉当资料"
    assert "葱爆牛肉" not in ctx.text


def test_clause_is_trimmed_from_dish_name():
    """「鸡腿除了红烧还能怎么做」不能把从句一起当菜名，否则检索必然零命中。"""
    items = [item for item in normalize_intents("鸡腿除了红烧还能怎么做") if item.intent == "cooking"]
    assert items[0].slots["dish_name"] == "鸡腿"


@pytest.mark.asyncio
async def test_knowledge_query_uses_full_message(db, mixed_knowledge):
    """只拿菜名去检索信息量太少，会被证据门槛判成不相关。"""
    await _recipe(db, "红烧肉", [("五花肉", "500")])
    ctx = await CookingService(db, _settings(), 1).build("红烧肉怎么做", dish_name="红烧肉")
    assert ctx.hits, "完整问句应能取到菜谱资料"
    assert mixed_knowledge["query"] == "红烧肉怎么做"


@pytest.mark.asyncio
async def test_alternatives_keep_other_dishes(db, mixed_knowledge):
    """问「除了红烧还能怎么做」时不能只留同名菜，否则替代方案全被删掉。"""
    ctx = await CookingService(db, _settings(), 1).build(
        "鸡腿除了红烧还能怎么做", dish_name="鸡腿"
    )
    assert len(ctx.hits) == 2


@pytest.mark.asyncio
async def test_unresolved_followup_skips_retrieval(db, mixed_knowledge):
    """没补全的追问（无历史可用）照旧沿用对话历史，不拿原句去撞检索。"""
    ctx = await CookingService(db, _settings(), 1).build(
        "没有生抽了怎么办", subtype="followup"
    )
    assert ctx.status == "skipped"
    assert ctx.hits == []


@pytest.mark.asyncio
async def test_resolved_followup_does_retrieve(db, mixed_knowledge):
    """补全成独立问句后必须真的检索，否则追问只能靠模型记忆编替代方案。"""
    ctx = await CookingService(db, _settings(), 1).build(
        "宫保鸡丁没有生抽怎么办", subtype="followup", resolved_query=True
    )
    assert ctx.hits, "补全后的追问应能取到菜谱依据"
    assert ctx.status != "skipped"


class _CapturingGateway:
    def __init__(self, settings, result: dict) -> None:
        self._result = result
        self.seen: dict = {}

    async def execute(self, payload, trace_id=""):
        self.seen["content"] = payload["messages"][0]["content"]
        return {"choices": [{"message": {"content": __import__("json").dumps(self._result, ensure_ascii=False)}}]}


@pytest.mark.asyncio
async def test_semantic_classification_receives_history(monkeypatch):
    """「不放辣」单看一句判不出意图，必须把上文一起给语义模型。"""
    from app.services import intent_classifier

    gateway = _CapturingGateway(
        _settings(),
        {"intent": "cooking", "confidence": 0.8, "slots": {"subtype": "followup"}, "missing_slots": [], "reply": None},
    )
    monkeypatch.setattr(intent_classifier, "ModelGateway", lambda settings: gateway)

    result = await intent_classifier.classify_message(
        _settings(),
        "不放辣",
        history=["[user] 有辣椒、五花肉、鸡腿", "[assistant] 第一道红烧肉，第二道宫保鸡丁"],
    )

    assert "近期对话" in gateway.seen["content"]
    assert "宫保鸡丁" in gateway.seen["content"]
    assert result["intents"][0]["intent"] == "cooking"


@pytest.mark.asyncio
async def test_semantic_classification_without_history_is_single_sentence(monkeypatch):
    """没有历史时不加前缀，保持原有的单句分类口径。"""
    from app.services import intent_classifier

    gateway = _CapturingGateway(
        _settings(),
        {"intent": "clarification", "confidence": 0.4, "slots": {}, "missing_slots": [], "reply": None},
    )
    monkeypatch.setattr(intent_classifier, "ModelGateway", lambda settings: gateway)

    await intent_classifier.classify_message(_settings(), "不放辣")

    assert gateway.seen["content"] == "不放辣"


@pytest.mark.asyncio
async def test_dish_name_from_history_does_not_filter_hits(db, mixed_knowledge):
    """菜名来自历史时不能做同菜过滤：追问命中的是《食材搭配与替换》，不含菜名。"""
    ctx = await CookingService(db, _settings(), 1).build(
        "不放辣", dish_name="红烧肉", subtype="followup"
    )
    assert len(ctx.hits) == 2, "本句没提红烧肉，不能按红烧肉过滤掉替换资料"


@pytest.mark.asyncio
async def test_dish_named_in_message_still_filters(db, mixed_knowledge):
    """本句真的点了菜名，仍要过滤掉同门类的其它菜。"""
    ctx = await CookingService(db, _settings(), 1).build(
        "红烧肉怎么做", dish_name="红烧肉", subtype="dish"
    )
    assert [hit.title for hit in ctx.hits] == ["红烧肉（家常焖烧）"]


@pytest.mark.asyncio
async def test_serving_mapping_fills_amount_g(db):
    """库里已有"1 个鸡蛋=50g"，就不该再追问克数。"""
    from app.services.food_service import FoodService

    db.add(FoodItem(name="鸡蛋", category="protein", unit="个",
                    calorie_per_100g=Decimal("143"), status="active"))
    await db.flush()
    food = (await db.execute(select(FoodItem).where(FoodItem.name == "鸡蛋"))).scalar_one()
    db.add(FoodServing(food_item_id=food.id, serving_name="个", gram_weight=Decimal("50")))
    await db.flush()

    grams = await FoodService(db).grams_for_serving("鸡蛋", 2, "个")
    assert grams == 100


@pytest.mark.asyncio
async def test_serving_mapping_absent_returns_none(db):
    """没有映射时必须返回 None 让调用方继续追问，不能按经验值猜。"""
    from app.services.food_service import FoodService

    db.add(FoodItem(name="燕麦片", category="grain", unit="克",
                    calorie_per_100g=Decimal("377"), status="active"))
    await db.flush()

    assert await FoodService(db).grams_for_serving("燕麦片", 1, "碗") is None
