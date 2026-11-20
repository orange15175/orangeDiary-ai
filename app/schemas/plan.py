"""周计划、偏好与食材核对的请求模型。"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class PlanRequest(BaseModel):
    startDate: date
    days: int = Field(default=1, ge=1, le=7)
    mealTypes: list[str] = Field(default_factory=lambda: ["breakfast", "lunch", "dinner"])
    ingredientMode: str | None = None
    allergens: list[str] | None = None
    equipment: list[str] | None = None
    maxPrepMinutes: int | None = Field(default=None, ge=1, le=600)

    @field_validator("mealTypes")
    @classmethod
    def _check_meals(cls, value: list[str]) -> list[str]:
        allowed = {"breakfast", "lunch", "dinner"}
        cleaned = [item for item in value if item in allowed]
        if not cleaned:
            raise ValueError("至少选择一个餐次")
        return cleaned

    @field_validator("ingredientMode")
    @classmethod
    def _check_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"prefer_available", "allow_purchase", "available_only"}:
            raise ValueError("食材模式不合法")
        return value


class AdoptRequest(BaseModel):
    versionNumber: int = Field(ge=1)


class ExecutionRequest(BaseModel):
    state: str

    @field_validator("state")
    @classmethod
    def _check(cls, value: str) -> str:
        if value not in {"planned", "executed", "skipped", "unknown"}:
            raise ValueError("执行状态不合法")
        return value


class PreferenceRequest(BaseModel):
    allergens: list[str] | None = None
    dislikes: list[str] | None = None
    cuisinePreferences: list[str] | None = None
    equipment: list[str] | None = None
    prepTimeLimit: int | None = Field(default=None, ge=1, le=600)
    budgetMode: str | None = None
    budgetValue: float | None = Field(default=None, ge=0)
    city: str | None = None
    ingredientMode: str | None = None
    version: int | None = None


class KnownIngredientRequest(BaseModel):
    id: int | None = None
    label: str = Field(min_length=1, max_length=120)
    foodItemId: int | None = None
    presence: str | None = None
    quantityState: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None
    foodState: str | None = None
    storageLocation: str | None = None
    source: str | None = None
    version: int | None = None


class CheckAnswer(BaseModel):
    itemId: int
    answer: str
    confirmedQuantity: float | None = Field(default=None, ge=0)


class CheckAnswerRequest(BaseModel):
    version: int | None = None
    answers: list[CheckAnswer] = Field(default_factory=list)
