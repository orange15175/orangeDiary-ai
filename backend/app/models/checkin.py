"""每日登录足迹。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class UserDailyCheckin(Base):
    __tablename__ = "user_daily_checkin"
    __table_args__ = (
        UniqueConstraint("user_id", "checkin_date", name="uk_user_daily_checkin_user_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), nullable=False)
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
