"""运动库与运动记录接口（`/api/exercises`、`/api/exercise-records`）。

新增记录走幂等（操作名 `EXERCISE_CREATE`，与 Java 枚举同值）；预览不写库、
不需要 requestId。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.exercise import CreateExerciseRecordRequest
from app.services import idempotency
from app.services.exercise_service import ExerciseService

items_router = APIRouter(prefix="/api/exercises", tags=["运动管理"])
records_router = APIRouter(prefix="/api/exercise-records", tags=["运动记录"])

EXERCISE_CREATE = "EXERCISE_CREATE"


def get_exercise_service(db: AsyncSession = Depends(get_session)) -> ExerciseService:
    return ExerciseService(db)


@items_router.get("", summary="List active exercise items")
async def list_exercises(
    keyword: str | None = Query(default=None),
    limit: int = Query(default=10),
    _user: User = Depends(get_current_user),
    service: ExerciseService = Depends(get_exercise_service),
):
    if keyword is None or not keyword.strip():
        return success(await service.list_items())
    return success(await service.search_items(keyword, limit))


@items_router.get("/categories", summary="List exercise browse categories")
async def list_exercise_categories(
    _user: User = Depends(get_current_user),
    service: ExerciseService = Depends(get_exercise_service),
):
    return success(service.list_categories())


@items_router.get("/browse", summary="Browse exercise items by category or keyword")
async def browse_exercises(
    category: str = Query(default="frequent"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1),
    pageSize: int = Query(default=20),
    user: User = Depends(get_current_user),
    service: ExerciseService = Depends(get_exercise_service),
):
    return success(
        await service.browse(user.id, category, keyword, page, pageSize)
    )


@records_router.post("", summary="Create exercise record")
async def create_exercise_record(
    payload: CreateExerciseRecordRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: ExerciseService = Depends(get_exercise_service),
):
    result = await idempotency.execute(
        db,
        user_id=user.id,
        operation=EXERCISE_CREATE,
        request_id=x_request_id,
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create_record(user.id, payload),
    )
    return success(result)


@records_router.post("/preview", summary="Preview exercise calorie without saving")
async def preview_exercise_record(
    payload: CreateExerciseRecordRequest,
    user: User = Depends(get_current_user),
    service: ExerciseService = Depends(get_exercise_service),
):
    return success(await service.preview_record(user.id, payload))


@records_router.get("", summary="List exercise records for one day")
async def list_exercise_records(
    date: date = Query(...),
    user: User = Depends(get_current_user),
    service: ExerciseService = Depends(get_exercise_service),
):
    return success(await service.list_records(user.id, date))


@records_router.delete("/{record_id}", summary="Delete exercise record")
async def delete_exercise_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: ExerciseService = Depends(get_exercise_service),
):
    await service.delete_record(user.id, record_id)
    await db.commit()
    return success(None)
