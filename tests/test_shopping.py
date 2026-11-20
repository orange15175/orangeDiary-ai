"""M5：采购缺口、价格不可用与幂等购买。

对照设计验收用例：
- A03 需 600g、确认可分配 200g → 缺 400g；
- A04 家中"有一点"（数量未知）→ shortage=null、needs_check，不自动全买；
- A09 有缺价 → priceStatus=unknown、budgetStatus=unverified；
- A07 重复购买同幂等键 → 只入一次库。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.diet_assistant import (
    IngredientLedger,
    KnownIngredient,
    MealPlan,
    MealPlanVersion,
    PlanMeal,
    Recipe,
    RecipeIngredient,
)
from app.models.food import FoodItem
from app.services.shopping_service import ShoppingService


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


async def _plan(db, user_id: int = 1, grams: str = "600") -> dict:
    egg = FoodItem(name="鸡蛋", unit="g", calorie_per_100g=Decimal("144"), protein_per_100g=Decimal("13"), fat_per_100g=Decimal("9"), carb_per_100g=Decimal("3"), status="active")
    db.add(egg)
    await db.flush()
    recipe = Recipe(name="卤蛋", scope="system", source_type="sample", servings=1, status="active")
    db.add(recipe)
    await db.flush()
    db.add(RecipeIngredient(recipe_id=recipe.id, food_item_id=egg.id, original_name="鸡蛋", quantity=Decimal(grams), unit="g", sort_order=0))
    plan = MealPlan(user_id=user_id, start_date=date(2026, 11, 20), end_date=date(2026, 11, 20), status="draft")
    db.add(plan)
    await db.flush()
    version = MealPlanVersion(plan_id=plan.id, version_number=1, status="draft", checks={"code": "OK"})
    db.add(version)
    await db.flush()
    db.add(PlanMeal(plan_version_id=version.id, meal_date=date(2026, 11, 20), meal_type="lunch", recipe_id=recipe.id, servings=1, execution_state="planned"))
    await db.commit()
    return {"egg": egg, "plan": plan, "version": version}


@pytest.mark.asyncio
async def test_old_quantity_is_not_used_as_exact_remaining_stock(db):
    data = await _plan(db, grams="600")
    db.add(KnownIngredient(user_id=1, food_item_id=data["egg"].id, label="鸡蛋", presence="present", quantity_state="known", quantity=Decimal("200"), unit="g", source="manual"))
    await db.commit()

    listing = await ShoppingService(db).build_list(1, data["plan"].id, 1)
    item = listing["items"][0]
    assert item["needQuantity"] == Decimal("600")
    assert item["availableQuantity"] is None
    assert item["shortageQuantity"] is None
    assert item["state"] == "needs_check"


@pytest.mark.asyncio
async def test_unknown_quantity_is_not_zero(db):
    data = await _plan(db, grams="600")
    db.add(KnownIngredient(user_id=1, food_item_id=data["egg"].id, label="鸡蛋", presence="present", quantity_state="unknown", source="manual"))
    await db.commit()

    listing = await ShoppingService(db).build_list(1, data["plan"].id, 1)
    item = listing["items"][0]
    assert item["shortageQuantity"] is None
    assert item["quantityStatus"] == "unknown"
    assert item["state"] == "needs_check"


@pytest.mark.asyncio
async def test_price_unavailable_is_explicit(db):
    data = await _plan(db)
    listing = await ShoppingService(db).build_list(1, data["plan"].id, 1)
    assert listing["totals"]["priceStatus"] == "unknown"
    assert listing["totals"]["budgetStatus"] == "unverified"
    assert listing["items"][0]["price"]["priceStatus"] == "unknown"


@pytest.mark.asyncio
async def test_purchase_is_idempotent_and_remembers_purchase_without_grams(db):
    data = await _plan(db)
    db.add(KnownIngredient(user_id=1, food_item_id=data["egg"].id, label="鸡蛋", presence="present", quantity_state="known", quantity=Decimal("200"), unit="g", source="manual"))
    await db.commit()

    service = ShoppingService(db)
    listing = await service.build_list(1, data["plan"].id, 1)
    item = listing["items"][0]
    assert item["buyQuantity"] is None

    await service.purchase(1, item["id"], actual_quantity=Decimal("400"), paid_price=None, add_to_ingredients=True, request_key="k1")
    known = await db.scalar(select(KnownIngredient).where(KnownIngredient.user_id == 1))
    assert known.quantity is None
    assert known.presence == "present"
    assert known.source == "purchase"

    # 同键重复提交：不再入库、不再记流水。
    await service.purchase(1, item["id"], actual_quantity=Decimal("400"), paid_price=None, add_to_ingredients=True, request_key="k1")
    known = await db.scalar(select(KnownIngredient).where(KnownIngredient.user_id == 1))
    assert known.quantity is None
    assert known.presence == "present"
    assert known.source == "purchase"
    ledgers = list(await db.scalars(select(IngredientLedger)))
    assert len(ledgers) == 1
