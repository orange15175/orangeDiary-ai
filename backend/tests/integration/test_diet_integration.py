"""饮食链路在真实 MySQL 8 上的验收。

SQLite 跑不出来的三件事，这里才有意义：
1. `SELECT ... FOR UPDATE` 真的加锁（SQLite 方言会直接忽略它）；
2. 唯一键在并发下真的拦住重复写入（幂等占位）；
3. `ON UPDATE CURRENT_TIMESTAMP`、外键约束、decimal 精度按 MySQL 实际行为走。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import BusinessError
from app.models.diet import DietRecord
from app.models.food import FoodItem
from app.models.health import DailySummary, IdempotencyRecord
from app.models.user import User, UserGoal, UserProfile
from app.schemas.diet import CreateDietRecordRequest
from app.services import idempotency
from app.services.diet_service import DietService

# 整个文件都需要真实 MySQL：没有这个标记，`-m "not integration"` 会把它们当成
# 单测跑（然后因为没有 TEST_DATABASE_URL 而 skip），验收时就悄悄少跑一半。
pytestmark = pytest.mark.integration


async def _seed(factory) -> tuple[int, int]:
    """建一个档案完整、目标生效的用户 + 一份食物，返回 (user_id, food_id)。"""
    async with factory() as session:
        user = User(email=f"diet-{datetime.now().timestamp()}@example.com", status="active", role="user")
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(
                user_id=user.id,
                gender="female",
                height_cm=Decimal("165.00"),
                current_weight_kg=Decimal("55.00"),
                target_weight_kg=Decimal("52.00"),
                activity_level="medium",
            )
        )
        session.add(
            UserGoal(
                user_id=user.id,
                goal_type="lose",
                daily_intake_target=Decimal("1600.00"),
                start_date=date(2026, 11, 1),
                status="active",
            )
        )
        food = FoodItem(
            name="集成米饭",
            category="谷物",
            unit="g",
            calorie_per_100g=Decimal("116.00"),
            protein_per_100g=Decimal("2.60"),
            fat_per_100g=Decimal("0.30"),
            carb_per_100g=Decimal("25.90"),
            status="active",
        )
        session.add(food)
        await session.commit()
        return user.id, food.id


def _payload(food_id: int) -> CreateDietRecordRequest:
    return CreateDietRecordRequest(
        foodItemId=food_id,
        mealType="lunch",
        quantityG=Decimal("150"),
        recordTime=datetime(2026, 11, 18, 12, 30),
    )


@pytest.mark.asyncio
async def test_diet_write_updates_summary_on_mysql(mysql_session_factory):
    user_id, food_id = await _seed(mysql_session_factory)

    async with mysql_session_factory() as session:
        row = await DietService(session).create_record(user_id, _payload(food_id))
        await session.commit()
        assert row["recordDate"] == "2026-11-18"
        assert Decimal(str(row["calorieSnapshot"])) == Decimal("174.00")

    async with mysql_session_factory() as session:
        summary = await session.scalar(
            select(DailySummary).where(
                DailySummary.user_id == user_id, DailySummary.summary_date == date(2026, 11, 18)
            )
        )
        assert summary is not None
        assert Decimal(str(summary.total_intake_calorie)) == Decimal("174.00")
        assert summary.diet_count == 1

        await DietService(session).delete_record(user_id, row["id"])
        await session.commit()

    async with mysql_session_factory() as session:
        summary = await session.scalar(select(DailySummary).where(DailySummary.user_id == user_id))
        assert summary.diet_count == 0
        assert Decimal(str(summary.total_intake_calorie)) == Decimal("0")


@pytest.mark.asyncio
async def test_idempotency_under_concurrency_on_mysql(mysql_session_factory):
    """两个并发同 requestId 请求：只能落一条记录，另一个要么重放要么报处理中。"""
    user_id, food_id = await _seed(mysql_session_factory)
    payload = _payload(food_id)
    request_id = "concurrent-test-0001"

    async def attempt() -> tuple[str, int | None]:
        async with mysql_session_factory() as session:
            try:
                result = await idempotency.execute(
                    session,
                    user_id=user_id,
                    operation="DIET_CREATE",
                    request_id=request_id,
                    payload=payload.model_dump(mode="json"),
                    action=lambda: DietService(session).create_record(user_id, payload),
                )
                return "ok", result["id"]
            except BusinessError as exc:
                return f"error:{exc.code}", None

    outcomes = await asyncio.gather(attempt(), attempt())
    statuses = [outcome[0] for outcome in outcomes]
    # 恰好一个真正落库；另一个是 ok（恰好赶上重放）或 409（占位被别人持有）。
    assert "ok" in statuses, statuses
    assert all(status in {"ok", "error:409"} for status in statuses), statuses

    async with mysql_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(DietRecord))
        assert count == 1
        succeeded = await session.scalar(
            select(func.count()).select_from(IdempotencyRecord).where(
                IdempotencyRecord.request_id == request_id,
                IdempotencyRecord.status == idempotency.SUCCEEDED,
            )
        )
        assert succeeded == 1


@pytest.mark.asyncio
async def test_checkin_insert_ignore_on_mysql(mysql_session_factory):
    """`INSERT IGNORE` 是 MySQL 方言，SQLite 单测走的是 ON CONFLICT，必须真库验一次。"""
    from app.models.checkin import UserDailyCheckin
    from app.services.checkin_service import CheckinService

    user_id, _ = await _seed(mysql_session_factory)

    async with mysql_session_factory() as session:
        service = CheckinService(session)
        await service.record_today(user_id)
        await session.commit()

    async with mysql_session_factory() as session:
        # 第二次插入同一天：不能抛异常，也不能多出一行
        await CheckinService(session).record_today(user_id)
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(UserDailyCheckin))
        assert count == 1
