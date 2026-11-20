"""站内消息（对齐 `NotificationService`）。

列表要做"可查看性判定"：帖子被删/被下架/不是自己的、评论被移除，都要给出
`unavailableReason` 而不是让前端点进去看到 403。这套判定分支很多，删掉任意一条
都会让用户点进灰色条目——所以逐条保留，并且按 Java 的优先级排列。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.community import Notification, Post, PostComment
from app.models.user import User

_ALLOWED_TYPES = {"COMMENT", "SYSTEM"}
_PAGE_LIMIT = 50


def notification_row(
    notification: Notification,
    actor: User | None,
    post: Post | None,
    comment: PostComment | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": notification.type,
        "title": notification.title,
        "id": notification.id,
        "postId": notification.post_id,
        "commentId": notification.comment_id,
        "actorName": None,
        "actorAvatar": None,
        "content": None,
        "postExcerpt": None,
        "unavailableReason": None,
        "createdAt": notification.created_at.isoformat() if notification.created_at else None,
        "readAt": notification.read_at.isoformat() if notification.read_at else None,
    }

    if notification.type == "SYSTEM":
        row["actorName"] = "系统通知"
        row["content"] = notification.content
        if notification.post_id is not None:
            visible = (
                post is not None
                and (
                    post.status == "published"
                    or (post.status == "hidden" and post.user_id == notification.recipient_id)
                )
                and (post.visibility == "public" or post.user_id == notification.recipient_id)
            )
            if not visible:
                row["unavailableReason"] = "关联帖子已不可查看"
        return row

    if actor is None or actor.status != "active":
        row["actorName"] = "已注销用户"
    else:
        # 昵称空白时用"橘子朋友"兜底，避免 UI 出现空名字加冒号。
        name = (actor.nickname or "").strip()
        row["actorName"] = name or "橘子朋友"
        row["actorAvatar"] = actor.avatar

    # 优先级：帖子问题 > 评论问题。顺序与 Java 一致，前端按 reason 文案展示。
    if post is None or post.status == "deleted":
        row["unavailableReason"] = "帖子已删除"
    elif post.status == "hidden":
        row["unavailableReason"] = "帖子已被下架"
    elif post.user_id != notification.recipient_id:
        row["unavailableReason"] = "无权查看该帖子"
    else:
        row["postExcerpt"] = post.content[:100]
        if comment is not None and comment.status == "removed":
            row["unavailableReason"] = "评论已被管理员移除"
        elif comment is None or comment.status != "active":
            row["unavailableReason"] = "评论已删除"
        else:
            row["content"] = comment.content
    return row


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(
        self, user_id: int, page: int = 1, size: int = 10, type: str | None = None
    ) -> dict[str, Any]:
        if type is not None and type.strip() and type not in _ALLOWED_TYPES:
            raise BusinessError(400, "消息类型不合法")

        current = max(1, min(1_000_000, page))
        limit = max(1, min(_PAGE_LIMIT, size))

        conditions = [Notification.recipient_id == user_id]
        if type is not None and type.strip():
            conditions.append(Notification.type == type)

        total = await self._db.scalar(select(func.count()).select_from(Notification).where(*conditions))
        total = int(total or 0)
        records = (
            await self._db.scalars(
                select(Notification)
                .where(*conditions)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()

        rows = await self._enrich(list(records))
        pages = (total + limit - 1) // limit
        return {
            "records": rows,
            "total": total,
            "size": limit,
            "current": current,
            "pages": pages,
        }

    async def unread_count(self, user_id: int) -> int:
        count = await self._db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_id == user_id, Notification.read_at.is_(None))
        )
        return int(count or 0)

    async def mark_read(self, user_id: int, notification_id: int) -> None:
        notification = await self._db.scalar(
            select(Notification).where(
                Notification.id == notification_id, Notification.recipient_id == user_id
            )
        )
        if notification is None:
            raise BusinessError(404, "通知不存在")
        # 已读时间只在第一次标记时写；重复点"已读"不能把时间往后推。
        if notification.read_at is None:
            notification.read_at = datetime.now()

    # ---------------- 供社区模块调用 ----------------

    async def notify_comment(self, post: Post, comment: PostComment) -> None:
        """自己评论自己的帖子不发通知——这是 Java 侧的显式判断，不是漏写。"""
        if post.user_id == comment.user_id:
            return
        self._db.add(
            Notification(
                type="COMMENT",
                recipient_id=post.user_id,
                actor_id=comment.user_id,
                post_id=post.id,
                comment_id=comment.id,
                created_at=datetime.now(),
            )
        )

    async def notify_system(
        self, recipient_id: int | None, title: str, content: str, post_id: int | None = None
    ) -> None:
        if (
            recipient_id is None
            or not title
            or not title.strip()
            or len(title) > 100
            or not content
            or not content.strip()
            or len(content) > 2000
        ):
            raise BusinessError(400, "系统通知参数不合法")
        recipient = await self._db.get(User, recipient_id)
        if recipient is None:
            raise BusinessError(404, "通知收件人不存在或已停用")
        self._db.add(
            Notification(
                type="SYSTEM",
                recipient_id=recipient_id,
                title=title.strip(),
                content=content.strip(),
                post_id=post_id,
                created_at=datetime.now(),
            )
        )

    async def _enrich(self, notifications: list[Notification]) -> list[dict[str, Any]]:
        if not notifications:
            return []
        actor_ids = {n.actor_id for n in notifications if n.actor_id is not None}
        post_ids = {n.post_id for n in notifications if n.post_id is not None}
        comment_ids = {n.comment_id for n in notifications if n.comment_id is not None}

        actors = await self._by_ids(User, User.id, actor_ids)
        posts = await self._by_ids(Post, Post.id, post_ids)
        comments = await self._by_ids(PostComment, PostComment.id, comment_ids)
        return [
            notification_row(
                n,
                actors.get(n.actor_id) if n.actor_id else None,
                posts.get(n.post_id) if n.post_id else None,
                comments.get(n.comment_id) if n.comment_id else None,
            )
            for n in notifications
        ]

    async def _by_ids(self, model, column, ids: set[int]) -> dict[int, Any]:
        if not ids:
            return {}
        rows = (await self._db.scalars(select(model).where(column.in_(ids)))).all()
        return {getattr(row, column.key): row for row in rows}
