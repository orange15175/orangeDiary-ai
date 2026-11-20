"""社区帖子（对齐 `PostServiceImpl`）。

这是全项目分支最多的一块，所以先写清三条容易踩的判定：

1. **`get` 和 `like/comment` 用的是两套可见性判定，不能合并。**
   `get` 允许"自己的帖子自己看"，哪怕状态是 hidden；`_require_interactive`
   只认 `published`——下架的帖子连作者自己都不能点赞/评论，但能打开看自己被
   下架的帖子和原因。合并成一套会立刻改变其中一个行为。
2. **私有帖的报错文案不同。** `get` 是"无权查看该帖子"，点赞/评论是"无权操作
   该帖子"。前端按文案决定"返回列表"还是"提示"，所以必须分开。
3. **`作者已注销` 时 `authorId` 也要置空。** 只清名字不清 ID，前端仍能点进
   用户主页，等于泄露一个已注销账号的 ID。

删帖的对象存储清理放在事务提交之后（由路由层做）：Java 用
`TransactionSynchronization.afterCommit`，这里让服务返回待删 key、路由
`commit()` 后再删。顺序颠倒会在回滚时把还在被引用的图片删掉。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.community import Post, PostComment, PostImage, PostLike
from app.models.user import User
from app.services.community_limit import CommunityLimiter
from app.services.image_storage import ImageStorage
from app.services.notification_service import NotificationService
from app.services.paging import mp_page, safe_page, safe_size

# 与 Java `SENSITIVE` 同一份清单：命中即拒，不做替换。命中"违法"两个字的健康
# 讨论也会被拦——这是产品取舍，不是实现缺陷，改词表要同步改前端提示。
SENSITIVE_WORDS = ("违法", "赌博", "诈骗", "色情")

POST_TYPES = ("diet_checkin", "exercise", "weight", "daily_share")
MAX_IMAGES = 9
MAX_CONTENT = 2000
MAX_COMMENT = 500

# 图片 URL 前缀；`ImageController` 的 GET 也按这个前缀解析 key。
IMAGE_URL_PREFIX = "/api/files/images/"


def display_name(user: User | None) -> str:
    """作者名兜底：注销 → 已注销用户，空昵称 → 橘子朋友。"""
    if user is None or user.status != "active":
        return "已注销用户"
    name = (user.nickname or "").strip()
    return name or "橘子朋友"


def _is_hidden_author(user: User | None) -> bool:
    return user is None or user.status != "active"


class PostService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        limiter: CommunityLimiter,
        storage: ImageStorage,
    ) -> None:
        self._db = db
        self._limiter = limiter
        self._storage = storage

    # ==================== 写 ====================

    async def create(self, user_id: int, payload: Any) -> dict[str, Any]:
        content = (payload.content or "").strip()
        if not content:
            raise BusinessError(400, "帖子内容不能为空")
        if any(word in content for word in SENSITIVE_WORDS):
            raise BusinessError(400, "内容包含不适宜发布的词语")

        images = list(payload.images or [])
        if len(images) > MAX_IMAGES:
            raise BusinessError(400, "最多添加9张图片")
        keys = [image.key for image in images]
        if len(set(keys)) != len(keys):
            raise BusinessError(400, "不能重复添加图片")

        content_types: dict[str, str] = {}
        for key in keys:
            if not key or not key.startswith(f"posts/{user_id}/") or ".." in key:
                raise BusinessError(400, "图片对象不合法")
            # 对象不存在时这里抛 404「图片不存在」——与 Java 一致：前端拿到
            # 上传成功响应后立刻发帖，正常不会走到；走到了说明对象被清过。
            content_type = await self._storage.content_type_of(key)
            content_types[key] = content_type
            existing = await self._db.scalar(
                select(func.count()).select_from(PostImage).where(PostImage.object_key == key)
            )
            if existing:
                raise BusinessError(400, "图片已经用于其他帖子")
            if not content_type or not content_type.startswith("image/"):
                raise BusinessError(400, "附件不是有效图片")

        # 限流放在所有内容校验之后：内容不合法就占掉一次发帖额度，用户会以为
        # "我改了错别字也算发帖"，比 429 本身更难理解。
        await self._limiter.check_post(user_id)

        post = Post(
            user_id=user_id,
            content=content,
            post_type=payload.postType,
            visibility=payload.visibility,
            status="published",
            published_at=datetime.now(),
            created_at=datetime.now(),
        )
        self._db.add(post)
        await self._db.flush()

        for index, image in enumerate(images):
            self._db.add(
                PostImage(
                    post_id=post.id,
                    object_key=image.key,
                    original_name=image.originalName,
                    content_type=content_types[image.key],
                    sort_order=index,
                )
            )
        await self._db.flush()
        return await self._single_row(post, viewer_id=user_id)

    async def delete(self, user_id: int, post_id: int) -> list[str]:
        """软删帖子，返回需要在提交后从对象存储删除的 key。"""
        post = await self._db.scalar(select(Post).where(Post.id == post_id).with_for_update())
        if post is None or post.status == "deleted":
            raise BusinessError(404, "帖子不存在")
        if post.user_id != user_id:
            raise BusinessError(403, "只能删除自己的帖子")

        post.status = "deleted"
        keys = list(
            (
                await self._db.scalars(
                    select(PostImage.object_key)
                    .where(PostImage.post_id == post_id)
                    .order_by(PostImage.sort_order)
                )
            ).all()
        )
        await self._db.flush()
        return keys

    # ==================== 点赞 ====================

    async def like(self, user_id: int, post_id: int) -> dict[str, Any]:
        await self._require_interactive(post_id, user_id, lock=True)
        values = {"post_id": post_id, "user_id": user_id}
        # 重复点赞不报错：并发双击会同时走到这里，靠唯一键吞掉第二条，
        # 而不是先查后插（那在并发下照样会撞键）。
        dialect = self._db.get_bind().dialect.name
        if dialect == "mysql":
            statement = mysql_insert(PostLike).values(**values).prefix_with("IGNORE")
        elif dialect == "sqlite":
            statement = sqlite_insert(PostLike).values(**values).on_conflict_do_nothing()
        else:  # pragma: no cover - 只会用在 mysql/sqlite
            statement = mysql_insert(PostLike).values(**values)
        await self._db.execute(statement)
        return {"liked": True, "likeCount": await self._like_count(post_id)}

    async def unlike(self, user_id: int, post_id: int) -> dict[str, Any]:
        await self._require_interactive(post_id, user_id, lock=True)
        await self._db.execute(
            delete(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
        )
        return {"liked": False, "likeCount": await self._like_count(post_id)}

    # ==================== 评论 ====================

    async def create_comment(self, user_id: int, post_id: int, content: str | None) -> dict[str, Any]:
        post = await self._require_interactive(post_id, user_id, lock=True)
        text = (content or "").strip()
        if not text:
            raise BusinessError(400, "评论内容不能为空")
        # Java 用 `String.length()`（UTF-16 code unit），emoji 算 2；Python 算 1。
        # 只影响 500 字边界上的极端输入，不为此引入 UTF-16 计数。
        if len(text) > MAX_COMMENT:
            raise BusinessError(400, "评论不能超过500字")

        await self._limiter.check_comment(user_id)
        comment = PostComment(
            post_id=post_id,
            user_id=user_id,
            content=text,
            status="active",
            created_at=datetime.now(),
        )
        self._db.add(comment)
        await self._db.flush()
        # 自己评论自己的帖子不发通知，判断在 NotificationService 里。
        await NotificationService(self._db).notify_comment(post, comment)
        await self._db.flush()
        return await self._comment_row(comment)

    async def delete_comment(self, user_id: int, post_id: int, comment_id: int) -> None:
        await self._require_interactive(post_id, user_id, lock=True)
        comment = await self._db.get(PostComment, comment_id)
        if comment is None or comment.post_id != post_id or comment.status != "active":
            raise BusinessError(404, "评论不存在")
        if comment.user_id != user_id:
            raise BusinessError(403, "只能删除自己的评论")
        comment.status = "deleted"
        await self._db.flush()

    # ==================== 读 ====================

    async def list_public(self, viewer_id: int, page: int, size: int) -> dict[str, Any]:
        return await self._list(
            page,
            size,
            [Post.status == "published", Post.visibility == "public"],
            order=(Post.published_at.desc(), Post.id.desc()),
            viewer_id=viewer_id,
        )

    async def list_mine(self, user_id: int, page: int, size: int) -> dict[str, Any]:
        return await self._list(
            page,
            size,
            [Post.user_id == user_id, Post.status != "deleted"],
            order=(Post.created_at.desc(), Post.id.desc()),
            viewer_id=user_id,
        )

    async def get(self, user_id: int, post_id: int) -> dict[str, Any]:
        post = await self._db.get(Post, post_id)
        if post is None or post.status == "deleted":
            raise BusinessError(404, "帖子不存在")
        if post.visibility == "private" and post.user_id != user_id:
            raise BusinessError(403, "无权查看该帖子")
        if post.status != "published" and post.user_id != user_id:
            raise BusinessError(404, "帖子已不可查看")
        return await self._single_row(post, viewer_id=user_id)

    async def list_comments(self, user_id: int, post_id: int, page: int, size: int) -> dict[str, Any]:
        await self._require_interactive(post_id, user_id)
        current = safe_page(page)
        limit = safe_size(size)
        conditions = [PostComment.post_id == post_id, PostComment.status == "active"]
        total = await self._db.scalar(
            select(func.count()).select_from(PostComment).where(*conditions)
        )
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(PostComment)
                .where(*conditions)
                .order_by(PostComment.created_at.desc(), PostComment.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return mp_page(
            await self._comment_rows(list(rows)), total=total, current=current, limit=limit
        )

    async def get_comment(self, user_id: int, post_id: int, comment_id: int) -> dict[str, Any]:
        await self._require_interactive(post_id, user_id)
        comment = await self._db.get(PostComment, comment_id)
        if comment is None or comment.post_id != post_id or comment.status != "active":
            raise BusinessError(404, "评论已删除")
        return await self._comment_row(comment)

    # ==================== 管理端 ====================

    async def list_admin(
        self,
        user_id: int,
        *,
        page: int,
        size: int,
        keyword: str | None = None,
        author_id: int | None = None,
        post_type: str | None = None,
        status: str | None = None,
        from_time: datetime | None = None,
        until_time: datetime | None = None,
    ) -> dict[str, Any]:
        await self._require_admin(user_id, "仅管理员可访问社区管理")
        if keyword is not None and len(keyword) > 100:
            raise BusinessError(400, "关键词不能超过100字")
        if author_id is not None and author_id < 1:
            raise BusinessError(400, "作者ID不合法")
        if post_type is not None and post_type.strip() and post_type not in POST_TYPES:
            raise BusinessError(400, "帖子类型不合法")
        if from_time is not None and until_time is not None and not from_time < until_time:
            raise BusinessError(400, "开始时间必须早于结束时间")
        if status is not None and status.strip() and status not in ("published", "hidden"):
            raise BusinessError(400, "帖子状态不合法")

        conditions: list[Any] = [
            Post.status.in_(("published", "hidden")),
            Post.visibility == "public",
        ]
        if status is not None and status.strip():
            conditions.append(Post.status == status)
        if keyword is not None and keyword.strip():
            conditions.append(Post.content.like(_like_pattern(keyword.strip()), escape="!"))
        if author_id is not None:
            conditions.append(Post.user_id == author_id)
        if post_type is not None and post_type.strip():
            conditions.append(Post.post_type == post_type)
        if from_time is not None:
            conditions.append(Post.published_at >= from_time)
        # until 是左闭右开：Java 用 `lt`，前端传整点不会出现"23:59:59.999 漏一条"。
        if until_time is not None:
            conditions.append(Post.published_at < until_time)

        return await self._list(
            page,
            size,
            conditions,
            order=(Post.published_at.desc(), Post.id.desc()),
            viewer_id=user_id,
            admin_viewer=True,
        )

    async def get_admin(self, user_id: int, post_id: int) -> dict[str, Any]:
        await self._require_admin(user_id, "仅管理员可访问社区管理")
        post = await self._require_admin_post(post_id)
        return await self._single_row(post, viewer_id=user_id, admin_viewer=True)

    async def comments_admin(
        self, user_id: int, post_id: int, page: int, size: int
    ) -> dict[str, Any]:
        await self._require_admin(user_id, "仅管理员可访问社区管理")
        await self._require_admin_post(post_id)
        current = safe_page(page)
        limit = safe_size(size)
        conditions = [
            PostComment.post_id == post_id,
            PostComment.status.in_(("active", "removed")),
        ]
        total = await self._db.scalar(
            select(func.count()).select_from(PostComment).where(*conditions)
        )
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(PostComment)
                .where(*conditions)
                .order_by(PostComment.created_at.desc(), PostComment.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return mp_page(
            await self._comment_rows(list(rows), with_moderation=True),
            total=total,
            current=current,
            limit=limit,
        )

    # ==================== 内部：判定 ====================

    async def _require_interactive(self, post_id: int, user_id: int, *, lock: bool = False):
        statement = select(Post).where(Post.id == post_id)
        if lock:
            # 点赞/评论先锁行：并发下"赞 + 取消赞"与"评论 + 删评论"要串行，
            # 否则计数与状态可能跨过一个中间态。SQLite 方言会忽略该子句。
            statement = statement.with_for_update()
        post = await self._db.scalar(statement)
        if post is None or post.status != "published":
            raise BusinessError(404, "帖子不存在")
        if post.visibility == "private" and post.user_id != user_id:
            raise BusinessError(403, "无权操作该帖子")
        return post

    async def _require_admin(self, user_id: int, message: str) -> User:
        # 每次都回库读：管理员被降权后，手上的旧 token 不该还能操作。
        user = await self._db.get(User, user_id)
        if user is None or user.status != "active" or user.role != "admin":
            raise BusinessError(403, message)
        return user

    async def _require_admin_post(self, post_id: int) -> Post:
        post = await self._db.get(Post, post_id)
        if (
            post is None
            or post.status not in ("published", "hidden")
            or post.visibility != "public"
        ):
            raise BusinessError(404, "公开帖子不存在或已不可查看")
        return post

    async def _is_admin(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        user = await self._db.get(User, user_id)
        return user is not None and user.status == "active" and user.role == "admin"

    async def _like_count(self, post_id: int) -> int:
        count = await self._db.scalar(
            select(func.count()).select_from(PostLike).where(PostLike.post_id == post_id)
        )
        return int(count or 0)

    # ==================== 内部：组装 ====================

    async def _list(
        self,
        page: int,
        size: int,
        conditions: list[Any],
        *,
        order: tuple[Any, ...],
        viewer_id: int | None = None,
        admin_viewer: bool | None = None,
    ) -> dict[str, Any]:
        current = safe_page(page)
        limit = safe_size(size)
        total = await self._db.scalar(select(func.count()).select_from(Post).where(*conditions))
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(Post)
                .where(*conditions)
                .order_by(*order)
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        records = await self._rows(list(rows), viewer_id=viewer_id, admin_viewer=admin_viewer)
        return mp_page(records, total=total, current=current, limit=limit)

    async def _single_row(
        self, post: Post, *, viewer_id: int | None, admin_viewer: bool | None = None
    ) -> dict[str, Any]:
        rows = await self._rows([post], viewer_id=viewer_id, admin_viewer=admin_viewer)
        return rows[0]

    async def _rows(
        self,
        posts: list[Post],
        *,
        viewer_id: int | None,
        admin_viewer: bool | None = None,
    ) -> list[dict[str, Any]]:
        if not posts:
            return []
        # admin_viewer 留 None 表示"按查看者身份判断"：Java 的 `toResponse` 里
        # 每条都问一次 `isAdminViewer()`，管理员在普通列表里也能看到处置原因。
        if admin_viewer is None:
            admin_viewer = await self._is_admin(viewer_id)
        post_ids = [post.id for post in posts]
        author_ids = {post.user_id for post in posts}

        authors = {
            user.id: user
            for user in (
                await self._db.scalars(select(User).where(User.id.in_(author_ids)))
            ).all()
        }

        images: dict[int, list[str]] = {}
        for image in (
            await self._db.scalars(
                select(PostImage)
                .where(PostImage.post_id.in_(post_ids))
                .order_by(PostImage.post_id, PostImage.sort_order)
            )
        ).all():
            images.setdefault(image.post_id, []).append(IMAGE_URL_PREFIX + image.object_key)

        like_counts = dict(
            (
                await self._db.execute(
                    select(PostLike.post_id, func.count())
                    .where(PostLike.post_id.in_(post_ids))
                    .group_by(PostLike.post_id)
                )
            ).all()
        )
        comment_counts = dict(
            (
                await self._db.execute(
                    select(PostComment.post_id, func.count())
                    .where(
                        PostComment.post_id.in_(post_ids), PostComment.status == "active"
                    )
                    .group_by(PostComment.post_id)
                )
            ).all()
        )

        liked: set[int] = set()
        if viewer_id is not None:
            liked = set(
                (
                    await self._db.scalars(
                        select(PostLike.post_id).where(
                            PostLike.post_id.in_(post_ids), PostLike.user_id == viewer_id
                        )
                    )
                ).all()
            )

        return [
            _post_row(
                post,
                authors.get(post.user_id),
                images.get(post.id, []),
                int(like_counts.get(post.id, 0)),
                int(comment_counts.get(post.id, 0)),
                post.id in liked,
                viewer_id=viewer_id,
                admin_viewer=admin_viewer,
            )
            for post in posts
        ]

    async def _comment_rows(
        self, comments: list[PostComment], *, with_moderation: bool = False
    ) -> list[dict[str, Any]]:
        if not comments:
            return []
        author_ids = {comment.user_id for comment in comments}
        authors = {
            user.id: user
            for user in (
                await self._db.scalars(select(User).where(User.id.in_(author_ids)))
            ).all()
        }
        return [
            _comment_row(comment, authors.get(comment.user_id), with_moderation)
            for comment in comments
        ]

    async def _comment_row(self, comment: PostComment, *, with_moderation: bool = False) -> dict[str, Any]:
        author = await self._db.get(User, comment.user_id)
        return _comment_row(comment, author, with_moderation)


def _like_pattern(keyword: str) -> str:
    """按 MySQL `LIKE ... ESCAPE '!'` 转义。

    用户搜 "50%_" 时，`%` 和 `_` 必须当成字面量；不转义会变成通配符，
    搜"50%"会命中所有以 50 开头的内容，看起来像搜索坏了。
    """
    escaped = keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _post_row(
    post: Post,
    author: User | None,
    image_urls: list[str],
    like_count: int,
    comment_count: int,
    liked_by_viewer: bool,
    *,
    viewer_id: int | None,
    admin_viewer: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": post.id,
        "authorId": None,
        "authorName": None,
        "authorAvatar": None,
        "postType": post.post_type,
        "content": post.content,
        "visibility": post.visibility,
        "status": post.status,
        "moderationReason": None,
        "moderatedAt": None,
        "publishedAt": _ts(post.published_at),
        "imageUrls": image_urls,
        "authorHidden": False,
        "likeCount": like_count,
        "commentCount": comment_count,
        "likedByCurrentUser": liked_by_viewer,
    }
    # 处置原因只给作者本人和管理员看：其他用户看到"因 XX 下架"等于公开了
    # 举报/审核细节。
    if admin_viewer or (viewer_id is not None and post.user_id == viewer_id):
        row["moderationReason"] = post.moderation_reason
        row["moderatedAt"] = _ts(post.moderated_at)

    if _is_hidden_author(author):
        row["authorHidden"] = True
        row["authorName"] = "已注销用户"
    else:
        row["authorId"] = post.user_id
        row["authorName"] = display_name(author)
        row["authorAvatar"] = author.avatar
    return row


def _comment_row(
    comment: PostComment, author: User | None, with_moderation: bool = False
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": comment.id,
        "postId": comment.post_id,
        "authorId": None,
        "authorName": None,
        "authorAvatar": None,
        "content": comment.content,
        "status": comment.status,
        "moderationReason": None,
        "createdAt": _ts(comment.created_at),
        "authorHidden": False,
    }
    if with_moderation:
        row["moderationReason"] = comment.moderation_reason
    if _is_hidden_author(author):
        row["authorHidden"] = True
        row["authorName"] = "已注销用户"
    else:
        row["authorId"] = comment.user_id
        row["authorName"] = display_name(author)
        row["authorAvatar"] = author.avatar
    return row
