"""食谱读取、营养计算与份量缩放。

复用现有 `food_item` 的每 100g 营养数据，不新建营养表也不编造数值：

- 能确定克重的食材才计入营养；单位是"个/碗/勺"时查 `food_serving` 换算，
  缺换算资料则保持未知，**不按克硬算**。
- 营养缺失保持 null，并给出 `coverage`（已解析食材占比）与 `missing`，
  让前端能显示"部分食材营养待补充"，而不是把未知当 0。
- 缩放只按份数等比放大食材克重，营养随克重重新计算，不改原食谱数据。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diet_assistant import Recipe, RecipeIngredient, RecipeNutrition, RecipeStep
from app.models.food import FoodItem, FoodServing

_GRAM_UNITS = {"g", "克", "公克", "gram"}
_KG_UNITS = {"kg", "千克", "公斤"}


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class RecipeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------- 查询 ----------------

    async def list_recipes(
        self,
        *,
        keyword: str | None = None,
        category: str | None = None,
        source_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        stmt = select(Recipe).where(Recipe.status == "active")
        if category:
            stmt = stmt.where(Recipe.category == category)
        if source_type:
            stmt = stmt.where(Recipe.source_type == source_type)
        if keyword and keyword.strip():
            like = f"%{keyword.strip()}%"
            # 名称或任一食材命中都算；食材名在子查询里匹配。
            ingredient_recipe_ids = select(RecipeIngredient.recipe_id).where(
                RecipeIngredient.original_name.like(like)
            )
            stmt = stmt.where(or_(Recipe.name.like(like), Recipe.id.in_(ingredient_recipe_ids)))
        total = await self._db.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())
        )
        rows = list(
            await self._db.scalars(
                stmt.order_by(Recipe.id.asc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        return {
            "records": [await self._summary(row) for row in rows],
            "total": int(total or 0),
            "page": page,
            "pageSize": page_size,
        }

    async def get_recipe(self, recipe_id: int) -> dict[str, Any] | None:
        recipe = await self._db.get(Recipe, recipe_id)
        if recipe is None or recipe.status != "active":
            return None
        detail = await self._summary(recipe)
        detail["steps"] = await self._steps(recipe_id)
        detail["ingredients"] = await self._ingredients(recipe_id)
        detail["nutritionDetail"] = await self.compute_nutrition(recipe_id)
        return detail

    async def search_for_chef(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """私人厨师的候选检索：按名称/食材/分类做确定性匹配，返回带营养的摘要。"""
        result = await self.list_recipes(keyword=query, page=1, page_size=limit)
        return result["records"]

    async def _summary(self, recipe: Recipe) -> dict[str, Any]:
        nutrition = await self.compute_nutrition(recipe.id)
        return {
            "id": recipe.id,
            "name": recipe.name,
            "category": recipe.category,
            "servings": recipe.servings,
            "prepMinutes": recipe.prep_minutes,
            "cookMinutes": recipe.cook_minutes,
            "equipment": recipe.equipment,
            "scope": recipe.scope,
            "sourceType": recipe.source_type,
            "sourceUrl": recipe.source_url,
            "license": recipe.license,
            "nutrition": nutrition,
        }

    async def _steps(self, recipe_id: int) -> list[dict[str, Any]]:
        rows = list(
            await self._db.scalars(
                select(RecipeStep)
                .where(RecipeStep.recipe_id == recipe_id)
                .order_by(RecipeStep.step_index.asc())
            )
        )
        return [
            {
                "index": row.step_index,
                "description": row.description,
                "durationHint": row.duration_hint,
                "equipment": row.equipment,
            }
            for row in rows
        ]

    async def _ingredient_rows(self, recipe_id: int) -> list[RecipeIngredient]:
        return list(
            await self._db.scalars(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == recipe_id)
                .order_by(RecipeIngredient.sort_order.asc(), RecipeIngredient.id.asc())
            )
        )

    async def _ingredients(self, recipe_id: int) -> list[dict[str, Any]]:
        rows = await self._ingredient_rows(recipe_id)
        foods = await self._resolve_foods(rows)
        servings = await self._serving_map(foods.keys())
        result = []
        for row in rows:
            food = foods.get(row.food_item_id) if row.food_item_id else None
            grams, basis = self._grams_for(row, food, servings)
            result.append(
                {
                    "id": row.id,
                    "name": row.original_name,
                    "foodItemId": row.food_item_id,
                    "quantity": _dec(row.quantity),
                    "unit": row.unit,
                    "grams": grams,
                    "gramStatus": basis,
                    "basis": row.basis,
                    "optional": row.optional,
                    "allergenStatus": row.allergen_status,
                }
            )
        return result

    # ---------------- 营养 ----------------

    async def compute_nutrition(self, recipe_id: int) -> dict[str, Any]:
        recipe = await self._db.get(Recipe, recipe_id)
        base_servings = recipe.servings if recipe and recipe.servings else 1
        rows = await self._ingredient_rows(recipe_id)
        foods = await self._resolve_foods(rows)
        servings = await self._serving_map(foods.keys())

        totals = {"calorie": Decimal("0"), "protein": Decimal("0"), "fat": Decimal("0"), "carb": Decimal("0")}
        known_flags = {"calorie": 0, "protein": 0, "fat": 0, "carb": 0}
        accounted = 0
        required = 0
        missing: list[str] = []

        for row in rows:
            if row.optional:
                continue
            required += 1
            food = foods.get(row.food_item_id) if row.food_item_id else None
            grams, _ = self._grams_for(row, food, servings)
            if food is None or grams is None:
                missing.append(row.original_name)
                continue
            accounted += 1
            factor = grams / Decimal("100")
            for key, column in (
                ("calorie", food.calorie_per_100g),
                ("protein", food.protein_per_100g),
                ("fat", food.fat_per_100g),
                ("carb", food.carb_per_100g),
            ):
                value = _dec(column)
                if value is None:
                    continue
                totals[key] += value * factor
                known_flags[key] += 1

        coverage = (
            Decimal(accounted) / Decimal(required) if required else Decimal("1")
        ).quantize(Decimal("0.0001"))
        # 任一营养项都没有已知来源时返回 null，不让 0 冒充"零热量"。
        nutrients = {
            key: (totals[key].quantize(Decimal("0.01")) if known_flags[key] else None)
            for key in totals
        }
        return {
            "baseServings": base_servings,
            "total": nutrients,
            "coverage": coverage,
            "missing": missing,
        }

    async def scale_servings(self, recipe_id: int, target_servings: int) -> dict[str, Any] | None:
        detail = await self.get_recipe(recipe_id)
        if detail is None:
            return None
        base = detail["nutrition"]["baseServings"] or 1
        ratio = Decimal(max(1, target_servings)) / Decimal(base)
        scaled_ingredients = []
        for item in detail["ingredients"]:
            grams = item["grams"]
            scaled_ingredients.append(
                {**item, "grams": (grams * ratio).quantize(Decimal("0.01")) if grams is not None else None}
            )
        total = detail["nutrition"]["total"]
        scaled_total = {
            key: (value * ratio).quantize(Decimal("0.01")) if value is not None else None
            for key, value in total.items()
        }
        detail["targetServings"] = target_servings
        detail["servings"] = target_servings
        detail["ingredients"] = scaled_ingredients
        detail["nutrition"] = {**detail["nutrition"], "baseServings": target_servings, "total": scaled_total}
        detail["nutritionDetail"] = detail["nutrition"]
        return detail

    # ---------------- 解析辅助 ----------------

    async def _resolve_foods(
        self, rows: list[RecipeIngredient]
    ) -> dict[int, FoodItem]:
        ids = {row.food_item_id for row in rows if row.food_item_id}
        names = {row.original_name for row in rows if not row.food_item_id and row.original_name}
        foods: dict[int, FoodItem] = {}
        if ids:
            for food in await self._db.scalars(
                select(FoodItem).where(FoodItem.id.in_(ids), FoodItem.status == "active")
            ):
                foods[food.id] = food
        if names:
            for food in await self._db.scalars(
                select(FoodItem).where(FoodItem.name.in_(names), FoodItem.status == "active")
            ):
                foods[food.id] = food
            # 把按名称命中的 food 回填到行上（只回填本次调用，不改库）。
            by_name = {food.name: food for food in foods.values()}
            for row in rows:
                if not row.food_item_id and row.original_name in by_name:
                    row.food_item_id = by_name[row.original_name].id
        return foods

    async def _serving_map(self, food_ids) -> dict[tuple[int, str], Decimal]:
        ids = [fid for fid in food_ids if fid]
        if not ids:
            return {}
        rows = await self._db.scalars(
            select(FoodServing).where(FoodServing.food_item_id.in_(ids))
        )
        return {(row.food_item_id, row.serving_name): _dec(row.gram_weight) for row in rows}

    def _grams_for(
        self, row: RecipeIngredient, food: FoodItem | None, servings: dict[tuple[int, str], Decimal]
    ) -> tuple[Decimal | None, str]:
        if row.gram_equivalent is not None:
            return _dec(row.gram_equivalent), "declared"
        quantity = _dec(row.quantity)
        if quantity is None:
            return None, "unknown"
        unit = (row.unit or "").strip().lower()
        if unit in _GRAM_UNITS:
            return quantity, "gram"
        if unit in _KG_UNITS:
            return quantity * Decimal("1000"), "gram"
        if food is None:
            return None, "unknown"
        gram_weight = servings.get((food.id, row.unit or ""))
        if gram_weight is None:
            return None, "unknown"
        return quantity * gram_weight, "serving"
