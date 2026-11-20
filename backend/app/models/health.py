"""每日汇总、体重、运动、幂等记录。

`daily_summary` 是缓存表，不是事实来源：每次饮食/运动/体重写入后由
`HealthSummaryWriter` 按当天明细重算整行。把它当缓存看待，才不会有人去
"补一条汇总"而不补明细。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class DailySummary(Base):
    __tablename__ = "daily_summary"
    __table_args__ = (UniqueConstraint("user_id", "summary_date", name="uk_daily_summary_user_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    summary_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_intake_calorie: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default=text("0.00"), default=Decimal("0")
    )
    total_burn_calorie: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default=text("0.00"), default=Decimal("0")
    )
    net_calorie: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default=text("0.00"), default=Decimal("0")
    )
    diet_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    exercise_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    goal_intake_target: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    goal_burn_target: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class WeightRecord(Base):
    __tablename__ = "weight_record"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False, index=True)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    body_fat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    bmi: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    record_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ExerciseItem(Base):
    __tablename__ = "exercise_item"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))
    calorie_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    # minute / km / time
    unit_type: Mapped[str | None] = mapped_column(String(20))
    met_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ExerciseRecord(Base):
    __tablename__ = "exercise_record"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False, index=True)
    exercise_item_id: Mapped[int] = mapped_column(BigIntPK, nullable=False, index=True)
    exercise_name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_min: Mapped[int | None] = mapped_column(Integer)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    calorie_burned: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    record_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("user_id", "operation", "request_id", name="uk_idempotency_user_operation_request"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    response_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
