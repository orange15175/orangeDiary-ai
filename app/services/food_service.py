"""食物库查询：列表、搜索、分类浏览、AI 名称匹配。

与 `FoodItemServiceImpl` 的语义完全对齐，只有一处实现方式不同：
Java 为了算"全站使用次数"把整张 `diet_record` 拉进内存再分组，Python 用
`GROUP BY` 在库里算。结果相同，但数据量上来后不会再因为一次浏览拖慢整库。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diet import DietRecord
from app.models.food import FoodAlias, FoodItem, FoodServing
from app.services.food_category import CATEGORY_CODES, normal_category_for_code, resolve

_ACTIVE = "active"
_SEARCH_LIMIT_MAX = 10
_PAGE_SIZE_MAX = 50


def food_row(food: FoodItem) -> dict[str, Any]:
    """`GET /api/foods` 直接返回实体，字段名与 Jackson 序列化结果一致。"""
    return {
        "id": food.id,
        "name": food.name,
        "category": food.category,
        "unit": food.unit,
        "caloriePer100g": food.calorie_per_100g,
        "proteinPer100g": food.protein_per_100g,
        "fatPer100g": food.fat_per_100g,
        "carbPer100g": food.carb_per_100g,
        "brand": food.brand,
        "sourceType": food.source_type,
        "createdBy": food.created_by,
        "status": food.status,
        "coverImageUrl": food.cover_image_url,
        "createdAt": _iso(food.created_at),
        "updatedAt": _iso(food.updated_at),
    }


def browse_item(food: FoodItem) -> dict[str, Any]:
    return {
        "id": food.id,
        "name": food.name,
        "category": resolve(food.category),
        "brand": food.brand,
        "unit": food.unit,
        "caloriePer100g": food.calorie_per_100g,
        "proteinPer100g": food.protein_per_100g,
        "fatPer100g": food.fat_per_100g,
        "carbPer100g": food.carb_per_100g,
        "coverImageUrl": food.cover_image_url,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class FoodService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_foods(self) -> list[dict[str, Any]]:
        # 与 Java 一致：不带关键词时返回全量，按 id 倒序，不分页。
        rows = (await self._db.scalars(select(FoodItem).order_by(FoodItem.id.desc()))).all()
        return [food_row(food) for food in rows]

    async def search_foods(self, keyword: str | None, limit: int = 10) -> list[dict[str, Any]]:
        text = (keyword or "").strip()
        if not text:
            return []
        safe_limit = min(max(limit, 1), _SEARCH_LIMIT_MAX)
        rows = (
            await self._db.scalars(
                select(FoodItem)
                .where(FoodItem.name.like(f"%{text}%"))
                .order_by(FoodItem.id.asc())
                .limit(safe_limit)
            )
        ).all()
        return [food_row(food) for food in rows]

    def list_browse_categories(self) -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in CATEGORY_CODES]

    async def browse_foods(
        self,
        user_id: int,
        category: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), _PAGE_SIZE_MAX)
        needle = (keyword or "").strip().lower()
        category_code = category or "staple"

        # 自定义/收藏 Tab 还没实现，空关键词时直接空页——这是存量行为，
        # 前端据此显示"暂无内容"而不是把全库倒出来。
        if not needle and category_code in {"custom", "favorite"}:
            return _page([], safe_page, safe_page_size, 0)

        requested_category = normal_category_for_code(category_code)
        global_counts = await self._global_usage_counts()
        user_usage = await self._user_usage(user_id)

        foods = (await self._db.scalars(select(FoodItem).where(FoodItem.status == _ACTIVE))).all()
        if needle:
            matched = [food for food in foods if _contains_keyword(food, needle)]
        elif category_code == "frequent":
            matched = [food for food in foods if food.id in user_usage]
        else:
            matched = [
                food
                for food in foods
                if requested_category is not None and resolve(food.category) == requested_category
            ]

        if category_code == "frequent":
            matched.sort(key=lambda food: _frequent_key(food, user_usage))
        else:
            matched.sort(key=lambda food: _default_key(food, global_counts))

        total = len(matched)
        start = min((safe_page - 1) * safe_page_size, total)
        end = min(start + safe_page_size, total)
        items = [browse_item(food) for food in matched[start:end]]
        return _page(items, safe_page, safe_page_size, total)

    async def match_food(self, name: str | None) -> dict[str, Any]:
        normalized = (name or "").strip()
        if not normalized:
            return {"matchType": "NOT_FOUND", "normalizedName": None, "candidates": []}

        exact = (
            await self._db.scalars(
                select(FoodItem)
                .where(FoodItem.name == normalized, FoodItem.status == _ACTIVE)
                .order_by(FoodItem.id.asc())
            )
        ).all()
        if exact:
            return {
                "matchType": "EXACT",
                "normalizedName": normalized,
                "candidates": await self._candidates(list(exact)),
            }

        alias = await self._db.scalar(select(FoodAlias).where(FoodAlias.alias_name == normalized))
        if alias is not None:
            item = await self._db.get(FoodItem, alias.food_item_id)
            if item is not None and item.status == _ACTIVE:
                return {
                    "matchType": "ALIAS",
                    "normalizedName": item.name,
                    "candidates": await self._candidates([item]),
                }

        # 模糊匹配只认 active，且只取前 10 条；未命中返回 NOT_FOUND 而不是空候选表。
        fuzzy = (
            await self._db.scalars(
                select(FoodItem)
                .where(FoodItem.name.like(f"%{normalized}%"), FoodItem.status == _ACTIVE)
                .order_by(FoodItem.id.asc())
                .limit(_SEARCH_LIMIT_MAX)
            )
        ).all()
        return {
            "matchType": "AMBIGUOUS" if fuzzy else "NOT_FOUND",
            "normalizedName": normalized,
            "candidates": await self._candidates(list(fuzzy)),
        }

    # ---------------- 内部 ----------------

    async def grams_for_serving(self, name: str, quantity: float, unit: str) -> int | None:
        """用食物库自带的份量映射把"2 个鸡蛋"换算成克。

        返回 None 表示库里没有这个单位与食物的映射：调用方应继续追问克数，
        **不能**按经验值猜（"一个鸡蛋约 50 克"这种说法不进系统）。
        """
        target = (unit or "").strip()
        if not target:
            return None
        match = await self.match_food(name)
        for candidate in match.get("candidates") or []:
            for serving in candidate.get("servings") or []:
                if str(serving.get("servingName") or "").strip() != target:
                    continue
                try:
                    grams = Decimal(str(quantity)) * Decimal(str(serving.get("gramWeight")))
                except (ArithmeticError, ValueError):
                    continue
                if grams > 0:
                    return int(round(grams))
        return None

    async def _candidates(self, foods: list[FoodItem]) -> list[dict[str, Any]]:
        servings = await self._servings_by_food([food.id for food in foods])
        return [
            {
                "id": food.id,
                "name": food.name,
                "category": food.category,
                "unit": food.unit,
                "caloriePer100g": food.calorie_per_100g,
                "proteinPer100g": food.protein_per_100g,
                "fatPer100g": food.fat_per_100g,
                "carbPer100g": food.carb_per_100g,
                "coverImageUrl": food.cover_image_url,
                "servings": servings.get(food.id, []),
            }
            for food in foods
        ]

    async def _servings_by_food(self, food_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not food_ids:
            return {}
        rows = (
            await self._db.scalars(
                select(FoodServing)
                .where(FoodServing.food_item_id.in_(food_ids))
                .order_by(FoodServing.sort_order.asc(), FoodServing.id.asc())
            )
        ).all()
        grouped: dict[int, list[dict[str, Any]]] = {}
        for serving in rows:
            grouped.setdefault(serving.food_item_id, []).append(
                {
                    "id": serving.id,
                    "servingName": serving.serving_name,
                    "gramWeight": serving.gram_weight,
                }
            )
        return grouped

    async def _global_usage_counts(self) -> dict[int, int]:
        rows = (
            await self._db.execute(
                select(DietRecord.food_item_id, func.count())
                .where(DietRecord.food_item_id.is_not(None))
                .group_by(DietRecord.food_item_id)
            )
        ).all()
        return {int(food_id): int(count) for food_id, count in rows}

    async def _user_usage(self, user_id: int) -> dict[int, tuple[int, datetime | None]]:
        rows = (
            await self._db.execute(
                select(
                    DietRecord.food_item_id,
                    func.count(),
                    func.max(DietRecord.record_time),
                )
                .where(DietRecord.user_id == user_id, DietRecord.food_item_id.is_not(None))
                .group_by(DietRecord.food_item_id)
            )
        ).all()
        usage: dict[int, tuple[int, datetime | None]] = {}
        for food_id, count, last_used in rows:
            usage[int(food_id)] = (int(count), last_used)
        return usage


def _contains_keyword(food: FoodItem, needle: str) -> bool:
    name = (food.name or "").lower()
    brand = (food.brand or "").lower()
    return needle in name or needle in brand


def _frequent_key(food: FoodItem, usage: dict[int, tuple[int, datetime | None]]):
    count, last_used = usage.get(food.id, (0, None))
    # 次数降序 → 最近使用降序（None 排最后）→ 名称升序（None 排最后）→ id 升序。
    return (
        -count,
        last_used is None,
        -last_used.timestamp() if last_used else 0.0,
        food.name is None,
        food.name or "",
        food.id,
    )


def _default_key(food: FoodItem, global_counts: dict[int, int]):
    return (-global_counts.get(food.id, 0), food.name is None, food.name or "", food.id)


def _page(items: list[dict[str, Any]], page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "pageSize": page_size,
        "total": total,
        # hasMore 按"本条之后还有没有"算，与 Java 的 toIndex < total 一致。
        "hasMore": (page - 1) * page_size + len(items) < total,
    }
