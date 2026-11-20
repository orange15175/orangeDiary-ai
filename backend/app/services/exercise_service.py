"""运动库与运动记录（对齐 `ExerciseItemServiceImpl` + `ExerciseRecordServiceImpl`）。

热量算法只有一条路径：`calorie_per_unit` 优先，否则用 MET × 体重 × 时长 / 60。
预览与落库共用 `_burned_calorie`，否则"预览 300 卡、保存 287 卡"这种漂移
用户一眼就能看出来。

与 Java 的一处有意修正：`countFrequentItems` 在 Java 里按"用户用过的不同项目数"
统计，没有过滤已停用项目，而列表 SQL 过滤了 `status='active'`。结果是一个停用项目
会让 `total` 比实际多 1，前端"还有更多"永远为真。这里按 active 统计，与列表一致。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.health import ExerciseItem, ExerciseRecord
from app.models.user import UserProfile
from app.schemas.exercise import CreateExerciseRecordRequest
from app.services import health_summary
from app.services.profile_gate import require_completed_profile

_SIXTY = Decimal("60")
_CENT = Decimal("0.01")
_PAGE_MAX = 50

CATEGORY_CODES: list[tuple[str, str]] = [
    ("frequent", "常练"),
    ("custom", "自定义"),
    ("favorite", "收藏"),
    ("aerobic", "有氧"),
    ("strength", "力量"),
    ("ball", "球类"),
    ("flexibility", "柔韧"),
    ("other", "其他"),
]

_STANDARD_CATEGORIES = {
    "aerobic": "有氧",
    "strength": "力量",
    "ball": "球类",
    "flexibility": "柔韧",
}


def item_row(item: ExerciseItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "caloriePerUnit": item.calorie_per_unit,
        "unitType": item.unit_type,
        "metValue": item.met_value,
        "status": item.status,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


def _browse_item(item: ExerciseItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "metValue": item.met_value,
        "caloriePerUnit": item.calorie_per_unit,
        "unitType": item.unit_type,
    }


def record_row(record: ExerciseRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "userId": record.user_id,
        "exerciseItemId": record.exercise_item_id,
        "exerciseNameSnapshot": record.exercise_name_snapshot,
        "durationMin": record.duration_min,
        "distanceKm": record.distance_km,
        "calorieBurned": record.calorie_burned,
        "recordTime": record.record_time.isoformat() if record.record_time else None,
        "recordDate": record.record_date.isoformat() if record.record_date else None,
        "remark": record.remark,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
        "updatedAt": record.updated_at.isoformat() if record.updated_at else None,
    }


class ExerciseService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------- 运动库 ----------------

    async def list_items(self) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(ExerciseItem).where(ExerciseItem.status == "active").order_by(ExerciseItem.id.asc())
            )
        ).all()
        return [item_row(item) for item in rows]

    async def search_items(self, keyword: str | None, limit: int = 10) -> list[dict[str, Any]]:
        text = (keyword or "").strip()
        if not text:
            return []
        rows = (
            await self._db.scalars(
                select(ExerciseItem)
                .where(ExerciseItem.status == "active", ExerciseItem.name.like(f"%{text}%"))
                .order_by(ExerciseItem.id.asc())
                .limit(min(max(limit, 1), 10))
            )
        ).all()
        return [item_row(item) for item in rows]

    def list_categories(self) -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in CATEGORY_CODES]

    async def browse(
        self,
        user_id: int,
        category: str = "frequent",
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), _PAGE_MAX)
        offset = (safe_page - 1) * safe_page_size
        safe_category = category if category and category.strip() else "frequent"
        needle = (keyword or "").strip()

        if not needle and safe_category == "frequent":
            total = await self._count_frequent(user_id)
            if total == 0:
                return _page([], 0, safe_page, safe_page_size)
            items = await self._select_frequent(user_id, offset, safe_page_size)
            return _page([_browse_item(item) for item in items], total, safe_page, safe_page_size)

        if not needle and safe_category in {"custom", "favorite"}:
            return _page([], 0, safe_page, safe_page_size)

        query = select(ExerciseItem).where(ExerciseItem.status == "active").order_by(ExerciseItem.id.asc())
        if needle:
            query = query.where(ExerciseItem.name.like(f"%{needle}%"))
        elif safe_category in _STANDARD_CATEGORIES:
            query = query.where(ExerciseItem.category == _STANDARD_CATEGORIES[safe_category])
        elif safe_category == "other":
            # 未分类也算"其他"；notIn 在 SQL 里对 NULL 不成立，必须显式带 is NULL。
            query = query.where(
                (ExerciseItem.category.is_(None))
                | ExerciseItem.category.not_in(list(_STANDARD_CATEGORIES.values()))
            )
        else:
            return _page([], 0, safe_page, safe_page_size)

        total = await self._db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (await self._db.scalars(query.offset(offset).limit(safe_page_size))).all()
        return _page([_browse_item(item) for item in rows], int(total), safe_page, safe_page_size)

    async def _count_frequent(self, user_id: int) -> int:
        # 与列表 SQL 同口径：只统计 active 项目，见模块文档。
        count = await self._db.scalar(
            select(func.count(func.distinct(ExerciseRecord.exercise_item_id)))
            .select_from(ExerciseRecord)
            .join(ExerciseItem, ExerciseItem.id == ExerciseRecord.exercise_item_id)
            .where(ExerciseRecord.user_id == user_id, ExerciseItem.status == "active")
        )
        return int(count or 0)

    async def _select_frequent(self, user_id: int, offset: int, limit: int) -> list[ExerciseItem]:
        usage = (
            select(
                ExerciseRecord.exercise_item_id.label("item_id"),
                func.count().label("use_count"),
                func.max(ExerciseRecord.record_time).label("last_used_at"),
            )
            .where(ExerciseRecord.user_id == user_id)
            .group_by(ExerciseRecord.exercise_item_id)
            .subquery()
        )
        return list(
            (
                await self._db.scalars(
                    select(ExerciseItem)
                    .join(usage, usage.c.item_id == ExerciseItem.id)
                    .where(ExerciseItem.status == "active")
                    .order_by(usage.c.use_count.desc(), usage.c.last_used_at.desc(), ExerciseItem.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )

    # ---------------- 运动记录 ----------------

    async def create_record(
        self, user_id: int, payload: CreateExerciseRecordRequest
    ) -> dict[str, Any]:
        await health_summary.lock_user(self._db, user_id)
        await require_completed_profile(self._db, user_id)
        item = await self._active_item(payload.exerciseItemId)
        burned = await self._burned_calorie(user_id, item, payload.durationMin)

        record = ExerciseRecord(
            user_id=user_id,
            exercise_item_id=item.id,
            exercise_name_snapshot=item.name,
            duration_min=payload.durationMin,
            calorie_burned=burned,
            record_time=payload.recordTime,
            record_date=payload.recordTime.date(),
            remark=payload.remark,
        )
        self._db.add(record)
        await self._db.flush()
        await self._db.refresh(record)
        await health_summary.refresh(self._db, user_id, record.record_date)
        return record_row(record)

    async def preview_record(
        self, user_id: int, payload: CreateExerciseRecordRequest
    ) -> dict[str, Any]:
        # 预览不写库，但档案与项目校验和落库完全一致——否则预览能过、保存报错。
        await require_completed_profile(self._db, user_id)
        item = await self._active_item(payload.exerciseItemId)
        burned = await self._burned_calorie(user_id, item, payload.durationMin)
        return {
            "exerciseItemId": item.id,
            "exerciseName": item.name,
            "durationMin": payload.durationMin,
            "recordTime": payload.recordTime.isoformat(),
            "estimatedCalorieBurned": burned,
        }

    async def list_records(self, user_id: int, day: date) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(ExerciseRecord)
                .where(ExerciseRecord.user_id == user_id, ExerciseRecord.record_date == day)
                .order_by(ExerciseRecord.record_time.desc(), ExerciseRecord.id.desc())
            )
        ).all()
        return [record_row(record) for record in rows]

    async def delete_record(self, user_id: int, record_id: int) -> None:
        await health_summary.lock_user(self._db, user_id)
        record = await self._db.scalar(
            select(ExerciseRecord).where(
                ExerciseRecord.id == record_id, ExerciseRecord.user_id == user_id
            )
        )
        if record is None:
            raise BusinessError(404, "Exercise record not found")
        day = record.record_date
        await self._db.delete(record)
        await self._db.flush()
        await health_summary.refresh(self._db, user_id, day)

    # ---------------- 内部 ----------------

    async def _active_item(self, item_id: int | None) -> ExerciseItem:
        item = await self._db.get(ExerciseItem, item_id) if item_id else None
        if item is None or item.status != "active":
            raise BusinessError(404, "Exercise item does not exist or is unavailable")
        return item

    async def _burned_calorie(
        self, user_id: int, item: ExerciseItem, duration_min: int | None
    ) -> Decimal:
        minutes = Decimal(duration_min or 0)
        if item.calorie_per_unit is not None:
            return (item.calorie_per_unit * minutes).quantize(_CENT, rounding=ROUND_HALF_UP)
        if item.met_value is None:
            raise BusinessError(400, "Exercise calorie rule is not configured")
        profile = await self._db.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        if profile is None or profile.current_weight_kg is None:
            # 实际不可达：`require_completed_profile` 已要求当前体重非空。
            # 保留是为了防止将来放宽档案闸门时静默算出 0 卡消耗。
            raise BusinessError(
                400, "Please set your current weight before adding exercise records"
            )
        return (
            item.met_value * profile.current_weight_kg * minutes / _SIXTY
        ).quantize(_CENT, rounding=ROUND_HALF_UP)


def _page(items: list[dict[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        # Java 用 page * pageSize < total；页数超界时它也会返回 false，与这里一致。
        "hasMore": page * page_size < total,
    }
