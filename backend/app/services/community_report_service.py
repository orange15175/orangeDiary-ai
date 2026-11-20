"""社区举报（对齐 `CommunityReportService`）。

几个顺序上的讲究，改了会直接影响用户体验：

- **重复举报先返回已存在的记录，再走限流。** 顺序反了的话，用户第二次点
  "举报"会因为配额被扣而拿到 429，但页面显示其实什么都没变。
- **`targetSnapshot` 存正文。** 原帖被删改后，管理员仍需看到"被举报的是什么"，
  所以它是文本快照而不是外键。
- **处置用 `WHERE status='pending'` 的条件更新。** 两个管理员同时处理同一条举报，
  后到的那个拿到 409 而不是静默覆盖，处置动作也不会执行两遍。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.community import CommunityReport, Post, PostComment
from app.models.user import User
from app.services.community_limit import CommunityLimiter
from app.services.moderation_service import ModerationService
from app.services.paging import safe_page, safe_size

REASONS = (
    "SPAM",
    "HARASSMENT",
    "HEALTH_MISINFORMATION",
    "SEXUAL_OR_VIOLENT",
    "PRIVACY",
    "OTHER",
)
RESOLUTIONS = ("HIDE_POST", "REMOVE_COMMENT", "DISMISS")
STATUSES = ("pending", "actioned", "dismissed")


class CommunityReportService:
    def __init__(self, db: AsyncSession, *, limiter: CommunityLimiter) -> None:
        self._db = db
        self._limiter = limiter

    # ==================== 提交 ====================

    async def report_post(self, user_id: int, post_id: int, payload: Any) -> dict[str, Any]:
        post = await self._public_post(post_id)
        if post.user_id == user_id:
            raise BusinessError(400, "不能举报自己的帖子")
        return await self._create(
            reporter_id=user_id,
            target_type="POST",
            target_id=post.id,
            post_id=post.id,
            target_author_id=post.user_id,
            snapshot=post.content,
            payload=payload,
        )

    async def report_comment(
        self, user_id: int, post_id: int, comment_id: int, payload: Any
    ) -> dict[str, Any]:
        post = await self._public_post(post_id)
        comment = await self._db.get(PostComment, comment_id)
        if comment is None or comment.post_id != post_id or comment.status != "active":
            raise BusinessError(404, "评论不存在或已不可举报")
        if comment.user_id == user_id:
            raise BusinessError(400, "不能举报自己的评论")
        return await self._create(
            reporter_id=user_id,
            target_type="COMMENT",
            target_id=comment.id,
            post_id=post.id,
            target_author_id=comment.user_id,
            snapshot=comment.content,
            payload=payload,
        )

    async def _create(
        self,
        *,
        reporter_id: int,
        target_type: str,
        target_id: int,
        post_id: int,
        target_author_id: int,
        snapshot: str,
        payload: Any,
    ) -> dict[str, Any]:
        reason = (payload.reason or "").strip()
        if reason not in REASONS:
            raise BusinessError(400, "举报原因不合法")
        details = (payload.details or "").strip() or None

        existing = await self._find(reporter_id, target_type, target_id)
        if existing is not None:
            return await self._row(existing)

        await self._limiter.check_report(reporter_id)

        report = CommunityReport(
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            post_id=post_id,
            target_author_id=target_author_id,
            reason=reason,
            details=details,
            target_snapshot=snapshot,
            status="pending",
            created_at=datetime.now(),
        )
        self._db.add(report)
        try:
            await self._db.flush()
        except IntegrityError:
            # 唯一键 (reporter_id, target_type, target_id) 兜底并发双击：
            # 对方已经写进去了，取它那条返回，前端看到的仍是"已举报"。
            await self._db.rollback()
            winner = await self._find(reporter_id, target_type, target_id)
            if winner is None:
                raise
            return await self._row(winner)
        return await self._row(report)

    # ==================== 管理端 ====================

    async def list_admin(
        self, user_id: int, *, page: int, size: int, status: str | None = None
    ) -> dict[str, Any]:
        await self._require_admin(user_id)
        if status is not None and status.strip() and status not in STATUSES:
            raise BusinessError(400, "举报状态不合法")

        current = safe_page(page)
        limit = safe_size(size, 50)
        conditions: list[Any] = []
        if status is not None and status.strip():
            conditions.append(CommunityReport.status == status)

        total = await self._db.scalar(
            select(func.count()).select_from(CommunityReport).where(*conditions)
        )
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(CommunityReport)
                .where(*conditions)
                # pending 排最前（字符串升序 "actioned" < "dismissed" < "pending"，
                # 这里故意保留 Java 的字面排序，前端靠 order 字段再排一次）。
                .order_by(
                    CommunityReport.status.asc(),
                    CommunityReport.created_at.desc(),
                    CommunityReport.id.desc(),
                )
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        records = [await self._row(row) for row in rows]
        return {
            "records": records,
            "total": total,
            "size": limit,
            "current": current,
            "pages": (total + limit - 1) // limit if limit else 0,
        }

    async def resolve(
        self, user_id: int, report_id: int, *, resolution: str | None, reason: str | None
    ) -> dict[str, Any]:
        admin = await self._require_admin(user_id)
        action = (resolution or "").strip()
        if action not in RESOLUTIONS:
            raise BusinessError(400, "处理结果不合法")
        text = (reason or "").strip()
        if not text or len(text) > 500:
            raise BusinessError(400, "处理原因须为1至500字")

        report = await self._db.scalar(
            select(CommunityReport).where(CommunityReport.id == report_id).with_for_update()
        )
        if report is None:
            raise BusinessError(404, "举报记录不存在")
        if report.status != "pending":
            raise BusinessError(409, "该举报已处理")

        moderation = ModerationService(self._db)
        if action == "HIDE_POST":
            if report.target_type != "POST":
                raise BusinessError(400, "该举报目标不是帖子")
            # 复用处置服务：状态 CAS、流水、站内信都走同一条路径，
            # 避免"从举报页下架"和"从管理页下架"写出两种日志。
            await moderation.change_post(admin.id, report.post_id, restore=False, reason=text)
            new_status = "actioned"
        elif action == "REMOVE_COMMENT":
            if report.target_type != "COMMENT":
                raise BusinessError(400, "该举报目标不是评论")
            await moderation.remove_comment(
                admin.id, report.post_id, report.target_id, reason=text
            )
            new_status = "actioned"
        else:
            new_status = "dismissed"

        processed_at = datetime.now()
        changed = await self._db.execute(
            update(CommunityReport)
            .where(CommunityReport.id == report_id, CommunityReport.status == "pending")
            .values(
                status=new_status,
                processed_by=admin.id,
                processed_at=processed_at,
                resolution=action,
                resolution_reason=text,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise BusinessError(409, "该举报已被其他管理员处理")

        report.status = new_status
        report.processed_by = admin.id
        report.processed_at = processed_at
        report.resolution = action
        report.resolution_reason = text
        return await self._row(report)

    # ==================== 内部 ====================

    async def _public_post(self, post_id: int) -> Post:
        post = await self._db.get(Post, post_id)
        if post is None or post.visibility != "public" or post.status != "published":
            raise BusinessError(404, "帖子不存在或已不可举报")
        return post

    async def _find(self, reporter_id: int, target_type: str, target_id: int):
        return await self._db.scalar(
            select(CommunityReport).where(
                CommunityReport.reporter_id == reporter_id,
                CommunityReport.target_type == target_type,
                CommunityReport.target_id == target_id,
            )
        )

    async def _require_admin(self, user_id: int) -> User:
        user = await self._db.get(User, user_id)
        if user is None or user.status != "active" or user.role != "admin":
            raise BusinessError(403, "仅管理员可处理社区举报")
        return user

    async def _row(self, report: CommunityReport) -> dict[str, Any]:
        reporter = await self._db.get(User, report.reporter_id)
        author = await self._db.get(User, report.target_author_id)
        processed_by_name = None
        if report.processed_by is not None:
            operator = await self._db.get(User, report.processed_by)
            processed_by_name = _display_name(operator)
        return {
            "id": report.id,
            "reporterId": report.reporter_id,
            "reporterName": _display_name(reporter),
            "targetType": report.target_type,
            "targetId": report.target_id,
            "postId": report.post_id,
            "targetAuthorId": report.target_author_id,
            "targetAuthorName": _display_name(author),
            "reason": report.reason,
            "details": report.details,
            "targetSnapshot": report.target_snapshot,
            "status": report.status,
            "processedBy": report.processed_by,
            "processedByName": processed_by_name,
            "processedAt": report.processed_at.isoformat() if report.processed_at else None,
            "resolution": report.resolution,
            "resolutionReason": report.resolution_reason,
            "createdAt": report.created_at.isoformat() if report.created_at else None,
        }


def _display_name(user: User | None) -> str:
    if user is None or user.status != "active":
        return "已注销用户"
    name = (user.nickname or "").strip()
    return name or "橘子朋友"
