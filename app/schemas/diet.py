"""饮食记录请求模型。

必填字段全部声明成可空再由 validator 抛中文——Pydantic 对"字段缺失"只会说
"Field required"，而 Java DTO 的 `@NotNull(message=...)` 有中文提示。
前端把 message 直接弹给用户，这里不能漏。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}


class CreateDietRecordRequest(BaseModel):
    foodItemId: int | None = None
    mealType: str | None = None
    quantityG: Decimal | None = None
    recordTime: datetime | None = None
    remark: str | None = None

    @field_validator("foodItemId")
    @classmethod
    def _check_food(cls, value: int | None) -> int | None:
        if value is None:
            raise ValueError("食物ID不能为空")
        return value

    @field_validator("mealType")
    @classmethod
    def _check_meal_type(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("餐别不能为空")
        if value not in _MEAL_TYPES:
            raise ValueError("餐别只能是breakfast、lunch、dinner或snack")
        return value

    @field_validator("quantityG")
    @classmethod
    def _check_quantity(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            raise ValueError("食用重量不能为空")
        if value < Decimal("0.01"):
            raise ValueError("食用重量必须大于0")
        if value > Decimal("99999.99"):
            raise ValueError("食用重量不能超过99999.99克")
        return value

    @field_validator("recordTime")
    @classmethod
    def _check_record_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            raise ValueError("进食时间不能为空")
        return value

    @field_validator("remark")
    @classmethod
    def _check_remark(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError("备注长度不能超过255位")
        return value
