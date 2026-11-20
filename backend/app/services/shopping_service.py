"""采购清单：缺口计算、价格占位与购买入库。

三条必须守住的口径（设计 §8）：
- 缺口 = max(需求 - 已确认可分配量, 0)，**只有状态与单位可比较时才成立**；
  数量未知就标 unknown，不代 0，也不得出"缺 400g"这种假精确；
- 价格未知时只给已知小计与未知项数量，strict 预算一律 `unverified`；
- 购买与入库幂等：同一 `requestKey` 重复提交只产生一次流水，已购事实不可被计划覆盖。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.core.jsonable import to_jsonable
from app.models.diet_assistant import (
    IngredientCheckItem,
    IngredientCheckSession,
    IngredientLedger,
    KnownIngredient,
    MealPlan,
    MealPlanVersion,
    PlanMeal,
    ShoppingItem,
    ShoppingItemSource,
    ShoppingList,
)
from app.services.price_provider import PriceProvider, build_price_provider
from app.services.recipe_service import RecipeService


class ShoppingService:
    def __init__(self, db: AsyncSession, provider: PriceProvider | None = None) -> None:
        self._db = db
        self._recipes = RecipeService(db)
        self._provider = provider or build_price_provider()

    async def build_list(
        self, user_id: int, plan_id: int, version_number: int | None
    ) -> dict[str, Any]:
        plan = await self._db.get(MealPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            raise BusinessError(404, "计划不存在")
        version = await self._version(plan, version_number)
        if version is None:
            raise BusinessError(404, "计划版本不存在")

        requirements = await self._requirements(version.id)
        from app.services.inventory_service import InventoryService
        memories = await InventoryService(self._db).list_known(user_id)
        by_food = {row["foodItemId"]: row for row in memories if row.get("foodItemId")}

        listing = ShoppingList(
            user_id=user_id,
            date_start=plan.start_date,
            date_end=plan.end_date,
            plan_version_refs=to_jsonable([f"{plan.id}:{version.version_number}"]),
            status="draft",
        )
        self._db.add(listing)
        await self._db.flush()

        unknown_count = 0
        for key, item in requirements.items():
            food_id = item.get("foodItemId")
            need = item["need"]
            memory = by_food.get(food_id) or next((row for row in memories if row["label"] == item["label"]), None)
            presence = memory.get("presence") if memory else "unknown"
            have = None
            status, shortage = ("confirmed_present", None) if presence == "present" else ("confirmed_absent", need) if presence == "absent" else ("unknown", None)
            if status == "unknown":
                unknown_count += 1
                state = "needs_check"
            elif status == "confirmed_absent":
                state = "to_buy"
            else:
                state = "skipped"
            price = await self._provider.quote(item["label"], None, None)
            self._db.add(
                ShoppingItem(
                    list_id=listing.id,
                    food_item_id=food_id,
                    label=item["label"],
                    need_quantity=need,
                    available_quantity=have,
                    shortage_quantity=shortage,
                    quantity_status=status,
                    buy_quantity=shortage if shortage and shortage > 0 else None,
                    unit="g",
                    state=state,
                    price_snapshot=to_jsonable({
                        "priceStatus": "known" if price else "unknown",
                        "provider": self._provider.name,
                        "unitPriceLow": price.unit_price_low if price else None,
                        "unitPriceHigh": price.unit_price_high if price else None,
                        "sourceUrl": price.source_url if price else None,
                    }),
                )
            )
        await self._db.flush()

        totals = {
            "priceStatus": "unknown" if not self._provider.available else "partial",
            "budgetStatus": "unverified",
            "unknownItemCount": unknown_count,
            "knownSubtotal": None,
            "note": "价格来源未配置，已知小计无法计算；完整预算待核实。"
            if not self._provider.available
            else "部分条目缺价，完整预算待核实。",
        }
        listing.totals = to_jsonable(totals)
        listing.coverage = Decimal("0.0000") if not self._provider.available else None
        await self._db.commit()
        return await self.get_list(user_id, listing.id)

    async def get_list(self, user_id: int, list_id: int) -> dict[str, Any]:
        listing = await self._db.get(ShoppingList, list_id)
        if listing is None or listing.user_id != user_id:
            raise BusinessError(404, "采购清单不存在")
        items = list(
            await self._db.scalars(
                select(ShoppingItem)
                .where(ShoppingItem.list_id == list_id)
                .order_by(ShoppingItem.id.asc())
            )
        )
        return {
            "id": listing.id,
            "status": listing.status,
            "startDate": listing.date_start.isoformat(),
            "endDate": listing.date_end.isoformat(),
            "totals": listing.totals,
            "coverage": listing.coverage,
            "ingredientPolicy": "light_memory",
            "version": listing.version,
            "items": [
                {
                    "id": row.id,
                    "label": row.label,
                    "foodItemId": row.food_item_id,
                    "needQuantity": row.need_quantity,
                    "availableQuantity": row.available_quantity,
                    "shortageQuantity": row.shortage_quantity,
                    "quantityStatus": row.quantity_status,
                    "buyQuantity": row.buy_quantity,
                    "unit": row.unit,
                    "state": row.state,
                    "price": row.price_snapshot,
                    "quantityNote": "菜谱参考用量，不代表家中缺少这么多",
                }
                for row in items
            ],
        }

    async def purchase(
        self,
        user_id: int,
        item_id: int,
        *,
        actual_quantity: Decimal | None,
        paid_price: Decimal | None,
        add_to_ingredients: bool,
        request_key: str,
    ) -> dict[str, Any]:
        item = await self._db.scalar(select(ShoppingItem).where(ShoppingItem.id == item_id).with_for_update().execution_options(populate_existing=True))
        if item is None:
            raise BusinessError(404, "采购条目不存在")
        listing = await self._db.get(ShoppingList, item.list_id)
        if listing is None or listing.user_id != user_id:
            raise BusinessError(404, "采购条目不存在")

        idem = f"purchase:{user_id}:{request_key}"
        existing = await self._db.scalar(
            select(IngredientLedger).where(IngredientLedger.idempotency_key == idem)
        )
        if existing is not None:
            if existing.cause_id != str(item.id):
                raise BusinessError(409, "该请求标识已用于其他采购条目")
            # 同一请求重复提交：直接返回当前状态，不再入库、不再记流水。
            return await self.get_list(user_id, listing.id)

        if item.state == "purchased":
            return await self.get_list(user_id, listing.id)
        item.state = "purchased"
        if paid_price is not None:
            item.price_snapshot = to_jsonable({**(item.price_snapshot or {}), "paidPrice": paid_price})
        await self._db.flush()

        if add_to_ingredients:
            from app.services.inventory_service import InventoryService
            previous = await self._db.scalar(select(KnownIngredient).where(KnownIngredient.user_id == user_id, KnownIngredient.food_item_id == item.food_item_id).order_by(KnownIngredient.id.desc()).limit(1)) if item.food_item_id else None
            before = {"presence": previous.presence, "source": previous.source, "lastConfirmedAt": previous.last_confirmed_at.isoformat() if previous.last_confirmed_at else None} if previous else None
            memory = await InventoryService(self._db).remember(user_id, item.label, item.food_item_id, "present", "purchase")
            item.price_snapshot = to_jsonable({**(item.price_snapshot or {}), "memoryId": memory.id, "memoryVersion": memory.version, "memoryBefore": before})
        self._db.add(IngredientLedger(user_id=user_id, ingredient_id=memory.id if add_to_ingredients else 0,
            event_type="purchase", delta=None, unit="memory", cause_type="shopping_item", cause_id=str(item.id), idempotency_key=idem))
        await self._db.commit()
        return await self.get_list(user_id, listing.id)

    async def reverse_purchase(
        self, user_id: int, item_id: int, *, reason: str, request_key: str
    ) -> dict[str, Any]:
        item = await self._db.scalar(select(ShoppingItem).where(ShoppingItem.id == item_id).with_for_update().execution_options(populate_existing=True))
        if item is None:
            raise BusinessError(404, "采购条目不存在")
        listing = await self._db.get(ShoppingList, item.list_id)
        if listing is None or listing.user_id != user_id:
            raise BusinessError(404, "采购条目不存在")
        idem = f"purchase-reversal:{user_id}:{request_key}"
        existing = await self._db.scalar(
            select(IngredientLedger).where(IngredientLedger.idempotency_key == idem)
        )
        if existing is not None:
            return await self.get_list(user_id, listing.id)
        if item.state != "purchased":
            return await self.get_list(user_id, listing.id)
        snapshot = dict(item.price_snapshot or {})
        if snapshot.get("memoryId"):
            memory = await self._db.scalar(select(KnownIngredient).where(KnownIngredient.id == snapshot["memoryId"], KnownIngredient.user_id == user_id).with_for_update())
            if memory and memory.version == snapshot.get("memoryVersion"):
                before = snapshot.get("memoryBefore") or {}
                memory.presence = before.get("presence", "unknown")
                memory.source = before.get("source", "manual")
                memory.last_confirmed_at = datetime.fromisoformat(before["lastConfirmedAt"]) if before.get("lastConfirmedAt") else None
                memory.quantity, memory.quantity_state = None, "unknown"
                memory.version += 1
        item.state = "to_buy"
        snapshot.pop("paidPrice", None)
        snapshot.pop("memoryId", None)
        snapshot.pop("memoryVersion", None)
        item.price_snapshot = to_jsonable(snapshot)
        self._db.add(IngredientLedger(user_id=user_id, ingredient_id=0, event_type="purchase_reversal", delta=None,
            unit="memory", cause_type="shopping_item", cause_id=str(item.id), idempotency_key=idem))
        await self._db.commit()
        return await self.get_list(user_id, listing.id)

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

    async def _requirements(self, version_id: int) -> dict[int, dict[str, Any]]:
        from app.services.inventory_service import InventoryService
        return await InventoryService(self._db)._requirements([version_id])

    @staticmethod
    def _shortage(need: Decimal, have: Decimal | None, unknown: bool) -> tuple[str, Decimal | None]:
        if unknown or have is None:
            return "unknown", None
        return "known", max(need - have, Decimal("0"))
