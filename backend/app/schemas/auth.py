"""认证请求/响应模型。

校验规则逐条对齐 Java DTO 的注解，错误文案也用同一句中文——前端会把 message
直接弹给用户，两边文案不一致会让人以为是两套系统。
"""
from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, field_validator

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 注册时的密码规则来自 Java `@Size(min = 8, max = 64)`；
# 登录不校验长度，否则老账号在规则变更后会被拦在门外。
_PASSWORD_MIN = 8
_PASSWORD_MAX = 64


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    confirmPassword: str

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("用户名不能为空")
        if len(text) > 50:
            raise ValueError("用户名长度不能超过50位")
        return text

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("邮箱不能为空")
        if len(text) > 255:
            raise ValueError("邮箱长度不能超过255位")
        if not _EMAIL_PATTERN.match(text):
            raise ValueError("邮箱格式不正确")
        return text

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if not value:
            raise ValueError("密码不能为空")
        if not _PASSWORD_MIN <= len(value) <= _PASSWORD_MAX:
            raise ValueError("密码长度必须在8到64位之间")
        return value

    @field_validator("confirmPassword")
    @classmethod
    def _check_confirm(cls, value: str) -> str:
        if not value:
            raise ValueError("确认密码不能为空")
        if len(value) > 64:
            raise ValueError("确认密码长度不能超过64位")
        return value


class LoginRequest(BaseModel):
    # Java 用 @JsonAlias({"email","mobile"}) 兼容老前端，这里用 model_validator 手动补。
    account: str | None = None
    email: str | None = None
    mobile: str | None = None
    password: str

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if not value:
            raise ValueError("密码不能为空")
        return value

    def resolved_account(self) -> str:
        account = (self.account or self.email or self.mobile or "").strip()
        if not account:
            raise ValueError("邮箱或手机号不能为空")
        return account


class RefreshTokenRequest(BaseModel):
    refreshToken: str

    @field_validator("refreshToken")
    @classmethod
    def _check_token(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Refresh Token不能为空")
        return value.strip()


class ProfileView(BaseModel):
    gender: str | None = None
    birthday: str | None = None
    heightCm: Decimal | None = None
    currentWeightKg: Decimal | None = None
    targetWeightKg: Decimal | None = None
    activityLevel: str | None = None


class GoalView(BaseModel):
    goalType: str
    dailyIntakeTarget: Decimal | None = None
    dailyBurnTarget: Decimal | None = None
    startDate: str
    endDate: str | None = None
    status: str


class UserResponse(BaseModel):
    id: int
    mobile: str | None = None
    email: str | None = None
    nickname: str | None = None
    avatar: str | None = None
    status: str
    role: str
    profileCompleted: bool
    profile: ProfileView | None = None
    activeGoal: GoalView | None = None


class LoginResponse(BaseModel):
    token: str
    tokenType: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int
