"""用户中心服务层测试（SQLite，无 MySQL/Redis）。

重点不在 CRUD 能不能跑，而在三处语义是否与 Java 一致：
- 资料修改：user 表部分更新、user_profile 整体覆盖
- 目标设置：新建前把旧的 active 目标置 cancelled
- 头像校验：非上传入口、他人对象、过期记录都必须拒绝
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import BusinessError
from app.db.base import Base
from app.models.avatar import AvatarUpload
from app.models.user import User, UserGoal, UserProfile
from app.schemas.user import CreateUserGoalRequest, UpdateUserProfileRequest
from app.services.user_service import UserService


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


async def _make_user(db, email: str = "orange@example.com") -> User:
    user = User(email=email, nickname="橙子", status="active", role="user")
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_count_active_users_ignores_disabled(db):
    service = UserService(db)
    active = await _make_user(db)
    disabled = await _make_user(db, "off@example.com")
    disabled.status = "disabled"
    await db.commit()

    assert await service.count_active_users() == 1
    assert active.status == "active"


@pytest.mark.asyncio
async def test_update_profile_completes_when_goal_exists(db):
    service = UserService(db)
    user = await _make_user(db)

    payload = await service.update_profile(
        user,
        UpdateUserProfileRequest(
            nickname="新昵称",
            gender="female",
            birthday=date(1998, 5, 20),
            heightCm=Decimal("165.00"),
            currentWeightKg=Decimal("55.50"),
            targetWeightKg=Decimal("52.00"),
            activityLevel="medium",
        ),
    )
    assert payload["nickname"] == "新昵称"
    assert payload["profile"]["gender"] == "female"
    assert payload["profileCompleted"] is False  # 还差生效目标

    await service.create_goal(
        user,
        CreateUserGoalRequest(goalType="lose", dailyIntakeTarget=Decimal("1500"), startDate=date(2026, 11, 18)),
    )
    refreshed = await service.current_user_payload(user)
    assert refreshed["profileCompleted"] is True
    assert refreshed["activeGoal"]["goalType"] == "lose"


@pytest.mark.asyncio
async def test_update_profile_without_nickname_keeps_it(db):
    service = UserService(db)
    user = await _make_user(db)

    payload = await service.update_profile(user, UpdateUserProfileRequest(gender="male"))
    # Java 的 user 表是部分更新：只改性别不能把昵称冲成 null。
    assert payload["nickname"] == "橙子"
    assert payload["profile"]["gender"] == "male"
    assert payload["profile"]["heightCm"] is None


@pytest.mark.asyncio
async def test_update_profile_overwrites_profile_fields(db):
    service = UserService(db)
    user = await _make_user(db)
    await service.update_profile(
        user,
        UpdateUserProfileRequest(gender="male", heightCm=Decimal("178.00"), activityLevel="high"),
    )

    # 第二次只传性别：Java 侧身高会被清空，这里必须一样。
    payload = await service.update_profile(user, UpdateUserProfileRequest(gender="female"))
    assert payload["profile"]["gender"] == "female"
    assert payload["profile"]["heightCm"] is None
    assert payload["profile"]["activityLevel"] is None


@pytest.mark.asyncio
async def test_create_goal_cancels_previous_active_goal(db):
    service = UserService(db)
    user = await _make_user(db)

    await service.create_goal(
        user, CreateUserGoalRequest(goalType="lose", startDate=date(2026, 1, 1))
    )
    payload = await service.create_goal(
        user, CreateUserGoalRequest(goalType="maintain", startDate=date(2026, 11, 18))
    )

    assert payload["activeGoal"]["goalType"] == "maintain"
    goals = (
        await db.execute(
            UserGoal.__table__.select().where(UserGoal.user_id == user.id).order_by(UserGoal.id)
        )
    ).all()
    assert [row.status for row in goals] == ["cancelled", "active"]


@pytest.mark.asyncio
async def test_create_goal_rejects_end_before_start(db):
    service = UserService(db)
    user = await _make_user(db)

    with pytest.raises(BusinessError) as exc:
        await service.create_goal(
            user,
            CreateUserGoalRequest(
                goalType="lose", startDate=date(2026, 11, 18), endDate=date(2026, 11, 1)
            ),
        )
    assert exc.value.code == 400
    assert exc.value.message == "目标结束日期不能早于开始日期"


@pytest.mark.asyncio
async def test_avatar_must_come_from_upload_entry(db):
    service = UserService(db)
    user = await _make_user(db)

    with pytest.raises(BusinessError) as exc:
        await service.update_profile(user, UpdateUserProfileRequest(avatar="https://evil.example/x.png"))
    assert exc.value.code == 400
    assert exc.value.message == "请通过头像上传入口选择图片"


@pytest.mark.asyncio
async def test_avatar_rejects_unknown_and_foreign_keys(db):
    service = UserService(db)
    user = await _make_user(db)
    other = await _make_user(db, "other@example.com")

    with pytest.raises(BusinessError) as exc:
        await service.update_profile(user, UpdateUserProfileRequest(avatar="/api/avatars/avatars/1/missing.png"))
    assert exc.value.message == "头像图片无效"

    db.add(AvatarUpload(object_key=f"avatars/{other.id}/a.png", user_id=other.id))
    await db.commit()
    with pytest.raises(BusinessError) as exc:
        await service.update_profile(
            user, UpdateUserProfileRequest(avatar=f"/api/avatars/avatars/{other.id}/a.png")
        )
    assert exc.value.message == "头像图片无效"


@pytest.mark.asyncio
async def test_avatar_accepts_fresh_own_upload(db):
    service = UserService(db)
    user = await _make_user(db)
    key = f"avatars/{user.id}/ok.png"
    db.add(AvatarUpload(object_key=key, user_id=user.id))
    await db.commit()

    payload = await service.update_profile(user, UpdateUserProfileRequest(avatar=f"/api/avatars/{key}"))
    assert payload["avatar"] == f"/api/avatars/{key}"


@pytest.mark.asyncio
async def test_avatar_rejects_expired_upload(db):
    service = UserService(db)
    user = await _make_user(db)
    key = f"avatars/{user.id}/old.png"
    db.add(
        AvatarUpload(
            object_key=key,
            user_id=user.id,
            created_at=datetime.now() - timedelta(days=2),
        )
    )
    await db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.update_profile(user, UpdateUserProfileRequest(avatar=f"/api/avatars/{key}"))
    assert exc.value.message == "头像图片无效"


@pytest.mark.asyncio
async def test_avatar_unchanged_skips_validation(db):
    service = UserService(db)
    user = await _make_user(db)
    # 模拟历史数据：头像指向一个已经不存在的对象，但用户没改它，应当放行。
    user.avatar = "/api/avatars/avatars/1/gone.png"
    await db.commit()

    payload = await service.update_profile(user, UpdateUserProfileRequest(avatar=user.avatar, gender="male"))
    assert payload["avatar"] == "/api/avatars/avatars/1/gone.png"
    assert payload["profile"]["gender"] == "male"
