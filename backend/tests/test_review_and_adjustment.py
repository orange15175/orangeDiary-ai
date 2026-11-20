"""M6/M7：实际饮食观察、动态调整与周期复盘。

断言的是口径，不是文案：
- 没反馈的餐次算"待确认"，不进 `confirmedExecutionRate` 分母，也不当"未执行"；
- 反馈不足时不显示百分比，改显示计数；
- 没有采纳计划时两个比率都是 null，不是 0%；
- 记录被修正后旧报告标 stale，历史不被静默改写；
- 调整草稿保留已执行餐次，且不覆盖生效版本。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.diet import DietRecord
from app.models.diet_assistant import (
    DietDayStatus,
    MealPlanVersion,
    PlanMeal,
    Recipe,
    RecipeIngredient,
)
from app.models.food import FoodItem
from app.models.health import DailySummary, WeightRecord
from app.models.user import UserGoal
from app.services.adjustment_service import AdjustmentService
from app.services.plan_service import PlanService
from app.services.review_service import ReviewService


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


# 复盘默认看"上一个完整周"，测试固定用 2026-11-09 ~ 11-15（周一至周日）。
PERIOD_START = date(2026, 11, 9)
PERIOD_END = date(2026, 11, 15)


def _user(user_id: int = 1):
    return SimpleNamespace(id=user_id, role="user")


async def _setup(db) -> dict:
    db.add(
        UserGoal(
            user_id=1,
            goal_type="maintain",
            daily_intake_target=Decimal("2000"),
            start_date=date(2026, 1, 1),
            status="active",
        )
    )
    egg = FoodItem(
        name="鸡蛋",
        unit="g",
        calorie_per_100g=Decimal("144"),
        protein_per_100g=Decimal("13"),
        fat_per_100g=Decimal("9"),
        carb_per_100g=Decimal("3"),
        status="active",
    )
    chicken = FoodItem(
        name="鸡胸肉",
        unit="g",
        calorie_per_100g=Decimal("133"),
        protein_per_100g=Decimal("19.4"),
        fat_per_100g=Decimal("5"),
        carb_per_100g=Decimal("2.5"),
        status="active",
    )
    db.add_all([egg, chicken])
    await db.flush()
    recipes = []
    for name, food in (("水煮蛋", egg), ("煎鸡胸", chicken)):
        recipe = Recipe(
            name=name, scope="system", source_type="sample", servings=1, status="active",
            prep_minutes=5, cook_minutes=5,
        )
        db.add(recipe)
        await db.flush()
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id, food_item_id=food.id, original_name=food.name,
                quantity=Decimal("100"), unit="g", sort_order=0,
            )
        )
        recipes.append(recipe)
    await db.commit()
    return {"egg": egg, "chicken": chicken, "recipes": recipes}


async def _adopted_plan(db, *, days: int = 1, meal_types: list[str] | None = None) -> dict:
    """生成并采纳一个覆盖 PERIOD_START 起的计划，并把采纳时间回调到周期之前。

    现实里计划是提前采纳的；复盘的 due 判定要求"餐次日期不早于采纳日"，
    所以这里显式把 adopted_at 设到周期开始前一天。
    """
    svc = PlanService(db)
    draft = await svc.generate_draft(
        _user(),
        {
            "startDate": PERIOD_START,
            "days": days,
            "mealTypes": meal_types or ["breakfast", "lunch"],
        },
    )
    adopted = await svc.adopt(1, draft["planId"], draft["versionNumber"])
    version = await db.scalar(
        select(MealPlanVersion).where(
            MealPlanVersion.plan_id == draft["planId"],
            MealPlanVersion.version_number == draft["versionNumber"],
        )
    )
    version.adopted_at = datetime.combine(PERIOD_START - timedelta(days=1), datetime.min.time())
    await db.commit()
    return adopted


@pytest.mark.asyncio
async def test_execution_rate_excludes_unrecorded_meals(db):
    await _setup(db)
    adopted = await _adopted_plan(db, days=2, meal_types=["breakfast", "lunch"])
    meal_ids = [meal["id"] for meal in adopted["meals"]]
    assert len(meal_ids) == 4
    await PlanService(db).set_execution(1, meal_ids[0], "executed")
    await PlanService(db).set_execution(1, meal_ids[1], "skipped")

    report = await ReviewService(db).generate(
        1, period_start=PERIOD_START, period_end=PERIOD_END
    )
    execution = report["metrics"]["execution"]
    assert execution["dueAdoptedMeals"] == 4
    assert execution["executed"] == 1
    assert execution["explicitlySkipped"] == 1
    # 剩余 2 餐没反馈：算待确认，不算未执行，也不进完成率分母。
    assert execution["unrecordedMeals"] == 2
    assert execution["confirmedExecutionRate"] == Decimal("0.5000")
    assert execution["executionCoverage"] == Decimal("0.5000")
    # 反馈 2 餐 < 3 餐门槛，不显示百分比。
    assert execution["showPercent"] is False
    assert "反馈不足" in execution["primaryText"]
    assert "待确认 2 餐" in execution["secondaryText"]


@pytest.mark.asyncio
async def test_no_adopted_plan_reports_null_not_zero(db):
    await _setup(db)
    report = await ReviewService(db).generate(
        1, period_start=PERIOD_START, period_end=PERIOD_END
    )
    execution = report["metrics"]["execution"]
    assert execution["dueAdoptedMeals"] == 0
    assert execution["confirmedExecutionRate"] is None
    assert execution["executionCoverage"] is None
    assert execution["primaryText"] == "本周没有可统计的饮食计划"
    codes = {gap["code"] for gap in report["dataGaps"]}
    assert "NO_ADOPTED_PLAN" in codes


@pytest.mark.asyncio
async def test_nutrition_uses_recorded_days_and_marks_completeness(db):
    data = await _setup(db)
    # 两天有汇总、一天只有记录；未记录的天不补 0。
    db.add_all(
        [
            DailySummary(user_id=1, summary_date=date(2026, 11, 9), total_intake_calorie=Decimal("1800"), goal_intake_target=Decimal("2000")),
            DailySummary(user_id=1, summary_date=date(2026, 11, 10), total_intake_calorie=Decimal("2000"), goal_intake_target=Decimal("2000")),
        ]
    )
    db.add(
        DietRecord(
            user_id=1, food_item_id=data["egg"].id, meal_type="breakfast",
            food_name_snapshot="鸡蛋", quantity_g=Decimal("100"),
            calorie_snapshot=Decimal("144"), protein_snapshot=Decimal("13"),
            fat_snapshot=Decimal("9"), carb_snapshot=Decimal("3"),
            record_time=datetime(2026, 11, 9, 8, 0), record_date=date(2026, 11, 9),
        )
    )
    # 一条缺营养素的记录，用于验证"缺失率"而不是"补 0"。
    db.add(
        DietRecord(
            user_id=1, food_item_id=data["egg"].id, meal_type="lunch",
            food_name_snapshot="鸡蛋", quantity_g=Decimal("50"),
            calorie_snapshot=Decimal("72"), record_time=datetime(2026, 11, 9, 12, 0),
            record_date=date(2026, 11, 9),
        )
    )
    await db.commit()

    svc = ReviewService(db)
    await svc.mark_day(1, date(2026, 11, 9), "complete")
    await svc.mark_day(1, date(2026, 11, 10), "partial")

    report = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    nutrition = report["metrics"]["nutrition"]
    assert nutrition["recordedDays"] == 2
    assert nutrition["averageIntakeCalorie"] == Decimal("1900.00")
    assert nutrition["averageBasis"] == "recorded_days"
    assert nutrition["completeDays"] == 1
    assert nutrition["partialDays"] == 1
    # 2 条记录里 1 条缺宏量营养素 → 0.5，缺的记 null 而不是 0。
    assert nutrition["macroMissingCount"] == 1
    assert nutrition["macroMissingRate"] == Decimal("0.5000")
    codes = {gap["code"] for gap in report["dataGaps"]}
    assert "MACRO_MISSING" in codes


@pytest.mark.asyncio
async def test_weight_trend_requires_two_samples(db):
    await _setup(db)
    svc = ReviewService(db)
    db.add(
        WeightRecord(
            user_id=1, weight_kg=Decimal("70.0"), record_date=date(2026, 11, 10),
            record_time=datetime(2026, 11, 10, 7, 0),
        )
    )
    await db.commit()
    report = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    weight = report["metrics"]["weight"]
    assert weight["sampleCount"] == 1
    assert weight["changeKg"] is None
    assert weight["trend"] == "insufficient"

    db.add(
        WeightRecord(
            user_id=1, weight_kg=Decimal("69.2"), record_date=date(2026, 11, 13),
            record_time=datetime(2026, 11, 13, 7, 0),
        )
    )
    await db.commit()
    report = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    weight = report["metrics"]["weight"]
    assert weight["sampleCount"] == 2
    # 报告经 JSON 落库后数值是浮点，按近似比较；口径是"体重变化"不是"脂肪变化"。
    assert float(weight["changeKg"]) == pytest.approx(-0.8, abs=0.001)
    assert weight["trend"] == "down"


@pytest.mark.asyncio
async def test_review_is_idempotent_and_marks_old_report_stale(db):
    await _setup(db)
    adopted = await _adopted_plan(db, days=1, meal_types=["lunch"])
    svc = ReviewService(db)

    first = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    again = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    assert again["id"] == first["id"]

    # 补记执行状态 → 输入变化 → 新报告，旧报告标 stale 而不是被覆盖。
    await PlanService(db).set_execution(1, adopted["meals"][0]["id"], "executed")
    third = await svc.generate(1, period_start=PERIOD_START, period_end=PERIOD_END)
    assert third["id"] != first["id"]
    old = await svc.get(1, first["id"])
    assert old["status"] == "stale"
    assert third["metrics"]["execution"]["executed"] == 1


@pytest.mark.asyncio
async def test_suggestions_are_capped_and_evidence_flagged(db):
    await _setup(db)
    await _adopted_plan(db, days=2, meal_types=["breakfast", "lunch"])
    report = await ReviewService(db).generate(
        1, period_start=PERIOD_START, period_end=PERIOD_END
    )
    assert 0 < len(report["suggestions"]) <= 3
    for item in report["suggestions"]:
        assert item["evidence"]
        assert item["kind"] in {"data", "hypothesis"}


@pytest.mark.asyncio
async def test_observation_keeps_unknown_nutrition_and_resolves(db):
    await _setup(db)
    svc = AdjustmentService(db)
    created = await svc.create_observation(
        1,
        occurred_at=datetime(2026, 11, 11, 19, 30),
        description="同事聚餐，火锅",
        source="manual",
        estimated_nutrition=None,
        completeness="partial",
    )
    # 没估算就是 null，不填 0。
    assert created["estimatedNutrition"] is None
    assert created["status"] == "draft"

    from app.models.food import FoodItem
    food = await db.scalar(select(FoodItem))
    for record_id in [101, 102]:
        db.add(DietRecord(id=record_id, user_id=1, food_item_id=food.id, meal_type="dinner", food_name_snapshot=food.name, quantity_g=Decimal("100"), calorie_snapshot=Decimal("100"), record_time=datetime(2026, 11, 11, 19, 30), record_date=date(2026, 11, 11)))
    await db.commit()
    resolved = await svc.resolve(1, created["id"], [101, 102])
    assert resolved["status"] == "resolved"

    retracted = await svc.retract(1, created["id"])
    assert retracted["status"] == "retracted"

    listed = await svc.list_observations(1, None, None)
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_adjustment_keeps_executed_meals_and_does_not_activate(db):
    await _setup(db)
    adopted = await _adopted_plan(db, days=2, meal_types=["lunch"])
    plan_id = adopted["planId"]
    active_number = adopted["versionNumber"]
    executed_meal = adopted["meals"][0]
    await PlanService(db).set_execution(1, executed_meal["id"], "executed")

    revised = await AdjustmentService(db).create_adjustment(
        1, plan_id, event="食材不足", note="第二天想吃点别的"
    )
    # 草稿版本号前进，但生效版本没被替换。
    assert revised["versionNumber"] == active_number + 1
    assert revised["activeVersion"] == active_number
    assert revised["versionStatus"] == "draft"
    assert revised["checks"]["keptMeals"] == 1
    assert revised["checks"]["changedMeals"] == 1

    kept = next(m for m in revised["meals"] if m["id"] == executed_meal["id"] or m["date"] == executed_meal["date"])
    assert kept["recipeId"] == executed_meal["recipeId"]
    assert kept["executionState"] == "executed"

    # 生效版本仍只有一份，且仍是旧的。
    plan_after = await PlanService(db).get_plan(1, plan_id)
    assert plan_after["activeVersion"] == active_number
    assert plan_after["versionStatus"] == "adopted"
