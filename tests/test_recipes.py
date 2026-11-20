"""M3：食谱营养计算与私人厨师。

钉住的是语义，不是实现细节：
- 能确定克重才计营养，缺换算/缺数量保持 null 并计入 missing，不拿 0 顶替；
- 缩放按份数等比，不改库；
- 私人厨师无模型时返回库中做法并标降级，过敏项被过滤。
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import replace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.models.diet_assistant import Recipe, RecipeIngredient, RecipeStep
from app.models.food import FoodItem, FoodServing
from app.services import knowledge_client
from app.services.chef_service import ChefService
from app.services.recipe_service import RecipeService


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


async def _food(db, name: str, calorie: str = "100", protein: str = "10") -> FoodItem:
    food = FoodItem(
        name=name,
        unit="g",
        calorie_per_100g=Decimal(calorie),
        protein_per_100g=Decimal(protein),
        fat_per_100g=Decimal("1"),
        carb_per_100g=Decimal("1"),
        status="active",
    )
    db.add(food)
    await db.flush()
    return food


async def _recipe(db, name: str = "番茄炒蛋", servings: int = 2) -> Recipe:
    recipe = Recipe(name=name, scope="system", source_type="sample", servings=servings, status="active")
    db.add(recipe)
    await db.flush()
    return recipe


@pytest.mark.asyncio
async def test_nutrition_uses_grams_and_serving_conversion(db):
    egg = await _food(db, "鸡蛋", "144", "13.3")
    oil = await _food(db, "豆油", "899", "0")
    db.add(FoodServing(food_item_id=egg.id, serving_name="个", gram_weight=Decimal("50")))
    recipe = await _recipe(db)
    db.add_all(
        [
            RecipeIngredient(
                recipe_id=recipe.id, food_item_id=egg.id, original_name="鸡蛋",
                quantity=Decimal("2"), unit="个", sort_order=0,
            ),
            RecipeIngredient(
                recipe_id=recipe.id, food_item_id=oil.id, original_name="食用油",
                quantity=Decimal("10"), unit="g", sort_order=1,
            ),
        ]
    )
    await db.commit()

    nutrition = await RecipeService(db).compute_nutrition(recipe.id)
    # 2 个鸡蛋 = 100g → 144 kcal；10g 油 → 89.9 kcal；合计 233.9
    assert nutrition["total"]["calorie"] == Decimal("233.90")
    assert nutrition["coverage"] == Decimal("1.0000")
    assert nutrition["missing"] == []


@pytest.mark.asyncio
async def test_unknown_quantity_is_missing_not_zero(db):
    salt = await _food(db, "精盐", "0", "0")
    recipe = await _recipe(db)
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id, food_item_id=salt.id, original_name="盐",
            quantity=None, unit=None, sort_order=0,
        )
    )
    await db.commit()

    nutrition = await RecipeService(db).compute_nutrition(recipe.id)
    # 数量未知：不计入、不补 0，coverage 为 0。
    assert nutrition["total"]["calorie"] is None
    assert nutrition["coverage"] == Decimal("0.0000")
    assert nutrition["missing"] == ["盐"]


@pytest.mark.asyncio
async def test_serving_without_conversion_stays_missing(db):
    tomato = await _food(db, "番茄（西红柿）", "15", "0.9")
    recipe = await _recipe(db)
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id, food_item_id=tomato.id, original_name="番茄",
            quantity=Decimal("2"), unit="个", sort_order=0,
        )
    )
    await db.commit()

    nutrition = await RecipeService(db).compute_nutrition(recipe.id)
    # 没有"个→克"换算，不按克硬算。
    assert nutrition["total"]["calorie"] is None
    assert nutrition["missing"] == ["番茄"]


@pytest.mark.asyncio
async def test_scale_servings_multiplies_grams_and_nutrition(db):
    egg = await _food(db, "鸡蛋", "144", "13.3")
    db.add(FoodServing(food_item_id=egg.id, serving_name="个", gram_weight=Decimal("50")))
    recipe = await _recipe(db, servings=1)
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id, food_item_id=egg.id, original_name="鸡蛋",
            quantity=Decimal("1"), unit="个", sort_order=0,
        )
    )
    db.add(RecipeStep(recipe_id=recipe.id, step_index=1, description="打散"))
    await db.commit()

    scaled = await RecipeService(db).scale_servings(recipe.id, 2)
    assert scaled["targetServings"] == 2
    assert scaled["ingredients"][0]["grams"] == Decimal("100.00")
    assert scaled["nutritionDetail"]["total"]["calorie"] == Decimal("144.00")


@pytest.mark.asyncio
async def test_chef_falls_back_without_model_and_filters_allergens(db, monkeypatch):
    egg = await _food(db, "鸡蛋", "144", "13.3")
    db.add(FoodServing(food_item_id=egg.id, serving_name="个", gram_weight=Decimal("50")))
    recipe = await _recipe(db, "番茄炒蛋")
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id, food_item_id=egg.id, original_name="鸡蛋",
            quantity=Decimal("2"), unit="个", sort_order=0,
        )
    )
    db.add(RecipeStep(recipe_id=recipe.id, step_index=1, description="打散炒熟"))
    await db.commit()

    async def empty_search(*args, **kwargs):
        return knowledge_client.RagResult.empty()

    monkeypatch.setattr(knowledge_client, "search", empty_search)
    settings = replace(Settings.from_env(), deepseek_api_key="")

    result = await ChefService(db, settings, user_id=1).respond("番茄炒蛋怎么做")
    assert "番茄炒蛋" in result["reply"]
    assert "打散炒熟" in result["reply"]
    assert result["degraded"] == "model_not_configured"

    blocked = await ChefService(db, settings, user_id=1).respond(
        "番茄炒蛋怎么做", allergens=["鸡蛋"]
    )
    assert blocked["recipes"] == []
    assert blocked["degraded"] == "no_candidates"
