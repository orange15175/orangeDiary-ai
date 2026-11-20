"""AI 会话记忆与问答追踪。

五张表，用途各不相同：

- `ai_conversation` / `ai_conversation_message`：多轮对话的持久化。消息表用
  `(conversation_id, message_sequence)` 唯一键保证序号不重复——顺序错了，
  模型看到的历史就是乱的。
- `ai_chat_trace`：一次问答的耗时与检索质量，供管理员排障。
- `ai_answer_source`：回答引用过的片段快照，索引更新后仍能解释旧回答。
- `ai_answer_feedback`：用户对回答的赞/踩，按 `(message_id, user_id)` 唯一。

**追踪表刻意不存问题全文与回答正文**，只存前 60 字摘要 + 哈希。运维表被
dump 出去的概率远高于业务表，而这里的内容是用户的健康状况。来源快照表存的是
公共文献片段（不是用户自述），两者边界不能混。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class AiConversation(Base):
    __tablename__ = "ai_conversation"
    __table_args__ = (
        UniqueConstraint("user_id", "conversation_id", name="uk_ai_conversation_user_conversation"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 滚动摘要：消息超过窗口后，老对话压成一段文本，避免每次都把全文塞进 prompt。
    summary: Mapped[str | None] = mapped_column(Text)
    summary_covered_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AiConversationMessage(Base):
    __tablename__ = "ai_conversation_message"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "message_sequence", name="uk_ai_message_conversation_sequence"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigIntPK,
        ForeignKey("ai_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # user / assistant
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiAnswerSource(Base):
    """回答引用的来源快照。

    与 `AiChatTrace.source_count` 互补：trace 只记"引了几条"，这张表记"引了什么"。
    索引更新后，旧回答仍可凭 message_id / trace_id 还原当时的证据，否则事后
    无法解释一个已经过时或已被下架的片段为什么会出现在那条回答里。
    """

    __tablename__ = "ai_answer_source"
    __table_args__ = (
        UniqueConstraint("message_id", "rank", name="uk_ai_answer_source_message_rank"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigIntPK,
        ForeignKey("ai_conversation_message.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_id: Mapped[str | None] = mapped_column(String(128))
    document_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(255))
    heading_path: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(512))
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), default=False
    )
    score: Mapped[float | None] = mapped_column(Float)
    # 片段来自哪份索引。索引更新后能区分"旧回答引用的旧片段"与"现在的检索结果"。
    index_version: Mapped[str | None] = mapped_column(String(64))
    # 片段属于哪个知识域（nutrition / cooking），用于排查串域引用。
    knowledge_domain: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiAnswerFeedback(Base):
    """用户对回答的赞/踩。

    `(message_id, user_id)` 唯一：同一用户再次提交是"改主意"，只更新 rating，
    不新增行。取消赞踩直接删行，这样计数永远等于表里的行数，不用再做去重。
    """

    __tablename__ = "ai_answer_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uk_ai_feedback_message_user"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigIntPK,
        ForeignKey("ai_conversation_message.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AiChatTrace(Base):
    __tablename__ = "ai_chat_trace"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(BigIntPK, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(64))
    question_excerpt: Mapped[str | None] = mapped_column(String(64))
    question_hash: Mapped[str | None] = mapped_column(String(64))
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    # 排序方式（rerank / rrf / none）与降级原因分开：一个是"怎么排的"，
    # 一个是"为什么没按预期排"，混用会让排障时误判。
    rerank_state: Mapped[str | None] = mapped_column(String(64))
    degraded: Mapped[str | None] = mapped_column(String(128))
    knowledge_domain: Mapped[str | None] = mapped_column(String(32))
    total_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
