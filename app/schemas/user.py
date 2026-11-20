"""用户档案与目标请求模型。

校验文案逐字对齐 Java DTO 的注解——前端把 `message` 直接弹给用户，
"身高必须大于0" 和 "身高不能小于1" 在用户眼里是两套系统。

一个容易踩的语义差异在这里被保留：`PUT /api/users/profile` 对 `user` 表是
**部分更新**（MyBatis-Plus 忽略 null），对 `user_profile` 表是**整体覆盖**
（Java 无条件 set 六个字段）。所以只改性别时昵称不会丢，但身高不传就会被清空。
看似不一致，但这是存量行为，改动会静默影响老用户数据。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_validator

# 用 str + 手动校验而不是 Literal：Literal 校验失败时 Pydantic 抛的是英文
# "Input should be 'male' or 'female'"，和 Java 的中文提示对不上。
_GENDERS = {"male", "female"}
_ACTIVITY_LEVELS = {"low", "medium", "high"}
_GOAL_TYPES = {"lose", "maintain", "gain"}


class UpdateUserProfileRequest(BaseModel):
    nickname: str | None = None
    avatar: str | None = None
    gender: str | None = None
    birthday: date | None = None
    heightCm: Decimal | None = None
    currentWeightKg: Decimal | None = None
    targetWeightKg: Decimal | None = None
    activityLevel: str | None = None

    @field_validator("nickname")
    @classmethod
    def _check_nickname(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 50:
            raise ValueError("昵称长度不能超过50位")
        return value

    @field_validator("avatar")
    @classmethod
    def _check_avatar(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError("头像地址长度不能超过255位")
        return value

    @field_validator("gender")
    @classmethod
    def _check_gender(cls, value: str | None) -> str | None:
        if value is not None and value not in _GENDERS:
            raise ValueError("性别只能是male或female")
        return value

    @field_validator("activityLevel")
    @classmethod
    def _check_activity_level(cls, value: str | None) -> str | None:
        if value is not None and value not in _ACTIVITY_LEVELS:
            raise ValueError("活动水平只能是low、medium或high")
        return value

    @field_validator("heightCm")
    @classmethod
    def _check_height(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value < Decimal("1.00"):
            raise ValueError("身高必须大于0")
        if value > Decimal("300.00"):
            raise ValueError("身高不能超过300cm")
        return value

    @field_validator("currentWeightKg")
    @classmethod
    def _check_current_weight(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < Decimal("1.00"):
            raise ValueError("当前体重必须大于0")
        return value

    @field_validator("targetWeightKg")
    @classmethod
    def _check_target_weight(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < Decimal("1.00"):
            raise ValueError("目标体重必须大于0")
        return value


class CreateUserGoalRequest(BaseModel):
    goalType: str
    dailyIntakeTarget: Decimal | None = None
    dailyBurnTarget: Decimal | None = None
    startDate: date
    endDate: date | None = None

    @field_validator("goalType")
    @classmethod
    def _check_goal_type(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("目标类型不能为空")
        if text not in _GOAL_TYPES:
            raise ValueError("目标类型只能是lose、maintain或gain")
        return text

    @field_validator("dailyIntakeTarget")
    @classmethod
    def _check_intake(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < Decimal("0"):
            raise ValueError("每日摄入目标不能小于0")
        return value

    @field_validator("dailyBurnTarget")
    @classmethod
    def _check_burn(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < Decimal("0"):
            raise ValueError("每日消耗目标不能小于0")
        return value
