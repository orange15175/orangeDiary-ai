"""食谱与私人厨师的请求模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChefRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, description="用户想做的菜或食材描述")
    servings: int | None = Field(default=None, ge=1, le=12, description="目标人份，可选")
    allergens: list[str] | None = Field(default=None, description="必须避开的过敏原，可选")

    @field_validator("message")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("需求不能为空")
        return text

    @field_validator("allergens")
    @classmethod
    def _clean_allergens(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item and item.strip()]
        return cleaned or None
