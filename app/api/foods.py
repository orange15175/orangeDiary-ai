"""食物库接口，路径与 `FoodItemController` 一致。

四个接口都要求登录：Java 拦截器覆盖 `/api/**`，只有 `/api/auth/**` 例外。
食物库是全站共享数据，但"常吃"排序依赖当前用户，匿名访问没有任何意义。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services.food_service import FoodService

router = APIRouter(prefix="/api/foods", tags=["食物管理"])


def get_food_service(db: AsyncSession = Depends(get_session)) -> FoodService:
    return FoodService(db)


@router.get("", summary="查询食物列表")
async def list_foods(
    keyword: str | None = Query(default=None),
    limit: int = Query(default=10),
    _user: User = Depends(get_current_user),
    service: FoodService = Depends(get_food_service),
):
    if keyword is None or not keyword.strip():
        return success(await service.list_foods())
    return success(await service.search_foods(keyword, limit))


@router.get("/categories", summary="查询食物浏览分类")
async def list_browse_categories(
    _user: User = Depends(get_current_user),
    service: FoodService = Depends(get_food_service),
):
    return success(service.list_browse_categories())


@router.get("/browse", summary="按分类或关键词分页浏览食物")
async def browse_foods(
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1),
    pageSize: int = Query(default=20),
    user: User = Depends(get_current_user),
    service: FoodService = Depends(get_food_service),
):
    return success(await service.browse_foods(user.id, category, keyword, page, pageSize))


@router.get("/match", summary="匹配 AI 解析出的食物")
async def match_food(
    name: str = Query(...),
    _user: User = Depends(get_current_user),
    service: FoodService = Depends(get_food_service),
):
    return success(await service.match_food(name))
