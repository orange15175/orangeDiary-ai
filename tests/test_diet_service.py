"""饮食记录与幂等服务测试（SQLite）。

重点验证"快照 + 汇总 + 幂等"三件事，这三处错一处就会出现用户能看见的脏数据：
快照错 → 热量不准；汇总错 → 首页数字和明细对不上；幂等错 → 重试多记一份。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import BusinessError
from app.db.base import Base
from app.models.diet import DietRecord
from app.models.food import FoodItem
from app.models.health import DailySummary, IdempotencyRecord
from app.models.user import User, UserGoal, UserProfile
from app.schemas.diet import CreateDietRecordRequest
from app.services import idempotency
from app.services.diet_service import DietService
from app.services.profile_gate import MESSAGE as PROFILE_MESSAGE


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


async def _user(db, complete_profile: bool = True) -> User:
    user = User(email="diet@example.com", nickname="橙子", status="active", role="user")
    db.add(user)
    await db.commit()
    if complete_profile:
        db.add(
            UserProfile(
                user_id=user.id,
                gender="female",
                height_cm=Decimal("165.00"),
                current_weight_kg=Decimal("55.00"),
                target_weight_kg=Decimal("52.00"),
                activity_level="medium",
            )
        )
        db.add(
            UserGoal(
                user_id=user.id,
                goal_type="lose",
                daily_intake_target=Decimal("1600.00"),
                start_date=date(2026, 11, 1),
                status="active",
            )
        )
        await db.commit()
    return user


async def _rice(db) -> FoodItem:
    food = FoodItem(
        name="米饭",
        category="谷物",
        unit="g",
        calorie_per_100g=Decimal("116.00"),
        protein_per_100g=Decimal("2.60"),
        fat_per_100g=Decimal("0.30"),
        carb_per_100g=Decimal("25.90"),
        status="active",
    )
    db.add(food)
    await db.commit()
    return food


def _payload(food_id: int, **overrides) -> CreateDietRecordRequest:
    data = {
        "foodItemId": food_id,
        "mealType": "lunch",
        "quantityG": Decimal("150"),
        "recordTime": datetime(2026, 11, 18, 12, 30),
    }
    data.update(overrides)
    return CreateDietRecordRequest(**data)


@pytest.mark.asyncio
async def test_create_requires_completed_profile(db):
    service = DietService(db)
    user = await _user(db, complete_profile=False)
    food = await _rice(db)

    with pytest.raises(BusinessError) as exc:
        await service.create_record(user.id, _payload(food.id))
    assert exc.value.code == 428
    assert exc.value.message == PROFILE_MESSAGE


@pytest.mark.asyncio
async def test_create_rejects_unknown_or_inactive_food(db):
    service = DietService(db)
    user = await _user(db)
    inactive = FoodItem(
        name="停用食物", unit="g", calorie_per_100g=Decimal("100"), status="inactive"
    )
    db.add(inactive)
    await db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.create_record(user.id, _payload(9999))
    assert exc.value.code == 404
    assert exc.value.message == "食物不存在或已停用"

    with pytest.raises(BusinessError) as exc:
        await service.create_record(user.id, _payload(inactive.id))
    assert exc.value.message == "食物不存在或已停用"


@pytest.mark.asyncio
async def test_create_computes_snapshots_and_date(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    row = await service.create_record(user.id, _payload(food.id, quantityG=Decimal("150")))
    assert row["foodNameSnapshot"] == "米饭"
    assert Decimal(str(row["calorieSnapshot"])) == Decimal("174.00")
    assert Decimal(str(row["proteinSnapshot"])) == Decimal("3.90")
    assert Decimal(str(row["fatSnapshot"])) == Decimal("0.45")
    assert Decimal(str(row["carbSnapshot"])) == Decimal("38.85")
    # 归属日期取进食时间，不是服务器当天。
    assert row["recordDate"] == "2026-11-18"
    assert row["recordTime"] == "2026-11-18T12:30:00"


@pytest.mark.asyncio
async def test_create_rounds_half_up(db):
    service = DietService(db)
    user = await _user(db)
    food = FoodItem(
        name="奇异食物",
        unit="g",
        calorie_per_100g=Decimal("1.11"),
        status="active",
    )
    db.add(food)
    await db.commit()

    # 1.11 * 0.3333 = 0.36996... → 0.37；HALF_EVEN 在这种值上结果相同，
    # 但 1.15 * 0.5 之类会分叉，这里锁定 Java 的 HALF_UP 口径。
    row = await service.create_record(user.id, _payload(food.id, quantityG=Decimal("33.33")))
    assert Decimal(str(row["calorieSnapshot"])) == Decimal("0.37")


@pytest.mark.asyncio
async def test_create_refreshes_daily_summary(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    await service.create_record(user.id, _payload(food.id))
    await db.commit()

    summary = await db.scalar(
        select(DailySummary).where(
            DailySummary.user_id == user.id, DailySummary.summary_date == date(2026, 11, 18)
        )
    )
    assert Decimal(str(summary.total_intake_calorie)) == Decimal("174.00")
    assert Decimal(str(summary.total_burn_calorie)) == Decimal("0")
    assert Decimal(str(summary.net_calorie)) == Decimal("174.00")
    assert summary.diet_count == 1


@pytest.mark.asyncio
async def test_delete_recomputes_summary(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    row = await service.create_record(user.id, _payload(food.id))
    await db.commit()
    await service.delete_record(user.id, row["id"])
    await db.commit()

    summary = await db.scalar(select(DailySummary).where(DailySummary.user_id == user.id))
    assert summary.diet_count == 0
    assert Decimal(str(summary.total_intake_calorie)) == Decimal("0")
    assert await db.scalar(select(func.count()).select_from(DietRecord)) == 0


@pytest.mark.asyncio
async def test_delete_other_users_record_returns_404(db):
    service = DietService(db)
    owner = await _user(db)
    intruder = User(email="intruder@example.com", status="active", role="user")
    db.add(intruder)
    await db.commit()
    food = await _rice(db)

    row = await service.create_record(owner.id, _payload(food.id))
    await db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.delete_record(intruder.id, row["id"])
    assert exc.value.code == 404
    assert exc.value.message == "Diet record not found"


@pytest.mark.asyncio
async def test_list_records_orders_and_enriches(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)
    food.cover_image_url = "/api/images/rice.png"
    await db.commit()

    await service.create_record(
        user.id, _payload(food.id, recordTime=datetime(2026, 11, 18, 8, 0))
    )
    await service.create_record(
        user.id, _payload(food.id, recordTime=datetime(2026, 11, 18, 19, 0))
    )
    # 另一天的记录不能出现在今天
    await service.create_record(
        user.id, _payload(food.id, recordTime=datetime(2026, 11, 17, 12, 0))
    )
    await db.commit()

    rows = await service.list_records(user.id, date(2026, 11, 18))
    assert [row["recordTime"] for row in rows] == [
        "2026-11-18T19:00:00",
        "2026-11-18T08:00:00",
    ]
    assert rows[0]["foodImageUrl"] == "/api/images/rice.png"
    assert rows[0]["foodCategory"] == "谷物"


@pytest.mark.asyncio
async def test_idempotent_create_returns_same_record(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)
    payload = _payload(food.id)

    first = await idempotency.execute(
        db,
        user_id=user.id,
        operation="DIET_CREATE",
        request_id="a" * 16,
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create_record(user.id, payload),
    )
    second = await idempotency.execute(
        db,
        user_id=user.id,
        operation="DIET_CREATE",
        request_id="a" * 16,
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create_record(user.id, payload),
    )

    assert second == first
    assert await db.scalar(select(func.count()).select_from(DietRecord)) == 1


@pytest.mark.asyncio
async def test_idempotency_payload_mismatch_returns_409(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    await idempotency.execute(
        db,
        user_id=user.id,
        operation="DIET_CREATE",
        request_id="b" * 16,
        payload=_payload(food.id).model_dump(mode="json"),
        action=lambda: service.create_record(user.id, _payload(food.id)),
    )

    with pytest.raises(BusinessError) as exc:
        await idempotency.execute(
            db,
            user_id=user.id,
            operation="DIET_CREATE",
            request_id="b" * 16,
            payload=_payload(food.id, quantityG=Decimal("200")).model_dump(mode="json"),
            action=lambda: service.create_record(
                user.id, _payload(food.id, quantityG=Decimal("200"))
            ),
        )
    assert exc.value.code == 409
    assert exc.value.message == "requestId与请求内容不一致"


@pytest.mark.asyncio
async def test_idempotency_rejects_bad_request_id(db):
    with pytest.raises(BusinessError) as exc:
        await idempotency.execute(
            db,
            user_id=1,
            operation="DIET_CREATE",
            request_id="short",
            payload={},
            action=lambda: None,
        )
    assert exc.value.code == 400
    assert exc.value.message == "X-Request-Id格式不合法"

    with pytest.raises(BusinessError):
        await idempotency.execute(
            db,
            user_id=1,
            operation="DIET_CREATE",
            request_id=None,
            payload={},
            action=lambda: None,
        )


@pytest.mark.asyncio
async def test_failed_action_leaves_no_idempotency_row(db):
    service = DietService(db)
    user = await _user(db)

    async def action():
        return await service.create_record(user.id, _payload(9999))

    with pytest.raises(BusinessError):
        await idempotency.execute(
            db,
            user_id=user.id,
            operation="DIET_CREATE",
            request_id="c" * 16,
            payload=_payload(9999).model_dump(mode="json"),
            action=action,
        )

    # PROCESSING 行必须随事务回滚，否则这个 requestId 会被永久占住。
    assert await db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


@pytest.mark.asyncio
async def test_idempotency_is_per_user(db):
    service = DietService(db)
    owner = await _user(db, complete_profile=False)
    other = User(email="other-diet@example.com", status="active", role="user")
    db.add(other)
    await db.commit()
    food = await _rice(db)
    payload = _payload(food.id)

    for user in (owner, other):
        db.add(
            UserProfile(
                user_id=user.id,
                gender="male",
                height_cm=Decimal("175.00"),
                current_weight_kg=Decimal("70.00"),
            )
        )
        db.add(
            UserGoal(
                user_id=user.id,
                goal_type="maintain",
                start_date=date(2026, 11, 1),
                status="active",
            )
        )
    await db.commit()

    for user in (owner, other):
        await idempotency.execute(
            db,
            user_id=user.id,
            operation="DIET_CREATE",
            request_id="d" * 16,
            payload=payload.model_dump(mode="json"),
            action=lambda u=user: service.create_record(u.id, payload),
        )

    # 同一个 requestId 在不同用户下互不影响，各自落一条。
    assert await db.scalar(select(func.count()).select_from(DietRecord)) == 2


@pytest.mark.asyncio
async def test_summary_tracks_latest_weight_of_day(db):
    from app.models.health import WeightRecord

    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    db.add_all(
        [
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("55.00"),
                record_date=date(2026, 11, 18),
                record_time=datetime(2026, 11, 18, 7, 0),
            ),
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("54.60"),
                record_date=date(2026, 11, 18),
                record_time=datetime(2026, 11, 18, 21, 0),
            ),
        ]
    )
    await db.commit()

    await service.create_record(user.id, _payload(food.id))
    await db.commit()

    summary = await db.scalar(select(DailySummary).where(DailySummary.user_id == user.id))
    assert Decimal(str(summary.weight_kg)) == Decimal("54.60")


@pytest.mark.asyncio
async def test_record_locked_to_its_own_date(db):
    service = DietService(db)
    user = await _user(db)
    food = await _rice(db)

    yesterday = datetime.now() - timedelta(days=1)
    row = await service.create_record(
        user.id, _payload(food.id, recordTime=yesterday.replace(hour=23, minute=50))
    )
    await db.commit()

    assert row["recordDate"] == yesterday.date().isoformat()
    summary = await db.scalar(
        select(DailySummary).where(DailySummary.summary_date == yesterday.date())
    )
    assert summary is not None
