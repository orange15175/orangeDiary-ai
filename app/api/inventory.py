"""家中食材与按需核对接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.plan import CheckAnswerRequest, KnownIngredientRequest
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/api/known-ingredients", tags=["家中食材"])
check_router = APIRouter(prefix="/api/ingredient-checks", tags=["食材核对"])


@router.get("", summary="家中食材列表")
async def list_known(
    keyword: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await InventoryService(db).list_known(user.id, keyword))


@router.post("", summary="新增或更新家中食材")
async def upsert_known(
    payload: KnownIngredientRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await InventoryService(db).upsert(user.id, payload.model_dump()))


@router.patch("/{ingredient_id}", summary="更新家中食材")
async def update_known(
    ingredient_id: int,
    payload: KnownIngredientRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    data = payload.model_dump()
    data["id"] = ingredient_id
    return success(await InventoryService(db).upsert(user.id, data))


@router.delete("/{ingredient_id}", summary="归档家中食材")
async def archive_known(
    ingredient_id: int,
    version: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    await InventoryService(db).archive(user.id, ingredient_id, version)
    return success(None)


@check_router.post("", summary="按计划生成食材核对单")
async def create_check(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    plan_id = int(payload.get("planId") or 0)
    version = payload.get("versionNumber")
    return success(
        await InventoryService(db).create_check(
            user.id, plan_id, int(version) if version is not None else None
        )
    )


@check_router.get("/{session_id}", summary="核对单详情")
async def get_check(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await InventoryService(db).get_check(user.id, session_id))


@check_router.post("/{session_id}/answers", summary="提交核对答案")
async def answer_check(
    session_id: int,
    payload: CheckAnswerRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    answers = [item.model_dump() for item in payload.answers]
    return success(
        await InventoryService(db).answer(user.id, session_id, answers, payload.version)
    )
