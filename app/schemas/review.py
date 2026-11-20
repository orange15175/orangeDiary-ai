"""实际饮食观察、计划调整与复盘请求模型。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class ObservationRequest(BaseModel):
    occurredAt: datetime
    description: str = Field(min_length=1, max_length=2000)
    source: str = "manual"
    completeness: str = "partial"
    estimatedNutrition: dict | None = None

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        allowed = {"manual", "image", "text", "voice", "takeout"}
        if value not in allowed:
            raise ValueError("记录来源不合法")
        return value

    @field_validator("completeness")
    @classmethod
    def _check_completeness(cls, value: str) -> str:
        if value not in {"partial", "complete", "unknown"}:
            raise ValueError("完整度不合法")
        return value


class ResolveRequest(BaseModel):
    dietRecordIds: list[int] = Field(default_factory=list)


class AdjustmentRequest(BaseModel):
    note: str = Field(min_length=1, max_length=200)
    event: str | None = None
    maxPrepMinutes: int | None = Field(default=None, ge=1, le=600)
    excludedFoods: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    periodStart: date | None = None
    periodEnd: date | None = None
    withDraftPlan: bool = False


class DayStatusRequest(BaseModel):
    completeness: str
    note: str | None = Field(default=None, max_length=255)

    @field_validator("completeness")
    @classmethod
    def _check_completeness(cls, value: str) -> str:
        if value not in {"unknown", "partial", "complete"}:
            raise ValueError("完整度不合法")
        return value
