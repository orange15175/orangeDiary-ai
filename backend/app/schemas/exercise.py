"""运动记录请求模型。

文案沿用 Java DTO 的英文原文（这批 DTO 当初就是英文的），不做"顺手翻译"：
前端若按文案分支过（例如判断是否要引导设置体重），改语言会静默失效。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class CreateExerciseRecordRequest(BaseModel):
    exerciseItemId: int | None = None
    durationMin: int | None = None
    recordTime: datetime | None = None
    remark: str | None = None

    @field_validator("exerciseItemId")
    @classmethod
    def _check_item(cls, value: int | None) -> int | None:
        if value is None:
            raise ValueError("Exercise item ID is required")
        return value

    @field_validator("durationMin")
    @classmethod
    def _check_duration(cls, value: int | None) -> int | None:
        if value is None:
            raise ValueError("Exercise duration is required")
        if value <= 0:
            raise ValueError("Exercise duration must be greater than 0")
        if value > 1440:
            raise ValueError("Exercise duration cannot exceed 1440 minutes")
        return value

    @field_validator("recordTime")
    @classmethod
    def _check_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            raise ValueError("Exercise time is required")
        return value

    @field_validator("remark")
    @classmethod
    def _check_remark(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError("Remark cannot exceed 255 characters")
        return value
