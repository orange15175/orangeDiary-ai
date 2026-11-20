"""食谱查询与私人厨师接口。

- `GET /api/recipes`：按名称/食材/分类检索样例或正式食谱，返回带营养覆盖率的摘要。
- `GET /api/recipes/{id}`：完整食材、步骤与营养明细。
- `POST /api/assistant/chef`：私人厨师，文字输入 → 候选菜 + 做法 + 引用来源。
  未连接模型时返回库中做法原文并标 `degraded`，不伪装成模型回答。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.schemas.recipe import ChefRequest
from app.services.chef_service import ChefService
from app.services.recipe_service import RecipeService

router = APIRouter(prefix="/api/recipes", tags=["食谱"])
assistant_router = APIRouter(prefix="/api/assistant", tags=["私人厨师"])


@router.get("", summary="检索食谱")
async def list_recipes(
    keyword: str | None = Query(default=None, description="菜名或食材关键词"),
    category: str | None = Query(default=None, description="分类"),
    sourceType: str | None = Query(default=None, description="sample / formal"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(
        await RecipeService(db).list_recipes(
            keyword=keyword,
            category=category,
            source_type=sourceType,
            page=page,
            page_size=pageSize,
        )
    )


@router.get("/{recipe_id}", summary="食谱详情")
async def get_recipe(
    recipe_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    detail = await RecipeService(db).get_recipe(recipe_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="食谱不存在")
    return success(detail)


@assistant_router.post("/chef", summary="私人厨师：菜谱推荐与烹饪指导")
async def chef(
    payload: ChefRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    result = await ChefService(db, settings, user.id).respond(
        payload.message,
        servings=payload.servings,
        allergens=payload.allergens,
    )
    return success(result)
