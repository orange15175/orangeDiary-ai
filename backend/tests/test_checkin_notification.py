"""登录足迹与站内消息的服务层测试（SQLite）。

通知的 `unavailableReason` 分支是整个模块最容易出错的部分：删帖、下架、越权、
评论移除四种情况对应四种文案，前端据此渲染灰色条目。这里逐条钉住。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import BusinessError
from app.db.base import Base
from app.models.checkin import UserDailyCheckin
from app.models.community import Notification, Post, PostComment
from app.models.user import User
from app.services.checkin_service import CheckinService, today_in_app_zone
from app.services.notification_service import NotificationService


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


async def _user(db, nickname: str | None = "橙子", status: str = "active") -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        nickname=nickname,
        status=status,
        role="user",
    )
    db.add(user)
    await db.commit()
    return user


async def _post(db, user: User, **kwargs) -> Post:
    post = Post(
        user_id=user.id,
        post_type=kwargs.pop("post_type", "text"),
        content=kwargs.pop("content", "今天吃了一碗热干面"),
        status=kwargs.pop("status", "published"),
        visibility=kwargs.pop("visibility", "public"),
        published_at=datetime(2026, 11, 18, 12, 0),
        created_at=kwargs.pop("created_at", datetime(2026, 11, 18, 12, 0)),
        **kwargs,
    )
    db.add(post)
    await db.commit()
    return post


# ---------------- 登录足迹 ----------------


@pytest.mark.asyncio
async def test_record_today_is_idempotent(db):
    service = CheckinService(db)
    user = await _user(db)

    await service.record_today(user.id)
    await service.record_today(user.id)
    await db.commit()

    count = await db.scalar(select(func.count()).select_from(UserDailyCheckin))
    assert count == 1
    row = await db.scalar(select(UserDailyCheckin))
    assert row.checkin_date == today_in_app_zone()


@pytest.mark.asyncio
async def test_footprint_counts_and_streak(db):
    service = CheckinService(db)
    user = await _user(db)
    # 11-01~11-03 连续 3 天，11-05~11-06 连续 2 天 → 最长 3
    for day in (1, 2, 3, 5, 6):
        db.add(
            UserDailyCheckin(
                user_id=user.id,
                checkin_date=date(2026, 11, day),
                checked_at=datetime(2026, 11, day, 9, 0),
            )
        )
    await db.commit()

    footprint = await service.get_footprint(user.id, date(2026, 11, 1), date(2026, 11, 4))
    assert footprint["checkedDates"] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert footprint["checkedDays"] == 3
    # streak 用全量记录算，不受查询区间影响
    assert footprint["longestStreak"] == 3


@pytest.mark.asyncio
async def test_footprint_range_validation(db):
    service = CheckinService(db)
    with pytest.raises(BusinessError) as exc:
        await service.get_footprint(1, date(2026, 11, 18), date(2026, 11, 1))
    assert exc.value.message == "开始日期不能晚于结束日期"

    with pytest.raises(BusinessError) as exc:
        await service.get_footprint(1, date(2026, 1, 1), date(2027, 1, 2))
    assert exc.value.message == "足迹查询最多支持365天"

    # 正好 365 天允许
    footprint = await service.get_footprint(1, date(2026, 1, 1), date(2026, 12, 31))
    assert footprint["checkedDays"] == 0


# ---------------- 站内消息 ----------------


@pytest.mark.asyncio
async def test_list_orders_and_paginates(db):
    service = NotificationService(db)
    owner = await _user(db)
    actor = await _user(db, nickname="小明")
    post = await _post(db, owner)

    for index in range(3):
        db.add(
            Notification(
                type="COMMENT",
                recipient_id=owner.id,
                actor_id=actor.id,
                post_id=post.id,
                created_at=datetime(2026, 11, 18, 10 + index, 0),
            )
        )
    await db.commit()

    page = await service.list(owner.id, page=1, size=2)
    assert page["total"] == 3
    assert page["pages"] == 2
    assert len(page["records"]) == 2
    # 最新的在前
    assert page["records"][0]["createdAt"].startswith("2026-11-18T12")


@pytest.mark.asyncio
async def test_list_rejects_unknown_type(db):
    service = NotificationService(db)
    with pytest.raises(BusinessError) as exc:
        await service.list(1, 1, 10, "LIKE")
    assert exc.value.code == 400
    assert exc.value.message == "消息类型不合法"


@pytest.mark.asyncio
async def test_unread_count_and_mark_read(db):
    service = NotificationService(db)
    owner = await _user(db)
    db.add_all(
        [
            Notification(type="SYSTEM", recipient_id=owner.id, title="t", content="c"),
            Notification(
                type="SYSTEM",
                recipient_id=owner.id,
                title="t2",
                content="c2",
                read_at=datetime(2026, 11, 18, 9, 0),
            ),
        ]
    )
    await db.commit()

    assert await service.unread_count(owner.id) == 1
    unread = await db.scalar(select(Notification).where(Notification.read_at.is_(None)))
    await service.mark_read(owner.id, unread.id)
    await db.commit()
    assert await service.unread_count(owner.id) == 0

    # 再次标记已读不改变时间，也不报错
    first_read_at = unread.read_at
    await service.mark_read(owner.id, unread.id)
    await db.commit()
    assert unread.read_at == first_read_at


@pytest.mark.asyncio
async def test_mark_read_other_users_notification_is_404(db):
    service = NotificationService(db)
    owner = await _user(db)
    intruder = await _user(db)
    notification = Notification(type="SYSTEM", recipient_id=owner.id, title="t", content="c")
    db.add(notification)
    await db.commit()

    with pytest.raises(BusinessError) as exc:
        await service.mark_read(intruder.id, notification.id)
    assert exc.value.code == 404
    assert exc.value.message == "通知不存在"


@pytest.mark.asyncio
async def test_comment_notification_content_and_excerpt(db):
    service = NotificationService(db)
    owner = await _user(db)
    actor = await _user(db, nickname="小明")
    post = await _post(db, owner, content="今天跑了五公里，感觉很爽")
    comment = PostComment(post_id=post.id, user_id=actor.id, content="太厉害了吧")
    db.add(comment)
    await db.commit()

    notification = Notification(
        type="COMMENT", recipient_id=owner.id, actor_id=actor.id, post_id=post.id, comment_id=comment.id
    )
    db.add(notification)
    await db.commit()

    page = await service.list(owner.id)
    row = page["records"][0]
    assert row["actorName"] == "小明"
    assert row["content"] == "太厉害了吧"
    assert row["postExcerpt"] == "今天跑了五公里，感觉很爽"
    assert row["unavailableReason"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post_status", "comment_status", "expected"),
    [
        ("deleted", "active", "帖子已删除"),
        ("hidden", "active", "帖子已被下架"),
        ("published", "removed", "评论已被管理员移除"),
        ("published", None, "评论已删除"),
    ],
)
async def test_comment_notification_unavailable_reasons(db, post_status, comment_status, expected):
    service = NotificationService(db)
    owner = await _user(db)
    actor = await _user(db)
    post = await _post(db, owner, status=post_status)
    comment_id = None
    if comment_status is not None:
        comment = PostComment(post_id=post.id, user_id=actor.id, content="x", status=comment_status)
        db.add(comment)
        await db.commit()
        comment_id = comment.id

    db.add(
        Notification(
            type="COMMENT",
            recipient_id=owner.id,
            actor_id=actor.id,
            post_id=post.id,
            comment_id=comment_id,
        )
    )
    await db.commit()

    row = (await service.list(owner.id))["records"][0]
    assert row["unavailableReason"] == expected


@pytest.mark.asyncio
async def test_comment_notification_on_foreign_post_is_denied(db):
    service = NotificationService(db)
    owner = await _user(db)
    other = await _user(db)
    actor = await _user(db)
    # 帖子不是收件人的：即使状态正常也不能给出正文
    post = await _post(db, other)
    comment = PostComment(post_id=post.id, user_id=actor.id, content="x")
    db.add(comment)
    await db.commit()
    db.add(
        Notification(
            type="COMMENT", recipient_id=owner.id, actor_id=actor.id, post_id=post.id, comment_id=comment.id
        )
    )
    await db.commit()

    row = (await service.list(owner.id))["records"][0]
    assert row["unavailableReason"] == "无权查看该帖子"
    assert row["content"] is None


@pytest.mark.asyncio
async def test_deactivated_actor_and_blank_nickname(db):
    service = NotificationService(db)
    owner = await _user(db)
    post = await _post(db, owner)
    gone = await _user(db, status="disabled")
    blank = await _user(db, nickname="   ")

    db.add_all(
        [
            Notification(type="COMMENT", recipient_id=owner.id, actor_id=gone.id, post_id=post.id),
            Notification(type="COMMENT", recipient_id=owner.id, actor_id=blank.id, post_id=post.id),
        ]
    )
    await db.commit()

    rows = (await service.list(owner.id))["records"]
    names = [row["actorName"] for row in rows]
    assert "已注销用户" in names
    assert "橘子朋友" in names


@pytest.mark.asyncio
async def test_system_notification_visibility(db):
    service = NotificationService(db)
    owner = await _user(db)
    public_post = await _post(db, owner)
    hidden_other = await _post(db, await _user(db), status="hidden")

    db.add_all(
        [
            Notification(
                type="SYSTEM", recipient_id=owner.id, title="系统通知", content="有内测",
                post_id=public_post.id,
            ),
            Notification(
                type="SYSTEM", recipient_id=owner.id, title="处理结果", content="已下架",
                post_id=hidden_other.id,
            ),
        ]
    )
    await db.commit()

    rows = (await service.list(owner.id, type="SYSTEM"))["records"]
    assert all(row["actorName"] == "系统通知" for row in rows)
    reasons = sorted(str(row["unavailableReason"]) for row in rows)
    assert "关联帖子已不可查看" in reasons
    assert "None" in reasons


@pytest.mark.asyncio
async def test_notify_comment_skips_self(db):
    service = NotificationService(db)
    owner = await _user(db)
    post = await _post(db, owner)
    comment = PostComment(post_id=post.id, user_id=owner.id, content="自己回复自己")
    db.add(comment)
    await db.commit()

    await service.notify_comment(post, comment)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.asyncio
async def test_notify_comment_creates_notification(db):
    service = NotificationService(db)
    owner = await _user(db)
    actor = await _user(db)
    post = await _post(db, owner)
    comment = PostComment(post_id=post.id, user_id=actor.id, content="赞")
    db.add(comment)
    await db.commit()

    await service.notify_comment(post, comment)
    await db.commit()
    notification = await db.scalar(select(Notification))
    assert notification.type == "COMMENT"
    assert notification.recipient_id == owner.id
    assert notification.actor_id == actor.id
    assert notification.comment_id == comment.id


@pytest.mark.asyncio
async def test_notify_system_validation(db):
    service = NotificationService(db)
    recipient = await _user(db)

    with pytest.raises(BusinessError) as exc:
        await service.notify_system(recipient.id, "  ", "内容")
    assert exc.value.code == 400
    assert exc.value.message == "系统通知参数不合法"

    with pytest.raises(BusinessError) as exc:
        await service.notify_system(recipient.id, "标题", "内容" * 1001)
    assert exc.value.code == 400

    with pytest.raises(BusinessError) as exc:
        await service.notify_system(10 ** 9, "标题", "内容")
    assert exc.value.code == 404
    assert exc.value.message == "通知收件人不存在或已停用"

    await service.notify_system(recipient.id, " 标题 ", " 内容 ")
    await db.commit()
    notification = await db.scalar(select(Notification))
    assert notification.title == "标题"
    assert notification.content == "内容"
