"""食物库服务测试（SQLite）。

覆盖四件事：
- 分类归一的判定顺序（"果汁"归蔬果而不是饮料，顺序错了全库分类都会漂移）
- 浏览排序：常吃按本人次数 + 最近使用，其他分类按全站次数
- 分页 hasMore 的边界
- 名称匹配：EXACT / ALIAS / AMBIGUOUS / NOT_FOUND，且都只认 active
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.diet import DietRecord
from app.models.food import FoodAlias, FoodItem, FoodServing
from app.models.user import User
from app.services.food_category import normal_category_for_code, resolve
from app.services.food_service import FoodService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _food(db, name: str, category: str = "谷物", calorie: str = "116.00", **kwargs) -> FoodItem:
    food = FoodItem(
        name=name,
        category=category,
        unit=kwargs.pop("unit", "g"),
        calorie_per_100g=Decimal(calorie),
        protein_per_100g=Decimal("2.60"),
        fat_per_100g=Decimal("0.30"),
        carb_per_100g=Decimal("25.90"),
        status=kwargs.pop("status", "active"),
        **kwargs,
    )
    db.add(food)
    await db.commit()
    return food


def test_resolve_category_order_matches_java():
    # "果汁"同时含"果"与"汁"：Java 先判蔬果，必须保持一致。
    assert resolve("果汁") == "蔬果"
    assert resolve("谷物及制品") == "主食"
    assert resolve("meat") == "肉蛋奶"
    assert resolve("咖啡") == "饮料"
    assert resolve("dessert") == "零食"
    assert resolve(None) == "其他"
    assert resolve("未分类") == "其他"


def test_category_codes():
    assert normal_category_for_code("staple") == "主食"
    assert normal_category_for_code("frequent") is None
    assert normal_category_for_code("unknown") is None


@pytest.mark.asyncio
async def test_list_foods_newest_first(db):
    service = FoodService(db)
    await _food(db, "米饭")
    await _food(db, "面条")
    rows = await service.list_foods()
    assert [row["name"] for row in rows] == ["面条", "米饭"]


@pytest.mark.asyncio
async def test_search_clamps_limit_and_requires_keyword(db):
    service = FoodService(db)
    for index in range(15):
        await _food(db, f"苹果{index}")

    assert await service.search_foods("   ", 10) == []
    assert len(await service.search_foods("苹果", 100)) == 10
    assert len(await service.search_foods("苹果", 0)) == 1
    first = await service.search_foods("苹果", 3)
    assert [row["name"] for row in first] == ["苹果0", "苹果1", "苹果2"]


@pytest.mark.asyncio
async def test_browse_filters_by_category(db):
    service = FoodService(db)
    await _food(db, "米饭", "谷物")
    await _food(db, "鸡胸肉", "肉类")
    await _food(db, "西兰花", "蔬菜")

    page = await service.browse_foods(user_id=1, category="staple")
    assert [item["name"] for item in page["items"]] == ["米饭"]
    assert page["items"][0]["category"] == "主食"
    assert page["total"] == 1
    assert page["hasMore"] is False


@pytest.mark.asyncio
async def test_browse_keyword_matches_brand_case_insensitively(db):
    service = FoodService(db)
    await _food(db, "无糖酸奶", "乳制品", brand="Bright")
    await _food(db, "全麦面包", "谷物", brand="Other")

    page = await service.browse_foods(user_id=1, category="staple", keyword="BRIGHT")
    assert [item["name"] for item in page["items"]] == ["无糖酸奶"]


@pytest.mark.asyncio
async def test_browse_custom_and_favorite_are_empty_without_keyword(db):
    service = FoodService(db)
    await _food(db, "米饭")
    for code in ("custom", "favorite"):
        page = await service.browse_foods(user_id=1, category=code)
        assert page["items"] == []
        assert page["total"] == 0


@pytest.mark.asyncio
async def test_browse_frequent_orders_by_user_usage(db):
    service = FoodService(db)
    user = User(email="food@example.com", status="active", role="user")
    db.add(user)
    await db.commit()
    rice = await _food(db, "米饭", "谷物")
    noodle = await _food(db, "面条", "谷物")

    base = datetime(2026, 11, 18, 12, 0, 0)
    # 面条吃 2 次（最近一次更早），米饭 1 次但更新 → 次数优先，面条在前。
    db.add_all(
        [
            DietRecord(
                user_id=user.id,
                food_item_id=noodle.id,
                meal_type="lunch",
                food_name_snapshot="面条",
                quantity_g=Decimal("200"),
                calorie_snapshot=Decimal("300"),
                record_time=base - timedelta(days=2),
                record_date=(base - timedelta(days=2)).date(),
            ),
            DietRecord(
                user_id=user.id,
                food_item_id=noodle.id,
                meal_type="dinner",
                food_name_snapshot="面条",
                quantity_g=Decimal("200"),
                calorie_snapshot=Decimal("300"),
                record_time=base - timedelta(days=1),
                record_date=(base - timedelta(days=1)).date(),
            ),
            DietRecord(
                user_id=user.id,
                food_item_id=rice.id,
                meal_type="lunch",
                food_name_snapshot="米饭",
                quantity_g=Decimal("150"),
                calorie_snapshot=Decimal("180"),
                record_time=base,
                record_date=base.date(),
            ),
        ]
    )
    await db.commit()

    page = await service.browse_foods(user_id=user.id, category="frequent")
    assert [item["name"] for item in page["items"]] == ["面条", "米饭"]
    assert page["total"] == 2


@pytest.mark.asyncio
async def test_browse_orders_by_global_usage(db):
    service = FoodService(db)
    user = User(email="global@example.com", status="active", role="user")
    other = User(email="other@example.com", status="active", role="user")
    db.add_all([user, other])
    await db.commit()

    apple = await _food(db, "苹果", "水果")
    banana = await _food(db, "香蕉", "水果")
    for record_user in (user.id, other.id):
        db.add(
            DietRecord(
                user_id=record_user,
                food_item_id=apple.id,
                meal_type="snack",
                food_name_snapshot="苹果",
                quantity_g=Decimal("100"),
                calorie_snapshot=Decimal("52"),
                record_time=datetime(2026, 11, 18, 10, 0, 0),
                record_date=datetime(2026, 11, 18).date(),
            )
        )
    await db.commit()

    page = await service.browse_foods(user_id=user.id, category="fruit_vegetable")
    assert [item["name"] for item in page["items"]] == ["苹果", "香蕉"]
    assert banana.id > apple.id  # 仅确认排序不是按 id


@pytest.mark.asyncio
async def test_browse_pagination_has_more(db):
    service = FoodService(db)
    for index in range(5):
        await _food(db, f"主食{index}", "谷物")

    first = await service.browse_foods(user_id=1, category="staple", page=1, page_size=2)
    assert len(first["items"]) == 2
    assert first["hasMore"] is True

    last = await service.browse_foods(user_id=1, category="staple", page=3, page_size=2)
    assert len(last["items"]) == 1
    assert last["hasMore"] is False

    beyond = await service.browse_foods(user_id=1, category="staple", page=9, page_size=2)
    assert beyond["items"] == []
    assert beyond["hasMore"] is False


@pytest.mark.asyncio
async def test_browse_excludes_inactive(db):
    service = FoodService(db)
    await _food(db, "停用食物", "谷物", status="inactive")
    page = await service.browse_foods(user_id=1, category="staple")
    assert page["items"] == []


@pytest.mark.asyncio
async def test_match_exact_with_servings(db):
    service = FoodService(db)
    rice = await _food(db, "米饭", "谷物")
    db.add_all(
        [
            FoodServing(food_item_id=rice.id, serving_name="碗", gram_weight=Decimal("150.00"), sort_order=2),
            FoodServing(food_item_id=rice.id, serving_name="勺", gram_weight=Decimal("20.00"), sort_order=1),
        ]
    )
    await db.commit()

    result = await service.match_food("米饭")
    assert result["matchType"] == "EXACT"
    assert result["normalizedName"] == "米饭"
    candidate = result["candidates"][0]
    # 份量按 sort_order 排序，前端下拉框顺序依赖它。
    assert [serving["servingName"] for serving in candidate["servings"]] == ["勺", "碗"]


@pytest.mark.asyncio
async def test_match_alias_returns_canonical_name(db):
    service = FoodService(db)
    tomato = await _food(db, "番茄", "蔬菜")
    db.add(FoodAlias(food_item_id=tomato.id, alias_name="西红柿"))
    await db.commit()

    result = await service.match_food(" 西红柿 ")
    assert result["matchType"] == "ALIAS"
    assert result["normalizedName"] == "番茄"
    assert [candidate["name"] for candidate in result["candidates"]] == ["番茄"]


@pytest.mark.asyncio
async def test_match_ambiguous_and_not_found(db):
    service = FoodService(db)
    await _food(db, "苹果汁", "饮料")
    await _food(db, "苹果醋", "饮料")

    ambiguous = await service.match_food("苹果")
    assert ambiguous["matchType"] == "AMBIGUOUS"
    assert len(ambiguous["candidates"]) == 2

    missing = await service.match_food("不存在的食物")
    assert missing == {"matchType": "NOT_FOUND", "normalizedName": "不存在的食物", "candidates": []}

    assert (await service.match_food("   "))["normalizedName"] is None


@pytest.mark.asyncio
async def test_match_ignores_inactive_alias_target(db):
    service = FoodService(db)
    food = await _food(db, "停用番茄", "蔬菜", status="inactive")
    db.add(FoodAlias(food_item_id=food.id, alias_name="老番茄"))
    await db.commit()

    result = await service.match_food("老番茄")
    # 别名指向的目标已停用：不能当作 ALIAS 命中，只能走模糊匹配（这里无结果）。
    assert result["matchType"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_food_row_has_entity_fields(db):
    service = FoodService(db)
    food = await _food(db, "米饭", "谷物")
    rows = await service.list_foods()
    assert rows[0]["id"] == food.id
    # 实体接口用的是 sourceType/caloriePer100g 这套驼峰字段，不是浏览接口的裁剪版。
    assert rows[0]["sourceType"] == "system"
    assert float(rows[0]["caloriePer100g"]) == 116.0
