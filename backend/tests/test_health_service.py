"""运动 / 体重 / 每日汇总的服务层测试（SQLite）。

三块共享同一条"明细 → 汇总"的因果链，所以放在一个文件里：
记运动 → 汇总的 burn 变；记体重 → 汇总的 weight 变、档案的当前体重跟着变；
减重目标 → 汇总的 targetDailyCalorie 用目标值而不是 BMR 估算。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import BusinessError
from app.db.base import Base
from app.models.health import DailySummary, ExerciseItem, ExerciseRecord, WeightRecord
from app.models.user import User, UserGoal, UserProfile
from app.schemas.exercise import CreateExerciseRecordRequest
from app.schemas.weight import CreateWeightRecordRequest
from app.services.exercise_service import ExerciseService
from app.services.profile_gate import MESSAGE as PROFILE_MESSAGE
from app.services.summary_service import SummaryService
from app.services.weight_service import WeightService
from uuid import uuid4


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


async def _user(db, complete: bool = True) -> User:
    # 邮件地址必须唯一：同一个用例里会建多个用户（例如"档案完整"与"档案缺失"对照）。
    user = User(email=f"health-{uuid4().hex[:10]}@example.com", status="active", role="user")
    db.add(user)
    await db.commit()
    if complete:
        db.add(
            UserProfile(
                user_id=user.id,
                gender="female",
                birthday=date(1996, 3, 1),
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


async def _item(db, name: str = "跑步", **kwargs) -> ExerciseItem:
    item = ExerciseItem(
        name=name,
        category=kwargs.pop("category", "有氧"),
        status=kwargs.pop("status", "active"),
        **kwargs,
    )
    db.add(item)
    await db.commit()
    return item


def _exercise_payload(item_id: int, **overrides) -> CreateExerciseRecordRequest:
    data = {
        "exerciseItemId": item_id,
        "durationMin": 30,
        "recordTime": datetime(2026, 11, 18, 7, 30),
    }
    data.update(overrides)
    return CreateExerciseRecordRequest(**data)


# ---------------- 运动库 ----------------


@pytest.mark.asyncio
async def test_list_items_only_active_ordered(db):
    service = ExerciseService(db)
    await _item(db, "跑步")
    await _item(db, "停用项目", status="inactive")
    await _item(db, "游泳")

    rows = await service.list_items()
    assert [row["name"] for row in rows] == ["跑步", "游泳"]


@pytest.mark.asyncio
async def test_search_items_limit_and_blank(db):
    service = ExerciseService(db)
    for index in range(12):
        await _item(db, f"深蹲{index}")

    assert await service.search_items("  ", 10) == []
    assert len(await service.search_items("深蹲", 99)) == 10
    assert len(await service.search_items("深蹲", 0)) == 1


@pytest.mark.asyncio
async def test_browse_standard_categories(db):
    service = ExerciseService(db)
    await _item(db, "跑步", category="有氧")
    await _item(db, "深蹲", category="力量")
    await _item(db, "拉伸", category="柔韧")
    await _item(db, "未分类", category=None)

    aerobic = await service.browse(1, category="aerobic")
    assert [item["name"] for item in aerobic["items"]] == ["跑步"]

    other = await service.browse(1, category="other")
    assert [item["name"] for item in other["items"]] == ["未分类"]


@pytest.mark.asyncio
async def test_browse_custom_and_favorite_empty(db):
    service = ExerciseService(db)
    await _item(db, "跑步")
    for code in ("custom", "favorite"):
        page = await service.browse(1, category=code)
        assert page["items"] == []
        assert page["total"] == 0


@pytest.mark.asyncio
async def test_browse_frequent_by_usage(db):
    service = ExerciseService(db)
    user = await _user(db)
    run = await _item(db, "跑步", category="有氧", calorie_per_unit=Decimal("10.00"))
    swim = await _item(db, "游泳", category="有氧", calorie_per_unit=Decimal("12.00"))

    base = datetime(2026, 11, 18, 7, 0)
    db.add_all(
        [
            ExerciseRecord(
                user_id=user.id,
                exercise_item_id=swim.id,
                exercise_name_snapshot="游泳",
                duration_min=30,
                calorie_burned=Decimal("360"),
                record_time=base - timedelta(days=2),
                record_date=(base - timedelta(days=2)).date(),
            ),
            ExerciseRecord(
                user_id=user.id,
                exercise_item_id=swim.id,
                exercise_name_snapshot="游泳",
                duration_min=30,
                calorie_burned=Decimal("360"),
                record_time=base - timedelta(days=1),
                record_date=(base - timedelta(days=1)).date(),
            ),
            ExerciseRecord(
                user_id=user.id,
                exercise_item_id=run.id,
                exercise_name_snapshot="跑步",
                duration_min=30,
                calorie_burned=Decimal("300"),
                record_time=base,
                record_date=base.date(),
            ),
        ]
    )
    await db.commit()

    page = await service.browse(user.id, category="frequent")
    assert [item["name"] for item in page["items"]] == ["游泳", "跑步"]
    assert page["total"] == 2


@pytest.mark.asyncio
async def test_frequent_total_excludes_inactive(db):
    """Java 的 count 不过滤 active，导致 total 虚高；这里按 active 统计。"""
    service = ExerciseService(db)
    user = await _user(db)
    active = await _item(db, "跑步", category="有氧")
    retired = await _item(db, "已停用", category="有氧", status="inactive")

    for item in (active, retired):
        db.add(
            ExerciseRecord(
                user_id=user.id,
                exercise_item_id=item.id,
                exercise_name_snapshot=item.name,
                duration_min=10,
                calorie_burned=Decimal("100"),
                record_time=datetime(2026, 11, 18, 7, 0),
                record_date=date(2026, 11, 18),
            )
        )
    await db.commit()

    page = await service.browse(user.id, category="frequent")
    assert page["total"] == 1
    assert [item["name"] for item in page["items"]] == ["跑步"]


# ---------------- 运动记录 ----------------


@pytest.mark.asyncio
async def test_create_uses_calorie_per_unit(db):
    service = ExerciseService(db)
    user = await _user(db)
    item = await _item(db, "跑步", calorie_per_unit=Decimal("10.00"))

    row = await service.create_record(user.id, _exercise_payload(item.id, durationMin=45))
    assert Decimal(str(row["calorieBurned"])) == Decimal("450.00")
    assert row["recordDate"] == "2026-11-18"


@pytest.mark.asyncio
async def test_create_uses_met_and_weight(db):
    service = ExerciseService(db)
    user = await _user(db)
    item = await _item(db, "跳绳", met_value=Decimal("10.00"))

    # MET × 体重 × 分钟 / 60 = 10 × 55 × 30 / 60 = 275
    row = await service.create_record(user.id, _exercise_payload(item.id, durationMin=30))
    assert Decimal(str(row["calorieBurned"])) == Decimal("275.00")


@pytest.mark.asyncio
async def test_missing_weight_is_blocked_by_profile_gate(db):
    """档案缺体重时先被 428 拦住，走不到 MET 算法里的 400。

    Java 侧同样如此：`requireCompletedProfile` 要求 gender/height/currentWeight
    齐全，所以 `calculateBurnedCalorie` 里那句 "Please set your current weight"
    实际不可达。这里保留断言是为了记录这个事实——将来有人放宽档案闸门时，
    这条用例会立刻失败，提醒他那条 400 文案开始生效。
    """
    service = ExerciseService(db)
    user = await _user(db, complete=False)
    item = await _item(db, "跳绳", met_value=Decimal("10.00"))

    with pytest.raises(BusinessError) as exc:
        await service.create_record(user.id, _exercise_payload(item.id))
    assert exc.value.code == 428
    assert exc.value.message == PROFILE_MESSAGE

    # 已完整但体重被清空：仍然先撞 428，而不是 400。
    user2 = await _user(db)
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user2.id))
    profile.current_weight_kg = None
    await db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.preview_record(user2.id, _exercise_payload(item.id))
    assert exc.value.code == 428


@pytest.mark.asyncio
async def test_create_rejects_missing_rule(db):
    service = ExerciseService(db)
    user = await _user(db)
    item = await _item(db, "神秘运动")

    with pytest.raises(BusinessError) as exc:
        await service.create_record(user.id, _exercise_payload(item.id))
    assert exc.value.code == 400
    assert exc.value.message == "Exercise calorie rule is not configured"


@pytest.mark.asyncio
async def test_preview_matches_create(db):
    service = ExerciseService(db)
    user = await _user(db)
    item = await _item(db, "跑步", calorie_per_unit=Decimal("10.00"))
    payload = _exercise_payload(item.id, durationMin=45)

    preview = await service.preview_record(user.id, payload)
    created = await service.create_record(user.id, payload)
    assert preview["estimatedCalorieBurned"] == created["calorieBurned"]
    assert preview["exerciseName"] == created["exerciseNameSnapshot"]


@pytest.mark.asyncio
async def test_exercise_updates_summary_and_delete_reverts(db):
    service = ExerciseService(db)
    user = await _user(db)
    item = await _item(db, "跑步", calorie_per_unit=Decimal("10.00"))

    row = await service.create_record(user.id, _exercise_payload(item.id, durationMin=45))
    await db.commit()

    summary = await db.scalar(select(DailySummary).where(DailySummary.user_id == user.id))
    assert Decimal(str(summary.total_burn_calorie)) == Decimal("450.00")
    assert Decimal(str(summary.net_calorie)) == Decimal("-450.00")
    assert summary.exercise_count == 1

    await service.delete_record(user.id, row["id"])
    await db.commit()
    summary = await db.scalar(select(DailySummary).where(DailySummary.user_id == user.id))
    assert summary.exercise_count == 0
    assert Decimal(str(summary.total_burn_calorie)) == Decimal("0")


# ---------------- 体重 ----------------


@pytest.mark.asyncio
async def test_weight_create_computes_bmi_and_syncs_profile(db):
    service = WeightService(db)
    user = await _user(db)

    row = await service.create_record(
        user.id,
        CreateWeightRecordRequest(
            weightKg=Decimal("54.00"),
            bodyFatRate=Decimal("23.50"),
            recordTime=datetime(2026, 11, 18, 7, 0),
        ),
    )
    await db.commit()

    # BMI = 54 / 1.65² = 19.83
    assert Decimal(str(row["bmi"])) == Decimal("19.83")
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    assert Decimal(str(profile.current_weight_kg)) == Decimal("54.00")

    summary = await db.scalar(select(DailySummary).where(DailySummary.user_id == user.id))
    assert Decimal(str(summary.weight_kg)) == Decimal("54.00")


@pytest.mark.asyncio
async def test_weight_delete_restores_previous_weight(db):
    service = WeightService(db)
    user = await _user(db)
    first = await service.create_record(
        user.id,
        CreateWeightRecordRequest(weightKg=Decimal("54.00"), recordTime=datetime(2026, 11, 17, 7, 0)),
    )
    second = await service.create_record(
        user.id,
        CreateWeightRecordRequest(weightKg=Decimal("53.50"), recordTime=datetime(2026, 11, 18, 7, 0)),
    )
    await db.commit()

    await service.delete_record(user.id, second["id"])
    await db.commit()
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    assert Decimal(str(profile.current_weight_kg)) == Decimal("54.00")
    assert first["id"] != second["id"]

    await service.delete_record(user.id, first["id"])
    await db.commit()
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    assert profile.current_weight_kg is None


@pytest.mark.asyncio
async def test_weight_trend_keeps_last_per_day(db):
    service = WeightService(db)
    user = await _user(db)
    db.add_all(
        [
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("55.00"),
                record_date=date(2026, 11, 17),
                record_time=datetime(2026, 11, 17, 7, 0),
            ),
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("54.80"),
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

    trend = await service.list_trend(user.id, date(2026, 11, 17), date(2026, 11, 18))
    assert [point["recordDate"] for point in trend] == ["2026-11-17", "2026-11-18"]
    assert Decimal(str(trend[-1]["weightKg"])) == Decimal("54.60")


@pytest.mark.asyncio
async def test_weight_trend_limits(db):
    service = WeightService(db)
    with pytest.raises(BusinessError) as exc:
        await service.list_trend(1, date(2026, 11, 18), date(2026, 11, 1))
    assert exc.value.message == "Start date must not be after end date"

    with pytest.raises(BusinessError) as exc:
        await service.list_trend(1, date(2026, 1, 1), date(2026, 4, 1))
    assert exc.value.message == "Weight trend supports at most 90 days"


@pytest.mark.asyncio
async def test_weight_progress(db):
    service = WeightService(db)
    user = await _user(db)
    # 目标体重 52，初始 55，最新 53 → 已减 2
    db.add_all(
        [
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("55.00"),
                record_date=date(2026, 11, 1),
                record_time=datetime(2026, 11, 1, 7, 0),
            ),
            WeightRecord(
                user_id=user.id,
                weight_kg=Decimal("53.00"),
                record_date=date(2026, 11, 10),
                record_time=datetime(2026, 11, 10, 7, 0),
            ),
        ]
    )
    await db.commit()

    progress = await service.get_progress(user.id)
    assert Decimal(str(progress["initialWeightKg"])) == Decimal("55.00")
    assert Decimal(str(progress["latestWeightKg"])) == Decimal("53.00")
    assert Decimal(str(progress["targetWeightKg"])) == Decimal("52.00")
    assert Decimal(str(progress["lostWeightKg"])) == Decimal("2.00")


@pytest.mark.asyncio
async def test_weight_progress_without_records_uses_profile(db):
    service = WeightService(db)
    user = await _user(db)
    progress = await service.get_progress(user.id)
    assert Decimal(str(progress["initialWeightKg"])) == Decimal("55.00")
    assert Decimal(str(progress["latestWeightKg"])) == Decimal("55.00")
    assert Decimal(str(progress["lostWeightKg"])) == Decimal("0")


# ---------------- 每日汇总 ----------------


@pytest.mark.asyncio
async def test_summary_empty_day_uses_goal_target(db):
    service = SummaryService(db)
    user = await _user(db)

    summary = await service.get_summary(user.id, date(2026, 11, 18))
    assert summary["id"] is None
    assert Decimal(str(summary["totalIntakeCalorie"])) == Decimal("0")
    assert Decimal(str(summary["targetDailyCalorie"])) == Decimal("1600")
    assert Decimal(str(summary["availableIntakeCalorie"])) == Decimal("1600.00")
    assert summary["overBudgetCalorie"] is None or Decimal(str(summary["overBudgetCalorie"])) == Decimal("0")


@pytest.mark.asyncio
async def test_summary_over_budget(db):
    service = SummaryService(db)
    user = await _user(db)
    db.add(
        DailySummary(
            user_id=user.id,
            summary_date=date(2026, 11, 18),
            total_intake_calorie=Decimal("1800.00"),
            total_burn_calorie=Decimal("200.00"),
            net_calorie=Decimal("1600.00"),
            diet_count=3,
            exercise_count=1,
        )
    )
    await db.commit()

    summary = await service.get_summary(user.id, date(2026, 11, 18))
    assert Decimal(str(summary["availableIntakeCalorie"])) == Decimal("0.00")
    assert Decimal(str(summary["overBudgetCalorie"])) == Decimal("200.00")


@pytest.mark.asyncio
async def test_summary_falls_back_to_bmr(db):
    service = SummaryService(db)
    user = await _user(db, complete=False)
    db.add(
        UserProfile(
            user_id=user.id,
            gender="female",
            birthday=date(1996, 3, 1),
            height_cm=Decimal("165.00"),
            current_weight_kg=Decimal("55.00"),
            activity_level="medium",
        )
    )
    await db.commit()

    # 无生效目标 → BMR 估算 + 维持（无修正）
    # 10*55 + 6.25*165 - 5*30 - 161 = 550 + 1031.25 - 150 - 161 = 1270.25
    # ×1.325 = 1683.08 → 1683
    summary = await service.get_summary(user.id, date(2026, 11, 18))
    assert Decimal(str(summary["targetDailyCalorie"])) == Decimal("1683")


@pytest.mark.asyncio
async def test_summary_without_profile_target_is_zero(db):
    service = SummaryService(db)
    user = User(email="bare@example.com", status="active", role="user")
    db.add(user)
    await db.commit()

    summary = await service.get_summary(user.id, date(2026, 11, 18))
    # 没有身高体重就不猜：目标 0、可用 0、超支 null（前端据此显示"去完善资料"）
    assert Decimal(str(summary["targetDailyCalorie"])) == Decimal("0")
    assert Decimal(str(summary["availableIntakeCalorie"])) == Decimal("0.00")
    assert summary["overBudgetCalorie"] is None
