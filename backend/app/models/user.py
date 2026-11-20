"""用户域三张表：user / user_profile / user_goal。

字段与 Java entity 一一对应，连索引名都保留（`uk_user_email` 等）——M2 做数据
对比时如果约束名不同，`information_schema` 的差分结果就没法直接看。

`BigInteger` 在 SQLite 上不会自增（只有 INTEGER PRIMARY KEY 才行），单测用
`with_variant(Integer, "sqlite")` 兜住；生产仍是 BIGINT。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    mobile: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(64), unique=True)
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'user'"), default="user"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # ON UPDATE CURRENT_TIMESTAMP 由 DDL 负责；ORM 侧也写一份，避免 SQLite 测试里时间不走。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("user.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    gender: Mapped[str | None] = mapped_column(String(10))
    birthday: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    current_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    target_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    activity_level: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserGoal(Base):
    __tablename__ = "user_goal"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    goal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    daily_intake_target: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    daily_burn_target: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
