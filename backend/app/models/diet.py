"""饮食记录表。

`food_name_snapshot` / `calorie_snapshot` 这一组快照字段是刻意冗余的：
食物库的营养值后续被修正时，历史记录不能跟着变，否则用户昨天的热量统计
今天会自己改数字。写入时一次性算好，读的时候不再 join food_item。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class DietRecord(Base):
    __tablename__ = "diet_record"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("user.id"), nullable=False, index=True
    )
    food_item_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("food_item.id"), nullable=False, index=True
    )
    # breakfast / lunch / dinner / snack
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    food_name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity_g: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    calorie_snapshot: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    protein_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    fat_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    carb_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    record_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 归属日期与 record_time 分离：23:50 吃的夜宵仍然算当天，不能靠 record_time 推。
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
