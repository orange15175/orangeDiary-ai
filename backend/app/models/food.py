"""食物库三张表：food_item / food_alias / food_serving。

`food_item` 是全库共享的（系统内置 + 用户自建），`created_by` 为空表示系统数据。
别名与标准份量是 AI 自然语言记饮食的解析基础：用户说"一个苹果"时，
先按别名命中食物，再按 `food_serving` 把"个"换算成克。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class FoodItem(Base):
    __tablename__ = "food_item"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))
    # g / ml / serving；"份"类食物按 serving 计，换算仍走 food_serving。
    unit: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'g'"), default="g")
    calorie_per_100g: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    protein_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    fat_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    carb_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    brand: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'system'"), default="system"
    )
    created_by: Mapped[int | None] = mapped_column(BigIntPK)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), default="active"
    )
    cover_image_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class FoodAlias(Base):
    __tablename__ = "food_alias"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    food_item_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("food_item.id"), nullable=False, index=True
    )
    # 唯一：一个别名只能指向一种食物，否则"西红柿"会出现两个候选。
    alias_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FoodServing(Base):
    __tablename__ = "food_serving"
    __table_args__ = (UniqueConstraint("food_item_id", "serving_name", name="uk_food_serving_name"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    food_item_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("food_item.id"), nullable=False, index=True
    )
    serving_name: Mapped[str] = mapped_column(String(50), nullable=False)
    gram_weight: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
