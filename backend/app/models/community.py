"""社区与通知：post / post_comment / post_like / post_image / community_report / notification。

这些表的结构来自四份迁移脚本（post / moderation / community_report / unified_notifications），
这里合并成 ORM 定义。`post` 与 `post_comment` 的审核字段是后来 ALTER 加的，
在 ORM 里就是普通可空列，不需要再区分来源脚本。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class Post(Base):
    __tablename__ = "post"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False, index=True)
    post_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    # public / private
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'public'"), default="public"
    )
    # published / hidden / deleted
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'published'"), default="published"
    )
    moderation_reason: Mapped[str | None] = mapped_column(String(500))
    moderated_by: Mapped[int | None] = mapped_column(BigIntPK)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PostComment(Base):
    __tablename__ = "post_comment"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    # active / removed
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    moderation_reason: Mapped[str | None] = mapped_column(String(500))
    moderated_by: Mapped[int | None] = mapped_column(BigIntPK)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PostLike(Base):
    __tablename__ = "post_like"

    # 复合主键：同一个人对同一条帖子只能点一次赞。
    post_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("post.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PostImage(Base):
    __tablename__ = "post_image"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CommunityReport(Base):
    __tablename__ = "community_report"
    __table_args__ = (
        UniqueConstraint("reporter_id", "target_type", "target_id", name="uk_reporter_target"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    post_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("post.id"), nullable=False)
    target_author_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[str | None] = mapped_column(String(500))
    # 举报时的内容快照：原帖被删改后仍要能看到"被举报的是什么"。
    target_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    # pending / accepted / rejected
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"), default="pending"
    )
    processed_by: Mapped[int | None] = mapped_column(BigIntPK)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution: Mapped[str | None] = mapped_column(String(30))
    resolution_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (UniqueConstraint("comment_id", name="uk_notification_comment"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    recipient_id: Mapped[int] = mapped_column(BigIntPK, nullable=False, index=True)
    # COMMENT / SYSTEM
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'COMMENT'"), default="COMMENT"
    )
    actor_id: Mapped[int | None] = mapped_column(BigIntPK)
    post_id: Mapped[int | None] = mapped_column(BigIntPK)
    comment_id: Mapped[int | None] = mapped_column(BigIntPK)
    title: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class AdminActionLog(Base):
    """管理员处置流水。

    存在的意义是"事后能说清是谁、以什么理由、把什么改成了什么"。
    `snapshot` 存处置时的正文快照——帖子被改或删掉之后，
    争议仍然要能回看当时到底写了什么，所以它不能是外键而是文本。
    """

    __tablename__ = "admin_action_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    admin_name: Mapped[str | None] = mapped_column(String(100))
    # POST_HIDE / POST_RESTORE / COMMENT_REMOVE
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # POST / COMMENT
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    post_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    author_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    before_status: Mapped[str] = mapped_column(String(20), nullable=False)
    after_status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
