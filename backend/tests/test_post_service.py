"""社区帖子服务测试（SQLite）。

重点钉三组容易出错的分支：

1. **两套可见性判定**：`get` 允许作者看自己的 hidden 帖，`like/comment` 只认
   published。把这两条写反，下架帖会变成"作者还能自己点赞"。
2. **校验顺序**：限流必须排在内容校验之后。内容不合法却先扣额度，用户会
   在"改个错别字"之后莫名收到 429。
3. **作者注销/空昵称**：`authorId` 必须一起置空，否则前端还能点进已注销账号。
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
from app.models.community import Notification, Post, PostComment, PostImage, PostLike
from app.models.user import User
from app.schemas.post import CreatePostRequest
from app.services.post_service import PostService


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
    """只记录调用、不真的连 Redis；`deny` 指定要拒绝的动作。"""

    def __init__(self, deny: str | None = None) -> None:
        self.calls: list[str] = []
        self.deny = deny

    def _check(self, action: str) -> None:
        self.calls.append(action)
        if self.deny == action:
            raise BusinessError(429, "发帖次数已达上限，请稍后重试", 1800)

    async def check_post(self, user_id: int) -> None:
        self._check("post")

    async def check_comment(self, user_id: int) -> None:
        self._check("comment")

    async def check_image_upload(self, user_id: int, size: int) -> None:
        self._check("image")

    async def check_report(self, user_id: int) -> None:
        self._check("report")


class FakeStorage:
    """对象存储替身：`objects` 是 key→content_type 的字典。"""

    def __init__(self, objects: dict[str, str] | None = None) -> None:
        self.objects = dict(objects or {})
        self.deleted: list[str] = []

    async def content_type_of(self, key: str) -> str:
        if key not in self.objects:
            raise BusinessError(404, "图片不存在")
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


def build_service(db, *, limiter=None, storage=None) -> PostService:
    return PostService(
        db, limiter=limiter or FakeLimiter(), storage=storage or FakeStorage()
    )


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
        created_at=kwargs.pop("created_at", datetime(2026, 11, 18, 12, 0)),
        **kwargs,
    )
    db.add(post)
    await db.commit()
    return post


def payload(**kwargs) -> CreatePostRequest:
    """构造请求对象。

    需要绕过 Pydantic 校验的用例（验证服务层兜底）用 `raw_payload`——
    正常路径一律走真实 schema，否则测的不是生产会走的那条路。
    """
    base = {"content": "今天吃了一碗热干面", "postType": "daily_share", "visibility": "public"}
    base.update(kwargs)
    return CreatePostRequest(**base)


def raw_payload(**kwargs) -> CreatePostRequest:
    base = {"content": "今天吃了一碗热干面", "postType": "daily_share", "visibility": "public"}
    base.update(kwargs)
    return CreatePostRequest.model_construct(images=None, **base)


# ==================== 发帖 ====================


@pytest.mark.asyncio
async def test_create_rejects_blank_content(db):
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.create(1, raw_payload(content="   "))
    assert info.value.code == 400
    assert info.value.message == "帖子内容不能为空"


@pytest.mark.asyncio
async def test_create_rejects_sensitive_word(db):
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.create(1, payload(content="这里有赌博链接"))
    assert info.value.message == "内容包含不适宜发布的词语"


@pytest.mark.asyncio
async def test_create_rejects_foreign_image_prefix(db):
    user = await _user(db)
    service = build_service(db, storage=FakeStorage({f"posts/{user.id + 1}/x.png": "image/png"}))
    with pytest.raises(BusinessError) as info:
        await service.create(
            user.id,
            payload(images=[{"key": f"posts/{user.id + 1}/x.png", "originalName": "x.png"}]),
        )
    assert info.value.message == "图片对象不合法"


@pytest.mark.asyncio
async def test_create_rejects_dotdot_in_image_key(db):
    user = await _user(db)
    key = f"posts/{user.id}/../other/x.png"
    service = build_service(db, storage=FakeStorage({key: "image/png"}))
    with pytest.raises(BusinessError) as info:
        await service.create(user.id, payload(images=[{"key": key}]))
    assert info.value.message == "图片对象不合法"


@pytest.mark.asyncio
async def test_create_rejects_duplicate_image_keys(db):
    user = await _user(db)
    key = f"posts/{user.id}/2026-11-18/a.png"
    service = build_service(db, storage=FakeStorage({key: "image/png"}))
    with pytest.raises(BusinessError) as info:
        await service.create(user.id, payload(images=[{"key": key}, {"key": key}]))
    assert info.value.message == "不能重复添加图片"


@pytest.mark.asyncio
async def test_create_rejects_missing_object(db):
    user = await _user(db)
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.create(user.id, payload(images=[{"key": f"posts/{user.id}/2026-11-18/a.png"}]))
    # 对象不在存储里：与 Java 一样是 404，不是 400。
    assert info.value.code == 404
    assert info.value.message == "图片不存在"


@pytest.mark.asyncio
async def test_create_rejects_image_reused_by_other_post(db):
    author = await _user(db)
    other = await _post(db, author)
    key = f"posts/{author.id}/2026-11-18/a.png"
    db.add(
        PostImage(
            post_id=other.id,
            object_key=key,
            content_type="image/png",
            sort_order=0,
        )
    )
    await db.commit()

    service = build_service(db, storage=FakeStorage({key: "image/png"}))
    with pytest.raises(BusinessError) as info:
        await service.create(author.id, payload(images=[{"key": key}]))
    assert info.value.message == "图片已经用于其他帖子"


@pytest.mark.asyncio
async def test_create_success_keeps_image_order(db):
    user = await _user(db)
    keys = [f"posts/{user.id}/2026-11-18/{name}.png" for name in ("a", "b")]
    service = build_service(db, storage=FakeStorage({key: "image/png" for key in keys}))

    row = await service.create(
        user.id,
        payload(images=[{"key": key, "originalName": "图.png"} for key in keys]),
    )
    await db.commit()

    assert row["status"] == "published"
    assert row["visibility"] == "public"
    assert row["publishedAt"] is not None
    assert row["imageUrls"] == [f"/api/files/images/{key}" for key in keys]
    assert row["likeCount"] == 0 and row["commentCount"] == 0
    assert row["authorHidden"] is False
    assert row["authorName"] == "橙子"


@pytest.mark.asyncio
async def test_create_checks_limit_after_content_validation(db):
    """内容不合法时不能扣限流额度——顺序反了用户会莫名收到 429。"""
    limiter = FakeLimiter(deny="post")
    service = build_service(db, limiter=limiter)
    with pytest.raises(BusinessError) as info:
        await service.create(1, raw_payload(content="  "))
    assert info.value.message == "帖子内容不能为空"
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_create_propagates_rate_limit(db):
    user = await _user(db)
    limiter = FakeLimiter(deny="post")
    service = build_service(db, limiter=limiter)
    with pytest.raises(BusinessError) as info:
        await service.create(user.id, payload())
    assert info.value.code == 429
    assert info.value.retry_after_seconds == 1800
    assert limiter.calls == ["post"]


# ==================== 可见性 ====================


@pytest.mark.asyncio
async def test_private_post_is_forbidden_for_others(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author, visibility="private")
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.get(visitor.id, post.id)
    assert info.value.code == 403
    assert info.value.message == "无权查看该帖子"
    # 作者本人可以看。
    assert (await service.get(author.id, post.id))["visibility"] == "private"


@pytest.mark.asyncio
async def test_hidden_post_visible_to_author_only(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(
        db, author, status="hidden", moderation_reason="含广告", moderated_at=datetime(2026, 11, 18, 13)
    )
    service = build_service(db)

    own = await service.get(author.id, post.id)
    assert own["status"] == "hidden"
    assert own["moderationReason"] == "含广告"

    with pytest.raises(BusinessError) as info:
        await service.get(visitor.id, post.id)
    assert info.value.code == 404
    assert info.value.message == "帖子已不可查看"


@pytest.mark.asyncio
async def test_deleted_post_is_not_found(db):
    author = await _user(db)
    post = await _post(db, author, status="deleted")
    with pytest.raises(BusinessError) as info:
        await build_service(db).get(author.id, post.id)
    assert info.value.message == "帖子不存在"


@pytest.mark.asyncio
async def test_moderation_reason_hidden_from_other_users(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author, moderation_reason="含广告")
    service = build_service(db)
    assert (await service.get(visitor.id, post.id))["moderationReason"] is None


@pytest.mark.asyncio
async def test_admin_sees_moderation_reason(db):
    author = await _user(db)
    admin = await _user(db, "管理员", role="admin")
    post = await _post(db, author, moderation_reason="含广告")
    row = await build_service(db).get(admin.id, post.id)
    assert row["moderationReason"] == "含广告"


# ==================== 点赞 ====================


@pytest.mark.asyncio
async def test_like_is_idempotent(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = build_service(db)

    first = await service.like(visitor.id, post.id)
    await db.commit()
    second = await service.like(visitor.id, post.id)
    await db.commit()

    assert first == {"liked": True, "likeCount": 1}
    assert second == {"liked": True, "likeCount": 1}
    total = await db.scalar(select(func.count()).select_from(PostLike))
    assert total == 1


@pytest.mark.asyncio
async def test_like_private_post_of_others_is_forbidden(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author, visibility="private")
    with pytest.raises(BusinessError) as info:
        await build_service(db).like(visitor.id, post.id)
    assert info.value.code == 403
    # 文案与 get 不同：这是"无权操作"，不是"无权查看"。
    assert info.value.message == "无权操作该帖子"


@pytest.mark.asyncio
async def test_author_cannot_like_own_hidden_post(db):
    """下架帖连作者都不能互动，但作者仍然可以打开看。"""
    author = await _user(db)
    post = await _post(db, author, status="hidden")
    with pytest.raises(BusinessError) as info:
        await build_service(db).like(author.id, post.id)
    assert info.value.code == 404
    assert info.value.message == "帖子不存在"


@pytest.mark.asyncio
async def test_unlike_removes_row(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = build_service(db)

    await service.like(visitor.id, post.id)
    await db.commit()
    result = await service.unlike(visitor.id, post.id)
    await db.commit()

    assert result == {"liked": False, "likeCount": 0}


# ==================== 评论 ====================


@pytest.mark.asyncio
async def test_comment_rejects_too_long(db):
    author = await _user(db)
    post = await _post(db, author)
    with pytest.raises(BusinessError) as info:
        await build_service(db).create_comment(author.id, post.id, "字" * 501)
    assert info.value.message == "评论不能超过500字"


@pytest.mark.asyncio
async def test_comment_notifies_post_author(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = build_service(db)

    comment = await service.create_comment(visitor.id, post.id, "看着真香")
    await db.commit()

    assert comment["status"] == "active"
    assert comment["authorName"] == "橙子"
    rows = (await db.scalars(select(Notification))).all()
    assert len(rows) == 1
    assert rows[0].recipient_id == author.id
    assert rows[0].actor_id == visitor.id
    assert rows[0].comment_id == comment["id"]


@pytest.mark.asyncio
async def test_self_comment_does_not_notify(db):
    author = await _user(db)
    post = await _post(db, author)
    await build_service(db).create_comment(author.id, post.id, "自己补一句")
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.asyncio
async def test_comment_rate_limited(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    limiter = FakeLimiter(deny="comment")
    service = build_service(db, limiter=limiter)
    with pytest.raises(BusinessError) as info:
        await service.create_comment(visitor.id, post.id, "太快了")
    assert info.value.code == 429
    assert await db.scalar(select(func.count()).select_from(PostComment)) == 0


@pytest.mark.asyncio
async def test_delete_comment_requires_owner(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    service = build_service(db)
    comment = await service.create_comment(visitor.id, post.id, "我来说两句")
    await db.commit()

    with pytest.raises(BusinessError) as info:
        await service.delete_comment(author.id, post.id, comment["id"])
    assert info.value.code == 403
    assert info.value.message == "只能删除自己的评论"


@pytest.mark.asyncio
async def test_deleted_comment_is_not_listed(db):
    author = await _user(db)
    post = await _post(db, author)
    service = build_service(db)
    comment = await service.create_comment(author.id, post.id, "待删")
    await db.commit()

    await service.delete_comment(author.id, post.id, comment["id"])
    await db.commit()

    page = await service.list_comments(author.id, post.id, 1, 10)
    assert page["total"] == 0
    with pytest.raises(BusinessError) as info:
        await service.get_comment(author.id, post.id, comment["id"])
    assert info.value.message == "评论已删除"
    with pytest.raises(BusinessError) as info:
        await service.delete_comment(author.id, post.id, comment["id"])
    assert info.value.message == "评论不存在"


# ==================== 删帖 ====================


@pytest.mark.asyncio
async def test_delete_requires_owner_and_returns_keys(db):
    author = await _user(db)
    visitor = await _user(db)
    post = await _post(db, author)
    key = f"posts/{author.id}/2026-11-18/a.png"
    db.add(PostImage(post_id=post.id, object_key=key, content_type="image/png", sort_order=0))
    await db.commit()

    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.delete(visitor.id, post.id)
    assert info.value.message == "只能删除自己的帖子"

    keys = await service.delete(author.id, post.id)
    await db.commit()
    assert keys == [key]
    assert (await db.get(Post, post.id)).status == "deleted"


# ==================== 列表 ====================


@pytest.mark.asyncio
async def test_list_public_filters_hidden_and_private(db):
    author = await _user(db)
    published = await _post(db, author)
    await _post(db, author, status="hidden")
    await _post(db, author, visibility="private")

    page = await build_service(db).list_public(author.id, 1, 10)
    assert page["total"] == 1
    assert [row["id"] for row in page["records"]] == [published.id]
    assert page["pages"] == 1


@pytest.mark.asyncio
async def test_list_mine_includes_hidden_excludes_deleted(db):
    author = await _user(db)
    await _post(db, author)
    await _post(db, author, status="hidden")
    await _post(db, author, status="deleted")

    page = await build_service(db).list_mine(author.id, 1, 10)
    assert page["total"] == 2
    assert {row["status"] for row in page["records"]} == {"published", "hidden"}


@pytest.mark.asyncio
async def test_page_size_is_clamped(db):
    author = await _user(db)
    for index in range(3):
        await _post(db, author, content=f"第{index}条")
    page = await build_service(db).list_public(author.id, 1, 500)
    assert page["size"] == 20


# ==================== 作者展示 ====================


@pytest.mark.asyncio
async def test_disabled_author_is_hidden(db):
    author = await _user(db, status="disabled")
    post = await _post(db, author)
    row = await build_service(db).get(author.id, post.id)
    assert row["authorHidden"] is True
    assert row["authorId"] is None
    assert row["authorName"] == "已注销用户"
    assert row["authorAvatar"] is None


@pytest.mark.asyncio
async def test_blank_nickname_falls_back_to_orange_friend(db):
    author = await _user(db, nickname="   ")
    post = await _post(db, author)
    row = await build_service(db).get(author.id, post.id)
    assert row["authorName"] == "橘子朋友"


# ==================== 管理端 ====================


@pytest.mark.asyncio
async def test_admin_endpoints_reject_normal_user(db):
    user = await _user(db)
    post = await _post(db, user)
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.list_admin(user.id, page=1, size=10)
    assert info.value.code == 403
    assert info.value.message == "仅管理员可访问社区管理"
    with pytest.raises(BusinessError):
        await service.get_admin(user.id, post.id)


@pytest.mark.asyncio
async def test_admin_list_excludes_private_and_deleted(db):
    author = await _user(db)
    admin = await _user(db, "管理员", role="admin")
    visible = await _post(db, author)
    await _post(db, author, status="hidden")
    await _post(db, author, visibility="private")
    await _post(db, author, status="deleted")

    page = await build_service(db).list_admin(admin.id, page=1, size=10)
    assert page["total"] == 2
    assert visible.id in {row["id"] for row in page["records"]}


@pytest.mark.asyncio
async def test_admin_list_validates_range(db):
    admin = await _user(db, "管理员", role="admin")
    service = build_service(db)
    with pytest.raises(BusinessError) as info:
        await service.list_admin(
            admin.id,
            page=1,
            size=10,
            from_time=datetime(2026, 11, 18, 12),
            until_time=datetime(2026, 11, 18, 12),
        )
    assert info.value.message == "开始时间必须早于结束时间"

    with pytest.raises(BusinessError) as info:
        await service.list_admin(admin.id, page=1, size=10, status="deleted")
    assert info.value.message == "帖子状态不合法"
