"""采购相关的请求模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BuildListRequest(BaseModel):
    planId: int
    versionNumber: int | None = None


class PurchaseRequest(BaseModel):
    actualQuantity: float | None = Field(default=None, ge=0)
    paidPrice: float | None = Field(default=None, ge=0)
    addToIngredients: bool = True


class ReversalRequest(BaseModel):
    reason: str = Field(default="", max_length=200)
