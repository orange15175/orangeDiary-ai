"""举报与处置测试（SQLite）。

这里钉的是"谁能改、改完之后留没留下证据"：

- 举报重复提交必须返回同一条记录，**且不扣限流额度**（先查后扣）；
- 处置走 `WHERE status='pending'` 的条件更新，二次处理必须 409；
- 每次处置都要留 `admin_action_log` 流水 + 给作者发站内信。
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.errors import BusinessError
from app.db.base import Base
from app.models.community import AdminActionLog, CommunityReport, Notification, Post, PostComment
from app.models.user import User
from app.schemas.post import CreateCommunityReportRequest
from app.services.community_report_service import CommunityReportService
from app.services.moderation_service import ModerationService


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


class FakeLimiter:
    def __init__(self, deny: str | None = None) -> None:
        self.calls: list[str] = []
        self.deny = deny

    def _check(self, action: str) -> None:
        self.calls.append(action)
        if self.deny == action:
            raise BusinessError(429, "举报次数已达上限，请稍后重试", 3600)

    async def check_report(self, user_id: int) -> None:
        self._check("report")


async def _user(db, nickname: str | None = "橙子", *, role: str = "user", status: str = "active"):
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        nickname=nickname,
        status=status,
        role=role,
    )
    db.add(user)
    await db.commit()
    return user


async def _post(db, author: User, **kwargs) -> Post:
    post = Post(
        user_id=author.id,
        post_type=kwargs.pop("post_type", "daily_share"),
        content=kwargs.pop("content", "今天吃了一碗热干面"),
        status=kwargs.pop("status", "published"),
        visibility=kwargs.pop("visibility", "public"),
        published_at=kwargs.pop("published_at", datetime(2026, 11, 18, 12, 0)),
        created_at=datetime(2026, 11, 18, 12, 0),
        **kwargs,
    )
    db.add(post)
    await db.commit()
    return post


async def _comment(db, post: Post, author: User, content: str = "看着真香") -> PostComment:
    comment = PostComment(
        post_id=post.id,
        user_id=author.id,
        content=content,
        status="active",
        created_at=datetime(2026, 11, 18, 12, 30),
    )
    db.add(comment)
    await db.commit()
    return comment


def report_body(**kwargs) -> CreateCommunityReportRequest:
    base = {"reason": "SPAM", "details": "  "}
    base.update(kwargs)
    return CreateCommunityReportRequest(**base)


# ==================== 举报 ====================


@pytest.mark.asyncio
async def test_cannot_report_own_post(db):
    author = await _user(db)
    post = await _post(db, author)
    service = CommunityReportService(db, limiter=FakeLimiter())
    with pytest.raises(BusinessError) as info:
        await service.report_post(author.id, post.id, report_body())
    assert info.value.message == "不能举报自己的帖子"


@pytest.mark.asyncio
async def test_report_requires_public_published_post(db):
    author = await _user(db)
    visitor = await _user(db)
    service = CommunityReportService(db, limiter=FakeLimiter())

    private = await _post(db, author, visibility="private")
    hidden = await _post(db, author, status="hidden")
    for post in (private, hidden):
        with pytest.raises(BusinessError) as info:
            await service.report_post(visitor.id, post.id, report_body())
        assert info.value.code == 404
        assert info.value.message == "帖子不存在或已不可举报"


@pytest.mark.asyncio
async def test_unknown_reason_is_rejected(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = CommunityReportService(db, limiter=FakeLimiter())
    with pytest.raises(BusinessError) as info:
        await service.report_post(visitor.id, post.id, report_body(reason="LOOKS_BAD"))
    assert info.value.message == "举报原因不合法"


@pytest.mark.asyncio
async def test_blank_details_become_null(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = CommunityReportService(db, limiter=FakeLimiter())

    row = await service.report_post(visitor.id, post.id, report_body(details="   "))
    await db.commit()

    assert row["details"] is None
    assert row["status"] == "pending"
    assert row["targetSnapshot"] == post.content
    assert row["reporterName"] == "橙子"
    assert row["targetAuthorName"] == "橙子"


@pytest.mark.asyncio
async def test_duplicate_report_replays_without_consuming_quota(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    limiter = FakeLimiter()
    service = CommunityReportService(db, limiter=limiter)

    first = await service.report_post(visitor.id, post.id, report_body())
    await db.commit()
    second = await service.report_post(visitor.id, post.id, report_body(details="再补一句"))
    await db.commit()

    assert first["id"] == second["id"]
    # 先查后扣：第二次不该再消耗一次举报额度。
    assert limiter.calls == ["report"]
    assert await db.scalar(select(func.count()).select_from(CommunityReport)) == 1


@pytest.mark.asyncio
async def test_report_is_rate_limited(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = CommunityReportService(db, limiter=FakeLimiter(deny="report"))
    with pytest.raises(BusinessError) as info:
        await service.report_post(visitor.id, post.id, report_body())
    assert info.value.code == 429
    assert info.value.retry_after_seconds == 3600


@pytest.mark.asyncio
async def test_report_comment_uses_comment_snapshot(db):
    author = await _user(db)
    commenter = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    comment = await _comment(db, post, commenter, "这是一条广告")
    service = CommunityReportService(db, limiter=FakeLimiter())

    row = await service.report_comment(visitor.id, post.id, comment.id, report_body())
    await db.commit()

    assert row["targetType"] == "COMMENT"
    assert row["targetId"] == comment.id
    assert row["postId"] == post.id
    assert row["targetSnapshot"] == "这是一条广告"
    assert row["targetAuthorId"] == commenter.id


@pytest.mark.asyncio
async def test_cannot_report_own_comment(db):
    author = await _user(db)
    post = await _post(db, author)
    comment = await _comment(db, post, author)
    service = CommunityReportService(db, limiter=FakeLimiter())
    with pytest.raises(BusinessError) as info:
        await service.report_comment(author.id, post.id, comment.id, report_body())
    assert info.value.message == "不能举报自己的评论"


@pytest.mark.asyncio
async def test_removed_comment_cannot_be_reported(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    comment = await _comment(db, post, author)
    comment.status = "removed"
    await db.commit()

    service = CommunityReportService(db, limiter=FakeLimiter())
    with pytest.raises(BusinessError) as info:
        await service.report_comment(visitor.id, post.id, comment.id, report_body())
    assert info.value.code == 404
    assert info.value.message == "评论不存在或已不可举报"


# ==================== 处置 ====================


@pytest.mark.asyncio
async def test_change_post_rejects_normal_user(db):
    author = await _user(db)
    post = await _post(db, author)
    with pytest.raises(BusinessError) as info:
        await ModerationService(db).change_post(author.id, post.id, restore=False, reason="广告")
    assert info.value.code == 403
    assert info.value.message == "仅管理员可操作"


@pytest.mark.asyncio
async def test_change_post_requires_reason(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author)
    with pytest.raises(BusinessError) as info:
        await ModerationService(db).change_post(admin.id, post.id, restore=False, reason="  ")
    assert info.value.message == "请填写1至500字的处理原因"


@pytest.mark.asyncio
async def test_hide_post_writes_log_and_notifies_author(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author)

    await ModerationService(db).change_post(admin.id, post.id, restore=False, reason="含广告")
    await db.commit()

    stored = await db.get(Post, post.id)
    assert stored.status == "hidden"
    assert stored.moderation_reason == "含广告"
    assert stored.moderated_by == admin.id
    assert stored.moderated_at is not None

    log = (await db.scalars(select(AdminActionLog))).one()
    assert log.action_type == "POST_HIDE"
    assert log.target_type == "POST"
    assert log.before_status == "published"
    assert log.after_status == "hidden"
    assert log.snapshot == post.content
    assert log.admin_name == "管理员"

    notification = (await db.scalars(select(Notification))).one()
    assert notification.recipient_id == author.id
    assert notification.title == "帖子已下架"
    assert "含广告" in notification.content


@pytest.mark.asyncio
async def test_hide_twice_conflicts(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author)
    service = ModerationService(db)

    await service.change_post(admin.id, post.id, restore=False, reason="含广告")
    await db.commit()
    with pytest.raises(BusinessError) as info:
        await service.change_post(admin.id, post.id, restore=False, reason="再下架一次")
    assert info.value.code == 409
    assert info.value.message == "帖子状态已变化，请刷新后重试"


@pytest.mark.asyncio
async def test_restore_after_hide(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author)
    service = ModerationService(db)

    await service.change_post(admin.id, post.id, restore=False, reason="含广告")
    await db.commit()
    await service.change_post(admin.id, post.id, restore=True, reason="申诉通过")
    await db.commit()

    stored = await db.get(Post, post.id)
    assert stored.status == "published"
    assert stored.moderation_reason == "申诉通过"
    assert await db.scalar(select(func.count()).select_from(AdminActionLog)) == 2


@pytest.mark.asyncio
async def test_remove_comment_marks_removed(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    commenter = await _user(db)
    post = await _post(db, author)
    comment = await _comment(db, post, commenter)

    await ModerationService(db).remove_comment(
        admin.id, post.id, comment.id, reason="人身攻击"
    )
    await db.commit()

    stored = await db.get(PostComment, comment.id)
    assert stored.status == "removed"
    assert stored.moderation_reason == "人身攻击"

    log = (await db.scalars(select(AdminActionLog))).one()
    assert log.action_type == "COMMENT_REMOVE"
    assert log.target_type == "COMMENT"
    notification = (await db.scalars(select(Notification))).one()
    assert notification.recipient_id == commenter.id
    assert notification.title == "评论已被移除"


@pytest.mark.asyncio
async def test_remove_comment_twice_conflicts(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author)
    comment = await _comment(db, post, author)
    service = ModerationService(db)

    await service.remove_comment(admin.id, post.id, comment.id, reason="人身攻击")
    await db.commit()
    with pytest.raises(BusinessError) as info:
        await service.remove_comment(admin.id, post.id, comment.id, reason="再删一次")
    assert info.value.code == 409
    assert info.value.message == "评论已被处理，请刷新后重试"


@pytest.mark.asyncio
async def test_moderation_rejects_private_post(db):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    post = await _post(db, author, visibility="private")
    with pytest.raises(BusinessError) as info:
        await ModerationService(db).change_post(admin.id, post.id, restore=False, reason="含广告")
    assert info.value.code == 404
    assert info.value.message == "帖子不存在或已不可处理"


# ==================== 举报处置 ====================


async def _pending_report(db, *, target_type: str = "POST"):
    admin = await _user(db, "管理员", role="admin")
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = CommunityReportService(db, limiter=FakeLimiter())
    if target_type == "POST":
        row = await service.report_post(visitor.id, post.id, report_body())
    else:
        comment = await _comment(db, post, author)
        row = await service.report_comment(visitor.id, post.id, comment.id, report_body())
    await db.commit()
    return admin, service, post, row


@pytest.mark.asyncio
async def test_resolve_requires_admin(db):
    _admin, service, _post_row, report = await _pending_report(db)
    visitor = await _user(db)
    with pytest.raises(BusinessError) as info:
        await service.resolve(visitor.id, report["id"], resolution="DISMISS", reason="不成立")
    assert info.value.code == 403
    assert info.value.message == "仅管理员可处理社区举报"


@pytest.mark.asyncio
async def test_resolve_validates_input(db):
    admin, service, _post_row, report = await _pending_report(db)
    with pytest.raises(BusinessError) as info:
        await service.resolve(admin.id, report["id"], resolution="DELETE_ALL", reason="随便")
    assert info.value.message == "处理结果不合法"
    with pytest.raises(BusinessError) as info:
        await service.resolve(admin.id, report["id"], resolution="DISMISS", reason="   ")
    assert info.value.message == "处理原因须为1至500字"


@pytest.mark.asyncio
async def test_hide_post_resolution_requires_post_target(db):
    admin, service, post, _report_row = await _pending_report(db, target_type="COMMENT")
    comment_report = (await db.scalars(select(CommunityReport))).one()
    with pytest.raises(BusinessError) as info:
        await service.resolve(
            admin.id, comment_report.id, resolution="HIDE_POST", reason="下架帖子"
        )
    assert info.value.message == "该举报目标不是帖子"


@pytest.mark.asyncio
async def test_hide_post_resolution_hides_and_marks_actioned(db):
    admin, service, post, report = await _pending_report(db)
    row = await service.resolve(admin.id, report["id"], resolution="HIDE_POST", reason="含广告")
    await db.commit()

    assert row["status"] == "actioned"
    assert row["resolution"] == "HIDE_POST"
    assert row["processedBy"] == admin.id
    assert row["processedByName"] == "管理员"

    stored = await db.get(Post, post.id)
    assert stored.status == "hidden"
    assert stored.moderation_reason == "含广告"
    assert await db.scalar(select(func.count()).select_from(AdminActionLog)) == 1


@pytest.mark.asyncio
async def test_resolve_twice_conflicts(db):
    admin, service, _post_row, report = await _pending_report(db)
    await service.resolve(admin.id, report["id"], resolution="DISMISS", reason="不成立")
    await db.commit()
    with pytest.raises(BusinessError) as info:
        await service.resolve(admin.id, report["id"], resolution="DISMISS", reason="再处理一次")
    assert info.value.code == 409
    assert info.value.message == "该举报已处理"


@pytest.mark.asyncio
async def test_dismiss_keeps_post_published(db):
    admin, service, post, report = await _pending_report(db)
    row = await service.resolve(admin.id, report["id"], resolution="DISMISS", reason="不成立")
    await db.commit()

    assert row["status"] == "dismissed"
    assert (await db.get(Post, post.id)).status == "published"
    assert await db.scalar(select(func.count()).select_from(AdminActionLog)) == 0


@pytest.mark.asyncio
async def test_list_admin_filters_by_status(db):
    admin, service, _post_row, _report = await _pending_report(db)
    page = await service.list_admin(admin.id, page=1, size=20)
    assert page["total"] == 1
    assert page["records"][0]["status"] == "pending"

    empty = await service.list_admin(admin.id, page=1, size=20, status="actioned")
    assert empty["total"] == 0

    with pytest.raises(BusinessError) as info:
        await service.list_admin(admin.id, page=1, size=20, status="open")
    assert info.value.message == "举报状态不合法"
