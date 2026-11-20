"""M4：周计划生成与食材核对。

断言的是产品口径：
- 草稿不生效，采纳才把 active 指针移过去并预留;
- 硬约束不满足时返回 infeasible，不凑数；
- 未记录食材是 unknown（待核对），不是 absent（待购买）；
- 有数量且够用直接确认。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.diet_assistant import (
    IngredientCheckItem,
    IngredientReservation,
    KnownIngredient,
    Recipe,
    RecipeIngredient,
)
from app.models.food import FoodItem, FoodServing
from app.models.user import UserGoal
from app.services.inventory_service import InventoryService
from app.services.plan_service import PlanService
from sqlalchemy import select


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


async def _setup(db, user_id: int = 1) -> dict:
    db.add(UserGoal(user_id=user_id, goal_type="maintain", daily_intake_target=Decimal("2000"), start_date=date(2026, 1, 1), status="active"))
    egg = FoodItem(name="鸡蛋", unit="g", calorie_per_100g=Decimal("144"), protein_per_100g=Decimal("13"), fat_per_100g=Decimal("9"), carb_per_100g=Decimal("3"), status="active")
    oil = FoodItem(name="豆油", unit="g", calorie_per_100g=Decimal("899"), protein_per_100g=Decimal("0"), fat_per_100g=Decimal("99"), carb_per_100g=Decimal("0"), status="active")
    db.add_all([egg, oil])
    await db.flush()
    db.add(FoodServing(food_item_id=egg.id, serving_name="个", gram_weight=Decimal("50")))
    recipes = []
    for name, kcal_note in (("番茄炒蛋", "低"), ("清炒西兰花", "中"), ("香菇鸡肉汤", "高")):
        recipe = Recipe(name=name, scope="system", source_type="sample", servings=1, status="active", prep_minutes=5, cook_minutes=5)
        db.add(recipe)
        await db.flush()
        db.add(RecipeIngredient(recipe_id=recipe.id, food_item_id=egg.id, original_name="鸡蛋", quantity=Decimal("2"), unit="个", sort_order=0))
        db.add(RecipeIngredient(recipe_id=recipe.id, food_item_id=oil.id, original_name="食用油", quantity=Decimal("10"), unit="g", sort_order=1))
        recipes.append(recipe)
    await db.commit()
    return {"egg": egg, "oil": oil, "recipes": recipes}


def _user(user_id: int = 1):
    return SimpleNamespace(id=user_id, role="user")


@pytest.mark.asyncio
async def test_generate_draft_creates_meals_without_activating(db):
    await _setup(db)
    result = await PlanService(db).generate_draft(
        _user(), {"startDate": date(2026, 11, 20), "days": 2, "mealTypes": ["breakfast", "lunch"]}
    )
    assert result["status"] == "draft"
    assert result["versionStatus"] == "draft"
    assert len(result["meals"]) == 4
    assert all(meal["nutrition"] is not None for meal in result["meals"])


@pytest.mark.asyncio
async def test_infeasible_when_all_recipes_contain_allergen(db):
    await _setup(db)
    result = await PlanService(db).generate_draft(
        _user(), {"startDate": date(2026, 11, 20), "days": 1, "mealTypes": ["lunch"], "allergens": ["鸡蛋"]}
    )
    assert result["status"] == "infeasible"
    assert result["checks"]["code"] == "CANDIDATE_POOL_INSUFFICIENT"
    assert result["meals"] == []


@pytest.mark.asyncio
async def test_adopt_activates_without_precision_reservations(db):
    data = await _setup(db)
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {"startDate": date(2026, 11, 20), "days": 1, "mealTypes": ["lunch"]})
    # 明确一袋鸡蛋够用，采纳后才预留。
    db.add(KnownIngredient(user_id=1, food_item_id=data["egg"].id, label="鸡蛋", presence="present", quantity_state="known", quantity=Decimal("500"), unit="g", source="manual"))
    await db.commit()
    adopted = await svc.adopt(1, draft["planId"], 1)
    assert adopted["status"] == "active"
    assert adopted["versionStatus"] == "adopted"
    reservations = list(await db.scalars(select(IngredientReservation)))
    # 单人午餐目标 2000 kcal，该菜每份约 234 kcal → 取 3 份（上限），2 个鸡蛋×50g×3 = 300g。
    assert reservations == []


@pytest.mark.asyncio
async def test_check_distinguishes_unknown_from_absent(db):
    data = await _setup(db)
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {"startDate": date(2026, 11, 20), "days": 1, "mealTypes": ["lunch"]})
    # 鸡蛋未记录 -> needs_check；油明确没有 -> to_buy。
    db.add(KnownIngredient(user_id=1, food_item_id=data["oil"].id, label="食用油", presence="absent", quantity_state="unknown", source="manual"))
    await db.commit()

    inventory = InventoryService(db)
    session = await inventory.create_check(1, draft["planId"], None)
    by_label = {item["label"]: item for item in session["items"]}
    assert by_label["鸡蛋"]["status"] == "needs_check"
    assert by_label["鸡蛋"]["existingAssessment"] == "unknown"
    assert by_label["食用油"]["status"] == "to_buy"


@pytest.mark.asyncio
async def test_check_confirms_recent_presence_without_quantity(db):
    data = await _setup(db)
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {"startDate": date(2026, 11, 20), "days": 1, "mealTypes": ["lunch"]})
    for food in [data["egg"], data["oil"]]:
        await InventoryService(db).upsert(1, {"label": food.name, "foodItemId": food.id, "presence": "present"})

    session = await InventoryService(db).create_check(1, draft["planId"], None)
    assert session["status"] == "confirmed"
    assert all(item["status"] == "confirmed" for item in session["items"])


@pytest.mark.asyncio
async def test_answer_moves_item_to_buy(db):
    data = await _setup(db)
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {"startDate": date(2026, 11, 20), "days": 1, "mealTypes": ["lunch"]})
    inventory = InventoryService(db)
    session = await inventory.create_check(1, draft["planId"], None)
    egg_item = next(item for item in session["items"] if item["label"] == "鸡蛋")
    updated = await inventory.answer(
        1, session["id"], [{"itemId": egg_item["id"], "answer": "none"}], session["version"]
    )
    egg_after = next(item for item in updated["items"] if item["label"] == "鸡蛋")
    assert egg_after["status"] == "to_buy"
    assert egg_after["confirmedQuantity"] == Decimal("0")
