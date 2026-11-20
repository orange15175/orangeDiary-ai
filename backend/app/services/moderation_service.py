"""社区处置（对齐 `ModerationService`）。

三个"必须做对"的点：

1. **状态用 CAS 改，不先读后写。** 两个管理员同时点"下架"，第二个的
   `UPDATE ... WHERE status='published'` 影响 0 行，返回 409 让前端刷新。
   先读后写会让后到的覆盖先到的，日志里出现两条"published → hidden"。
2. **流水里存正文快照。** 帖子事后可能被作者改掉或删掉，争议复盘时
   光有 post_id 什么都看不到。
3. **处置必发站内信。** 作者不知道帖子为什么消失，只会来问客服；
   通知里带上原因，能省掉大部分申诉。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.community import AdminActionLog, Post, PostComment
from app.models.user import User
from app.services.notification_service import NotificationService
from app.services.paging import safe_page, safe_size

ACTION_TYPES = ("POST_HIDE", "POST_RESTORE", "COMMENT_REMOVE")


class ModerationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ==================== 帖子 ====================

    async def change_post(
        self, user_id: int, post_id: int, *, restore: bool, reason: str | None
    ) -> None:
        operator = await self._admin(user_id)
        text = _reason(reason)
        post = await self._locked_public_post(post_id)

        before, after = ("hidden", "published") if restore else ("published", "hidden")
        if post.status != before:
            raise BusinessError(409, "帖子状态已变化，请刷新后重试")

        moderated_at = datetime.now()
        changed = await self._db.execute(
            update(Post)
            .where(Post.id == post_id, Post.status == before)
            .values(
                status=after,
                moderation_reason=text,
                moderated_by=operator.id,
                moderated_at=moderated_at,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise BusinessError(409, "帖子状态已变化，请刷新后重试")
        # 显式回写会话里的对象：Core UPDATE 不会自动刷新已加载的实例，
        # 依赖 `synchronize_session="auto"` 的启发式会只同步一部分字段。
        post.status = after
        post.moderation_reason = text
        post.moderated_by = operator.id
        post.moderated_at = moderated_at

        await self._log(
            operator,
            action="POST_RESTORE" if restore else "POST_HIDE",
            target_type="POST",
            target_id=post_id,
            post_id=post_id,
            author_id=post.user_id,
            reason=text,
            before=before,
            after=after,
            snapshot=post.content,
        )
        await NotificationService(self._db).notify_system(
            post.user_id,
            "帖子已恢复" if restore else "帖子已下架",
            ("你的帖子已恢复公开展示。恢复原因：" if restore else "你的帖子已被下架。下架原因：")
            + text,
            post_id,
        )
        await self._db.flush()

    # ==================== 评论 ====================

    async def remove_comment(
        self, user_id: int, post_id: int, comment_id: int, *, reason: str | None
    ) -> None:
        operator = await self._admin(user_id)
        text = _reason(reason)
        await self._locked_public_post(post_id)

        comment = await self._db.get(PostComment, comment_id)
        if comment is None or comment.post_id != post_id:
            raise BusinessError(404, "评论不存在")
        if comment.status != "active":
            raise BusinessError(409, "评论已被处理，请刷新后重试")

        moderated_at = datetime.now()
        changed = await self._db.execute(
            update(PostComment)
            .where(PostComment.id == comment_id, PostComment.status == "active")
            .values(
                status="removed",
                moderation_reason=text,
                moderated_by=operator.id,
                moderated_at=moderated_at,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise BusinessError(409, "评论状态已变化，请刷新后重试")
        comment.status = "removed"
        comment.moderation_reason = text
        comment.moderated_by = operator.id
        comment.moderated_at = moderated_at

        await self._log(
            operator,
            action="COMMENT_REMOVE",
            target_type="COMMENT",
            target_id=comment_id,
            post_id=post_id,
            author_id=comment.user_id,
            reason=text,
            before="active",
            after="removed",
            snapshot=comment.content,
        )
        await NotificationService(self._db).notify_system(
            comment.user_id, "评论已被移除", f"你的评论因以下原因被管理员移除：{text}", post_id
        )
        await self._db.flush()

    # ==================== 流水查询 ====================

    async def list_logs(
        self,
        user_id: int,
        *,
        page: int,
        size: int,
        action: str | None = None,
        admin_id: int | None = None,
        target_id: int | None = None,
        from_time: datetime | None = None,
        until_time: datetime | None = None,
    ) -> dict[str, Any]:
        await self._admin(user_id)
        if action is not None and action.strip() and action not in ACTION_TYPES:
            raise BusinessError(400, "操作类型不合法")
        if (admin_id is not None and admin_id < 1) or (target_id is not None and target_id < 1):
            raise BusinessError(400, "查询ID不合法")
        if from_time is not None and until_time is not None and not from_time < until_time:
            raise BusinessError(400, "开始时间必须早于结束时间")

        current = safe_page(page)
        # 流水页上限 50，与帖子列表的 20 不同——这是 Java 两个方法各自的值。
        limit = safe_size(size, 50)
        conditions: list[Any] = []
        if action is not None and action.strip():
            conditions.append(AdminActionLog.action_type == action)
        if admin_id is not None:
            conditions.append(AdminActionLog.admin_id == admin_id)
        if target_id is not None:
            conditions.append(AdminActionLog.target_id == target_id)
        if from_time is not None:
            conditions.append(AdminActionLog.created_at >= from_time)
        if until_time is not None:
            conditions.append(AdminActionLog.created_at < until_time)

        total = await self._db.scalar(
            select(func.count()).select_from(AdminActionLog).where(*conditions)
        )
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(AdminActionLog)
                .where(*conditions)
                .order_by(AdminActionLog.created_at.desc(), AdminActionLog.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return {
            "records": [_log_row(row) for row in rows],
            "total": total,
            "size": limit,
            "current": current,
            "pages": (total + limit - 1) // limit if limit else 0,
        }

    async def get_log(self, user_id: int, log_id: int) -> dict[str, Any]:
        await self._admin(user_id)
        row = await self._db.get(AdminActionLog, log_id)
        if row is None:
            raise BusinessError(404, "操作记录不存在")
        return _log_row(row)

    # ==================== 内部 ====================

    async def _admin(self, user_id: int) -> User:
        user = await self._db.get(User, user_id)
        if user is None or user.status != "active" or user.role != "admin":
            raise BusinessError(403, "仅管理员可操作")
        return user

    async def _locked_public_post(self, post_id: int) -> Post:
        post = await self._db.scalar(
            select(Post).where(Post.id == post_id).with_for_update()
        )
        if (
            post is None
            or post.visibility != "public"
            or post.status not in ("published", "hidden")
        ):
            raise BusinessError(404, "帖子不存在或已不可处理")
        return post

    async def _log(
        self,
        operator: User,
        *,
        action: str,
        target_type: str,
        target_id: int,
        post_id: int,
        author_id: int,
        reason: str,
        before: str,
        after: str,
        snapshot: str,
    ) -> None:
        self._db.add(
            AdminActionLog(
                admin_id=operator.id,
                # 昵称可能为空：流水要留"当时叫什么"，不跟随用户后来改名。
                admin_name=operator.nickname,
                action_type=action,
                target_type=target_type,
                target_id=target_id,
                post_id=post_id,
                author_id=author_id,
                reason=reason,
                before_status=before,
                after_status=after,
                snapshot=snapshot,
                created_at=datetime.now(),
            )
        )


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if not text or len(text) > 500:
        raise BusinessError(400, "请填写1至500字的处理原因")
    return text


def _log_row(row: AdminActionLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "adminId": row.admin_id,
        "adminName": row.admin_name,
        "actionType": row.action_type,
        "targetType": row.target_type,
        "targetId": row.target_id,
        "postId": row.post_id,
        "authorId": row.author_id,
        "reason": row.reason,
        "beforeStatus": row.before_status,
        "afterStatus": row.after_status,
        "snapshot": row.snapshot,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
