"""体重记录请求模型（文案沿用 Java 英文原文，理由见 exercise.py）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator


class CreateWeightRecordRequest(BaseModel):
    weightKg: Decimal | None = None
    bodyFatRate: Decimal | None = None
    recordTime: datetime | None = None
    remark: str | None = None

    @field_validator("weightKg")
    @classmethod
    def _check_weight(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            raise ValueError("Weight is required")
        if value < Decimal("1.00"):
            raise ValueError("Weight must be greater than 0")
        if value > Decimal("500.00"):
            raise ValueError("Weight cannot exceed 500 kg")
        return value

    @field_validator("bodyFatRate")
    @classmethod
    def _check_body_fat(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value < Decimal("0"):
            raise ValueError("Body fat rate cannot be negative")
        if value > Decimal("100"):
            raise ValueError("Body fat rate cannot exceed 100")
        return value

    @field_validator("recordTime")
    @classmethod
    def _check_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            raise ValueError("Record time is required")
        return value

    @field_validator("remark")
    @classmethod
    def _check_remark(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError("Remark cannot exceed 255 characters")
        return value
