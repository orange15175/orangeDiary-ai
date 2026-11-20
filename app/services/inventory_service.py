"""轻量食材记忆与核对：推荐、确认购买、最近用过分别记录，不推断剩余克重。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.core.limiter import APP_ZONE
from app.models.diet_assistant import (
    IngredientCheckItem,
    IngredientCheckSession,
    KnownIngredient,
    MealPlan,
    MealPlanVersion,
    PlanMeal,
    ShoppingItem,
    ShoppingList,
)
from app.services.recipe_service import RecipeService

_PRESENCE = {"present", "absent", "unknown"}
_QUANTITY_STATE = {"known", "approximate", "unknown"}
_ANSWERS = {"have_enough", "have_not_enough", "some", "none", "unknown"}


def effective_presence(row: KnownIngredient) -> str:
    if row.presence == "absent":
        return "absent"
    confirmed = row.last_confirmed_at
    if confirmed and confirmed.date() == datetime.now(APP_ZONE).date() and row.source != "used":
        return row.presence
    return "unknown"



class InventoryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._recipes = RecipeService(db)

    # ---------------- 家中食材 ----------------

    async def list_known(self, user_id: int, keyword: str | None = None) -> list[dict[str, Any]]:
        stmt = select(KnownIngredient).where(KnownIngredient.user_id == user_id)
        if keyword and keyword.strip():
            stmt = stmt.where(KnownIngredient.label.like(f"%{keyword.strip()}%"))
        rows = list(await self._db.scalars(stmt.order_by(KnownIngredient.updated_at.desc()).limit(100)))
        result = [self._serialize(row) for row in rows]
        labels = {item["label"] for item in result}
        cutoff = datetime.now(APP_ZONE).replace(tzinfo=None) - timedelta(days=7)
        suggestions = await self._db.scalars(select(ShoppingItem).join(ShoppingList, ShoppingItem.list_id == ShoppingList.id).where(
            ShoppingList.user_id == user_id, ShoppingList.created_at >= cutoff,
            ShoppingItem.state.in_(("to_buy", "needs_check", "purchased")),
        ).order_by(ShoppingList.created_at.desc()).limit(100))
        for item in suggestions:
            if item.label in labels or (keyword and keyword not in item.label):
                continue
            labels.add(item.label)
            result.append({"id": None, "label": item.label, "foodItemId": item.food_item_id,
                "presence": "unknown", "quantityState": "unknown", "source": "suggestion",
                "version": None, "hint": "之前的采购建议里有它；是否买过、现在是否有剩余，尚未确认。"})
        return result

    async def remember(self, user_id: int, label: str, food_id: int | None, presence: str, source: str = "manual") -> KnownIngredient:
        query = select(KnownIngredient).where(KnownIngredient.user_id == user_id)
        query = query.where(KnownIngredient.food_item_id == food_id) if food_id else query.where(KnownIngredient.label == label)
        row = await self._db.scalar(query.order_by(KnownIngredient.id.desc()).limit(1).with_for_update())
        if row is None:
            row = KnownIngredient(user_id=user_id, label=label, food_item_id=food_id, version=0)
            self._db.add(row)
        row.presence = presence
        row.quantity_state = "unknown"
        row.quantity = None
        row.unit = None
        row.source = source
        row.last_confirmed_at = datetime.now(APP_ZONE).replace(tzinfo=None)
        row.version = (row.version or 0) + 1
        await self._db.flush()
        return row

    async def mark_used(self, user_id: int, recipe_id: int) -> None:
        recipe = await self._recipes.get_recipe(recipe_id)
        if recipe:
            for ingredient in recipe["ingredients"]:
                if not ingredient.get("optional"):
                    await self.remember(user_id, ingredient["name"], ingredient.get("foodItemId"), "unknown", "used")

    async def upsert(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("presence") and payload["presence"] not in _PRESENCE:
            raise BusinessError(400, "presence 只能是 present / absent / unknown")
        if payload.get("quantityState") and payload["quantityState"] not in _QUANTITY_STATE:
            raise BusinessError(400, "quantityState 只能是 known / approximate / unknown")
        label = str(payload.get("label") or "").strip()
        if not label:
            raise BusinessError(400, "食材名称不能为空")
        if payload.get("id"):
            row = await self._db.scalar(select(KnownIngredient).where(KnownIngredient.id == payload["id"], KnownIngredient.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            if row is None:
                raise BusinessError(404, "食材记录不存在")
            if payload.get("version") is not None and payload["version"] != row.version:
                raise BusinessError(409, "食材记录已更新，请刷新后重试")
            label, food_id = row.label, row.food_item_id
        else:
            food_id = payload.get("foodItemId")
            if food_id is None:
                from app.models.food import FoodItem
                food_id = await self._db.scalar(select(FoodItem.id).where(FoodItem.name == label, FoodItem.status == "active").limit(1))
        row = await self.remember(user_id, label, food_id, payload.get("presence") or "unknown", "manual")
        await self._db.commit()
        return self._serialize(row)

    async def archive(self, user_id: int, ingredient_id: int, version: int | None) -> None:
        row = await self._db.get(KnownIngredient, ingredient_id)
        if row is None or row.user_id != user_id:
            raise BusinessError(404, "食材记录不存在")
        if version is not None and version != row.version:
            raise BusinessError(409, "食材记录已更新，请刷新后重试")
        row.presence = "absent"
        row.version += 1
        await self._db.commit()

    # ---------------- 按需核对 ----------------

    async def create_check(
        self, user_id: int, plan_id: int, version_number: int | None
    ) -> dict[str, Any]:
        plan = await self._db.get(MealPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            raise BusinessError(404, "计划不存在")
        version = await self._version(plan, version_number)
        if version is None:
            raise BusinessError(404, "计划版本不存在")
        requirements = await self._requirements([version.id])
        known = {
            row.food_item_id: row
            for row in await self._db.scalars(
                select(KnownIngredient).where(KnownIngredient.user_id == user_id)
            )
            if row.food_item_id
        }
        session = IngredientCheckSession(
            user_id=user_id, plan_version_ref=f"{plan.id}:{version.version_number}", status="open"
        )
        self._db.add(session)
        await self._db.flush()
        needs_check = False
        for key, item in requirements.items():
            food_id = item.get("foodItemId")
            need = item["need"]
            row = known.get(food_id)
            if row is None:
                row = await self._db.scalar(select(KnownIngredient).where(KnownIngredient.user_id == user_id, KnownIngredient.label == item["label"]).order_by(KnownIngredient.id.desc()).limit(1))
            status, assessment, confirmed = self._assess(need, row)
            self._db.add(
                IngredientCheckItem(
                    session_id=session.id,
                    food_item_id=food_id,
                    label=item["label"],
                    needed_quantity=need,
                    unit="g",
                    existing_assessment=assessment,
                    confirmed_quantity=confirmed,
                    status=status,
                )
            )
            if status == "needs_check":
                needs_check = True
        session.status = "open" if needs_check else "confirmed"
        await self._db.commit()
        return await self.get_check(user_id, session.id)

    async def get_check(self, user_id: int, session_id: int) -> dict[str, Any]:
        session = await self._db.get(IngredientCheckSession, session_id)
        if session is None or session.user_id != user_id:
            raise BusinessError(404, "核对会话不存在")
        items = list(
            await self._db.scalars(
                select(IngredientCheckItem)
                .where(IngredientCheckItem.session_id == session_id)
                .order_by(IngredientCheckItem.id.asc())
            )
        )
        return {
            "id": session.id,
            "status": session.status,
            "planVersionRef": session.plan_version_ref,
            "version": session.version,
            "items": [
                {
                    "id": row.id,
                    "label": row.label,
                    "foodItemId": row.food_item_id,
                    "neededQuantity": row.needed_quantity,
                    "unit": row.unit,
                    "existingAssessment": row.existing_assessment,
                    "answer": row.answer,
                    "confirmedQuantity": row.confirmed_quantity,
                    "status": row.status,
                }
                for row in items
            ],
        }

    async def answer(
        self, user_id: int, session_id: int, answers: list[dict[str, Any]], version: int | None
    ) -> dict[str, Any]:
        session = await self._db.scalar(select(IngredientCheckSession).where(IngredientCheckSession.id == session_id, IngredientCheckSession.user_id == user_id).with_for_update().execution_options(populate_existing=True))
        if session is None:
            raise BusinessError(404, "核对会话不存在")
        if version is not None and version != session.version:
            raise BusinessError(409, "核对会话已更新，请刷新后重试")
        items = {
            row.id: row
            for row in await self._db.scalars(
                select(IngredientCheckItem).where(IngredientCheckItem.session_id == session_id)
            )
        }
        for answer in answers:
            item = items.get(int(answer.get("itemId") or 0))
            if item is None:
                continue
            value = answer.get("answer")
            if value not in _ANSWERS:
                raise BusinessError(400, "答题只能是 have_enough / have_not_enough / none / unknown")
            item.answer = value
            presence = "present" if value in {"have_enough", "some"} else "absent" if value == "none" else "unknown"
            await self.remember(user_id, item.label, item.food_item_id, presence)
            if value in {"have_enough", "some"}:
                item.status = "confirmed"
                item.confirmed_quantity = None
            elif value == "have_not_enough":
                item.status = "to_buy"
                if answer.get("confirmedQuantity") is not None:
                    item.confirmed_quantity = Decimal(str(answer["confirmedQuantity"]))
            elif value == "none":
                item.status = "to_buy"
                item.confirmed_quantity = Decimal("0")
            else:
                item.status = "needs_check"
        session.version += 1
        await self._db.flush()
        remaining = [row for row in items.values() if row.status == "needs_check"]
        session.status = "open" if remaining else "confirmed"
        await self._db.commit()
        return await self.get_check(user_id, session_id)

    # ---------------- 内部 ----------------

    async def _version(self, plan: MealPlan, version_number: int | None) -> MealPlanVersion | None:
        if version_number is not None:
            return await self._db.scalar(
                select(MealPlanVersion).where(
                    MealPlanVersion.plan_id == plan.id,
                    MealPlanVersion.version_number == version_number,
                )
            )
        if plan.active_version_id:
            return await self._db.get(MealPlanVersion, plan.active_version_id)
        return await self._db.scalar(
            select(MealPlanVersion)
            .where(MealPlanVersion.plan_id == plan.id)
            .order_by(MealPlanVersion.version_number.desc())
            .limit(1)
        )

    async def _requirements(self, version_ids: list[int]) -> dict[int, dict[str, Any]]:
        meals = list(
            await self._db.scalars(select(PlanMeal).where(PlanMeal.plan_version_id.in_(version_ids)))
        )
        requirements: dict[int, dict[str, Any]] = {}
        for meal in meals:
            detail = await self._recipes.get_recipe(meal.recipe_id) if meal.recipe_id else None
            if detail is None:
                continue
            base = detail["nutrition"]["baseServings"] or 1
            ratio = Decimal(meal.servings) / Decimal(base)
            for ingredient in detail["ingredients"]:
                if ingredient.get("optional"):
                    continue
                food_id, grams = ingredient.get("foodItemId"), ingredient.get("grams")
                key = food_id or "label:" + ingredient["name"]
                entry = requirements.setdefault(key, {"label": ingredient["name"], "foodItemId": food_id, "need": Decimal("0")})
                if grams is None:
                    entry["need"] = None
                elif entry["need"] is not None:
                    entry["need"] += Decimal(str(grams)) * ratio
        return requirements

    @staticmethod
    def _assess(
        need: Decimal | None, row: KnownIngredient | None
    ) -> tuple[str, str, Decimal | None]:
        if row is None:
            return "needs_check", "unknown", None
        presence = effective_presence(row)
        if presence == "absent":
            return "to_buy", "absent", None
        if presence == "present":
            return "confirmed", "recently_confirmed", None
        return "needs_check", "leftover_unknown", None

    @staticmethod
    def _serialize(row: KnownIngredient) -> dict[str, Any]:
        return {
            "id": row.id,
            "label": row.label,
            "foodItemId": row.food_item_id,
            "presence": effective_presence(row),
            "lastConfirmedAt": row.last_confirmed_at.isoformat() if row.last_confirmed_at else None,
            "hint": ("最近用过，是否有剩余？" if row.source == "used" else "确认购买过，现在还有剩余吗？" if row.source == "purchase" else "之前记过这项食材，现在还有吗？") if effective_presence(row) == "unknown" else ("今天确认还有一些，不估算剩余克重。" if row.presence == "present" else "用户确认已用完。"),
            "quantityState": "unknown",
            "foodState": row.food_state,
            "storageLocation": row.storage_location,
            "source": row.source,
            "version": row.version,
        }
