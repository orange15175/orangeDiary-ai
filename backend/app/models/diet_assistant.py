"""R3 智能饮食闭环的数据模型。

按设计文档 §13.2 建表，覆盖食谱、计划、食材、采购、调整、复盘。
几条贯穿全模块的约定，先写在这里：

1. **null = 未知，0 = 确认零**。营养缺失、数量未知一律存 null，不用 0 顶替；
   展示层据此给覆盖率与"待核对"，不把未知算成没有。
2. **版本化**。计划、预览、采购清单都带 `version`，采纳产生新版本而不是原地改，
   并发提交靠 `version` 做乐观锁。
3. **草稿不扣库存**。只有采纳后才建立 `ingredient_reservation`；已购、已食用
   是事实，后续计划不能覆盖。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import BigIntPK


class DietPreference(Base):
    __tablename__ = "diet_preference"
    __table_args__ = (UniqueConstraint("user_id", name="uk_diet_preference_user"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"))
    # 过敏、忌口、菜系偏好、设备用逗号分隔的文本保存；首版不引入多表。
    allergens: Mapped[str | None] = mapped_column(Text)
    dislikes: Mapped[str | None] = mapped_column(Text)
    cuisine_preferences: Mapped[str | None] = mapped_column(Text)
    equipment: Mapped[str | None] = mapped_column(Text)
    prep_time_limit: Mapped[int | None] = mapped_column(Integer)
    budget_mode: Mapped[str] = mapped_column(String(16), default="soft", server_default=text("'soft'"))
    budget_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    city: Mapped[str | None] = mapped_column(String(64))
    ingredient_mode: Mapped[str] = mapped_column(
        String(24), default="prefer_available", server_default=text("'prefer_available'")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Recipe(Base):
    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(BigIntPK)
    # system / user；sample 只用于内部联调，formal 才是验收数据。
    scope: Mapped[str] = mapped_column(String(16), default="system", server_default=text("'system'"))
    source_type: Mapped[str] = mapped_column(String(16), default="sample", server_default=text("'sample'"))
    source_url: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str | None] = mapped_column(String(120))
    servings: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    prep_minutes: Mapped[int | None] = mapped_column(Integer)
    cook_minutes: Mapped[int | None] = mapped_column(Integer)
    equipment: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default=text("'active'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredient"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("recipe.id"), index=True)
    food_item_id: Mapped[int | None] = mapped_column(BigIntPK)
    original_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # 数量或单位未知时保持 null；gram_equivalent 是"整条食材的总克数"，用于无单位时换算。
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))
    gram_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    basis: Mapped[str] = mapped_column(String(16), default="raw", server_default=text("'raw'"))
    optional: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    allergen_status: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class RecipeStep(Base):
    __tablename__ = "recipe_step"
    __table_args__ = (UniqueConstraint("recipe_id", "step_index", name="uk_recipe_step_index"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("recipe.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    duration_hint: Mapped[str | None] = mapped_column(String(64))
    equipment: Mapped[str | None] = mapped_column(String(120))


class RecipeNutrition(Base):
    __tablename__ = "recipe_nutrition"
    __table_args__ = (UniqueConstraint("recipe_id", "recipe_version", name="uk_recipe_nutrition_version"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("recipe.id"), index=True)
    recipe_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    nutrients: Mapped[dict | None] = mapped_column(JSON)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    calculation_version: Mapped[str] = mapped_column(
        String(32), default="v1", server_default=text("'v1'")
    )
    input_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnownIngredient(Base):
    __tablename__ = "known_ingredient"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    food_item_id: Mapped[int | None] = mapped_column(BigIntPK)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # present / absent / unknown；unknown 不等于 absent。
    presence: Mapped[str] = mapped_column(String(16), default="unknown", server_default=text("'unknown'"))
    # known / approximate / unknown
    quantity_state: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'")
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))
    food_state: Mapped[str | None] = mapped_column(String(16))
    storage_location: Mapped[str | None] = mapped_column(String(64))
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(24), default="manual", server_default=text("'manual'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IngredientLedger(Base):
    __tablename__ = "ingredient_ledger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uk_ingredient_ledger_idem"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"))
    ingredient_id: Mapped[int] = mapped_column(BigIntPK, index=True)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    delta: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    before_qty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    after_qty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))
    cause_type: Mapped[str | None] = mapped_column(String(24))
    cause_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IngredientReservation(Base):
    __tablename__ = "ingredient_reservation"
    __table_args__ = (
        UniqueConstraint("plan_meal_id", "ingredient_id", name="uk_reservation_meal_ingredient"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"))
    ingredient_id: Mapped[int] = mapped_column(BigIntPK, index=True)
    plan_meal_id: Mapped[int] = mapped_column(BigIntPK)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default=text("'active'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IngredientCheckSession(Base):
    __tablename__ = "ingredient_check_session"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    plan_version_ref: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="open", server_default=text("'open'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IngredientCheckItem(Base):
    __tablename__ = "ingredient_check_item"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("ingredient_check_session.id"), index=True
    )
    food_item_id: Mapped[int | None] = mapped_column(BigIntPK)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    needed_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))
    existing_assessment: Mapped[str | None] = mapped_column(String(24))
    answer: Mapped[str | None] = mapped_column(String(24))
    confirmed_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(
        String(24), default="needs_check", server_default=text("'needs_check'")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class MealPlan(Base):
    __tablename__ = "meal_plan"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    active_version_id: Mapped[int | None] = mapped_column(BigIntPK)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class MealPlanVersion(Base):
    __tablename__ = "meal_plan_version"
    __table_args__ = (UniqueConstraint("plan_id", "version_number", name="uk_meal_plan_version"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("meal_plan.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(BigIntPK)
    input_snapshot: Mapped[dict | None] = mapped_column(JSON)
    constraints: Mapped[dict | None] = mapped_column(JSON)
    checks: Mapped[dict | None] = mapped_column(JSON)
    nutrition_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    price_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanMeal(Base):
    __tablename__ = "plan_meal"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    plan_version_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("meal_plan_version.id"), index=True
    )
    meal_date: Mapped[date] = mapped_column(Date, nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    recipe_id: Mapped[int | None] = mapped_column(BigIntPK)
    recipe_version: Mapped[int | None] = mapped_column(Integer)
    servings: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    nutrition_snapshot: Mapped[dict | None] = mapped_column(JSON)
    cost_range: Mapped[dict | None] = mapped_column(JSON)
    execution_state: Mapped[str] = mapped_column(
        String(16), default="planned", server_default=text("'planned'")
    )
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class ShoppingList(Base):
    __tablename__ = "shopping_list"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    plan_version_refs: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"))
    totals: Mapped[dict | None] = mapped_column(JSON)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ShoppingItem(Base):
    __tablename__ = "shopping_item"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("shopping_list.id"), index=True)
    food_item_id: Mapped[int | None] = mapped_column(BigIntPK)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    spec: Mapped[str | None] = mapped_column(String(120))
    need_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    available_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    shortage_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    quantity_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'")
    )
    buy_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(
        String(16), default="needs_check", server_default=text("'needs_check'")
    )
    price_snapshot: Mapped[dict | None] = mapped_column(JSON)
    ledger_id: Mapped[int | None] = mapped_column(BigIntPK)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ShoppingItemSource(Base):
    __tablename__ = "shopping_item_source"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("shopping_item.id"), index=True)
    plan_meal_id: Mapped[int | None] = mapped_column(BigIntPK)
    recipe_ingredient_id: Mapped[int | None] = mapped_column(BigIntPK)
    required_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(16))


class PriceObservation(Base):
    __tablename__ = "price_observation"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    food_item_id: Mapped[int | None] = mapped_column(BigIntPK)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    spec: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(64))
    unit_price_low: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit_price_high: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY", server_default=text("'CNY'"))
    price_type: Mapped[str] = mapped_column(
        String(16), default="regular", server_default=text("'regular'")
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    queried_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source_url: Mapped[str | None] = mapped_column(String(512))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[str | None] = mapped_column(String(255))


class MealObservation(Base):
    __tablename__ = "meal_observation"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="manual", server_default=text("'manual'"))
    estimated_nutrition: Mapped[dict | None] = mapped_column(JSON)
    completeness: Mapped[str] = mapped_column(
        String(16), default="partial", server_default=text("'partial'")
    )
    confidence_kind: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ObservationRecordLink(Base):
    __tablename__ = "observation_record_link"
    __table_args__ = (
        UniqueConstraint("observation_id", "diet_record_id", name="uk_observation_link"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("meal_observation.id"), index=True
    )
    diet_record_id: Mapped[int] = mapped_column(BigIntPK, index=True)


class DietDayStatus(Base):
    """用户对"当天记录是否记全"的自述（设计 §11、§13.2）。

    复盘算日均值时要能说清分母是"已记录天"还是"完整记录天"；系统无法从
    记录条数推断用户是否漏记，所以这里只存用户表态，不存推断结果。
    """

    __tablename__ = "diet_day_status"
    __table_args__ = (UniqueConstraint("user_id", "status_date", name="uk_diet_day_status_user_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    status_date: Mapped[date] = mapped_column(Date, nullable=False)
    completeness: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default=text("'unknown'")
    )
    note: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ReviewReport(Base):
    __tablename__ = "review_report"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict | None] = mapped_column(JSON)
    data_gaps: Mapped[list | None] = mapped_column(JSON)
    suggestions: Mapped[list | None] = mapped_column(JSON)
    draft_plan_ref: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rule_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("user.id"), index=True)
    plan_meal_id: Mapped[int | None] = mapped_column(BigIntPK)
    recipe_id: Mapped[int | None] = mapped_column(BigIntPK)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    hunger: Mapped[str | None] = mapped_column(String(16))
    satiety: Mapped[str | None] = mapped_column(String(16))
    difficulty: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
