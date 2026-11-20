"""周计划：轻量食材记忆辅助选餐，草稿与采纳分离；不维护精确库存或新增预留。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.core.jsonable import to_jsonable
from app.models.diet_assistant import (
    DietPreference,
    IngredientReservation,
    KnownIngredient,
    MealPlan,
    MealPlanVersion,
    PlanMeal,
    Recipe,
)
from app.models.user import User
from app.services.recipe_service import RecipeService
from app.services.summary_service import daily_target

# 三餐热量分配是设计默认值，不是营养学结论；用户可后续在偏好里调整。
_MEAL_SHARE = {"breakfast": Decimal("0.30"), "lunch": Decimal("0.40"), "dinner": Decimal("0.30")}
_MAX_DAYS = 7
_MAX_SERVINGS = 3


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class PlanService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._recipes = RecipeService(db)

    # ---------------- 生成草稿 ----------------

    async def generate_draft(self, user: User, req: dict[str, Any]) -> dict[str, Any]:
        start = req["startDate"]
        days = min(max(int(req.get("days") or 1), 1), _MAX_DAYS)
        meal_types = req.get("mealTypes") or ["breakfast", "lunch", "dinner"]
        end = start + timedelta(days=days - 1)

        target = await daily_target(self._db, user.id, start)
        if target <= 0:
            raise BusinessError(400, "请先完善身高体重或设置每日目标，才能生成计划")

        preference = await self._preference(user.id)
        allergens = _split(req.get("allergens")) or _split(preference.allergens if preference else None)
        equipment = _split(req.get("equipment")) or _split(preference.equipment if preference else None)
        max_minutes = req.get("maxPrepMinutes") or (preference.prep_time_limit if preference else None)
        ingredient_mode = req.get("ingredientMode") or (
            preference.ingredient_mode if preference else "prefer_available"
        )

        candidates = await self._candidates(allergens, equipment, max_minutes, ingredient_mode, user.id)
        plan = MealPlan(user_id=user.id, start_date=start, end_date=end, status="draft")
        self._db.add(plan)
        await self._db.flush()

        if not candidates:
            version = MealPlanVersion(
                plan_id=plan.id,
                version_number=1,
                input_snapshot=to_jsonable(req),
                checks=to_jsonable({
                    "code": "CANDIDATE_POOL_INSUFFICIENT",
                    "reason": "当前条件下没有满足硬约束的食谱",
                    "allergens": allergens,
                    "equipment": equipment,
                    "maxPrepMinutes": max_minutes,
                    "ingredientMode": ingredient_mode,
                }),
                status="infeasible",
                reason="候选食谱不足",
            )
            self._db.add(version)
            await self._db.commit()
            return {"planId": plan.id, "status": "infeasible", "checks": version.checks, "meals": []}

        share = self._shares(meal_types)
        used_by_date: dict[date, set[int]] = {}
        previous_ids: set[int] = set()
        meals: list[PlanMeal] = []
        nutrition_known = 0
        nutrition_total = 0

        for offset in range(days):
            day = start + timedelta(days=offset)
            used_by_date[day] = set()
            for meal_type in meal_types:
                meal_target = target * share.get(meal_type, Decimal("0.33"))
                chosen = self._pick(candidates, used_by_date[day], previous_ids)
                if chosen is None:
                    continue
                used_by_date[day].add(chosen["id"])
                servings, snapshot = self._portion(chosen, meal_target)
                meals.append(
                    PlanMeal(
                        plan_version_id=0,  # 版本写回后统一修正
                        meal_date=day,
                        meal_type=meal_type,
                        recipe_id=chosen["id"],
                        recipe_version=chosen.get("version") or 1,
                        servings=servings,
                        nutrition_snapshot=to_jsonable(snapshot),
                        execution_state="planned",
                    )
                )
                nutrition_total += 1
                if snapshot and snapshot.get("calorie") is not None:
                    nutrition_known += 1
            previous_ids = set(used_by_date[day])

        complete = len(meals) == days * len(meal_types)
        if not complete:
            plan.status = "infeasible"
        version = MealPlanVersion(
            plan_id=plan.id,
            version_number=1,
            input_snapshot=to_jsonable(req),
            constraints=to_jsonable({"allergens": allergens, "equipment": equipment, "maxPrepMinutes": max_minutes, "ingredientMode": ingredient_mode}),
            checks=to_jsonable({"code": "OK" if complete else "CANDIDATE_POOL_INSUFFICIENT", "mealCount": len(meals), "requestedMealCount": days * len(meal_types), "ingredientPolicy": "light_memory"}),
            nutrition_coverage=(
                Decimal(nutrition_known) / Decimal(nutrition_total) if nutrition_total else None
            ),
            status="draft" if complete else "infeasible",
        )
        self._db.add(version)
        await self._db.flush()
        for meal in meals:
            meal.plan_version_id = version.id
            self._db.add(meal)
        await self._db.commit()
        return await self.get_plan(user.id, plan.id)

    async def _candidates(
        self,
        allergens: list[str],
        equipment: list[str],
        max_minutes: int | None,
        ingredient_mode: str,
        user_id: int,
    ) -> list[dict[str, Any]]:
        rows = list(await self._db.scalars(select(Recipe).where(Recipe.status == "active")))
        memories = list(await self._db.scalars(select(KnownIngredient).where(KnownIngredient.user_id == user_id)))
        from app.services.inventory_service import effective_presence
        from app.services.inventory_service import InventoryService
        memory_hints = await InventoryService(self._db).list_known(user_id)
        available_ids = {row.food_item_id for row in memories if effective_presence(row) == "present"}
        available_labels = [row.label for row in memories if effective_presence(row) == "present"]
        preference = await self._preference(user_id)
        dislikes = _split(preference.dislikes if preference else None)
        result: list[dict[str, Any]] = []
        for row in rows:
            detail = await self._recipes.get_recipe(row.id)
            if detail is None:
                continue
            names = " ".join(str(item.get("name") or "") for item in detail["ingredients"])
            if any(a and a in names for a in allergens + dislikes):
                continue
            if equipment:
                needed = [part.strip() for part in (row.equipment or "").split(",") if part.strip()]
                if any(item not in equipment for item in needed):
                    continue
            if max_minutes is not None:
                total_minutes = (row.prep_minutes or 0) + (row.cook_minutes or 0)
                if total_minutes > max_minutes:
                    continue
            if ingredient_mode == "available_only":
                required = [i for i in detail["ingredients"] if not i.get("optional")]
                if any(i.get("foodItemId") not in available_ids and not any(label in i["name"] or i["name"] in label for label in available_labels) for i in required):
                    continue
            detail["memoryMatches"] = sum((3 if hint["presence"] == "present" else 1) for i in detail["ingredients"] for hint in memory_hints if hint["presence"] != "absent" and (hint.get("foodItemId") and hint["foodItemId"] == i.get("foodItemId") or hint["label"] in i["name"]))
            detail["version"] = row.version
            result.append(detail)
        if ingredient_mode == "prefer_available":
            result.sort(key=lambda item: -item["memoryMatches"])
        return result

    @staticmethod
    def _shares(meal_types: list[str]) -> dict[str, Decimal]:
        raw = {meal: _MEAL_SHARE.get(meal, Decimal("0.33")) for meal in meal_types}
        total = sum(raw.values()) or Decimal("1")
        return {meal: value / total for meal, value in raw.items()}

    @staticmethod
    def _pick(candidates: list[dict[str, Any]], used_today: set[int], previous_ids: set[int]) -> dict[str, Any] | None:
        # 优先不重复当天、也不与前一天重复；都没有才允许重复。
        for item in candidates:
            if item["id"] not in used_today and item["id"] not in previous_ids:
                return item
        for item in candidates:
            if item["id"] not in used_today:
                return item
        return None

    @staticmethod
    def _portion(candidate: dict[str, Any], meal_target: Decimal) -> tuple[int, dict[str, Any] | None]:
        nutrition = candidate.get("nutrition") or {}
        total = nutrition.get("total") or {}
        base = nutrition.get("baseServings") or 1
        per_serving = _dec(total.get("calorie"))
        if per_serving is None or per_serving <= 0:
            return 1, None
        per_serving = per_serving / Decimal(base)
        servings = int((meal_target / per_serving).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        servings = min(max(servings, 1), _MAX_SERVINGS)
        ratio = Decimal(servings) / Decimal(base)
        snapshot = {
            key: (value * ratio).quantize(Decimal("0.01")) if value is not None else None
            for key, value in total.items()
        }
        return servings, snapshot

    # ---------------- 查询与采纳 ----------------

    async def get_plan(self, user_id: int, plan_id: int, version_number: int | None = None) -> dict[str, Any]:
        plan = await self._owned_plan(user_id, plan_id)
        version = await self._active_or_latest_version(plan)
        if version_number is not None:
            version = await self._db.scalar(select(MealPlanVersion).where(MealPlanVersion.plan_id == plan_id, MealPlanVersion.version_number == version_number))
            if version is None:
                raise BusinessError(404, "计划版本不存在")
        return await self._serialize(plan, version)

    async def list_plans(self, user_id: int, start: date | None, end: date | None) -> list[dict[str, Any]]:
        stmt = select(MealPlan).where(MealPlan.user_id == user_id)
        if start:
            stmt = stmt.where(MealPlan.end_date >= start)
        if end:
            stmt = stmt.where(MealPlan.start_date <= end)
        plans = list(await self._db.scalars(stmt.order_by(MealPlan.start_date.desc()).limit(20)))
        return [await self._serialize(plan, await self._active_or_latest_version(plan)) for plan in plans]

    async def list_versions(self, user_id: int, plan_id: int) -> list[dict[str, Any]]:
        await self._owned_plan(user_id, plan_id)
        versions = list(
            await self._db.scalars(
                select(MealPlanVersion)
                .where(MealPlanVersion.plan_id == plan_id)
                .order_by(MealPlanVersion.version_number.desc())
            )
        )
        return [
            {
                "version": row.version_number,
                "status": row.status,
                "reason": row.reason,
                "checks": row.checks,
                "adoptedAt": row.adopted_at.isoformat() if row.adopted_at else None,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in versions
        ]

    async def revise(
        self, user_id: int, plan_id: int, reason: str, event: str | None = None, *, max_minutes: int | None = None, excluded_foods: list[str] | None = None
    ) -> dict[str, Any]:
        """基于当前生效版本生成"调整草稿"：已执行/已跳过/锁定餐次原样保留，
        其余餐次重新选餐。**不覆盖生效计划**，采纳后才有新版本生效。"""
        plan = await self._locked_plan(user_id, plan_id)
        base = await self._active_or_latest_version(plan)
        if base is None:
            raise BusinessError(404, "计划版本不存在")
        base_meals = list(
            await self._db.scalars(
                select(PlanMeal)
                .where(PlanMeal.plan_version_id == base.id)
                .order_by(PlanMeal.meal_date.asc(), PlanMeal.id.asc())
            )
        )
        target = await daily_target(self._db, user_id, plan.start_date)
        constraints = dict(base.constraints or {})
        allergens = _split(constraints.get("allergens"))
        equipment = _split(constraints.get("equipment"))
        inherited_limit = constraints.get("maxPrepMinutes")
        if max_minutes is not None:
            constraints["maxPrepMinutes"] = min(max_minutes, inherited_limit) if inherited_limit else max_minutes
        candidates = await self._candidates(
            allergens, equipment, constraints.get("maxPrepMinutes"),
            constraints.get("ingredientMode") or "prefer_available", user_id,
        )
        excluded = _split(excluded_foods)
        if excluded:
            candidates = [item for item in candidates if not any(name in " ".join(i["name"] for i in item["ingredients"]) for name in excluded)]
        constraints["excludedFoods"] = list(dict.fromkeys(_split(constraints.get("excludedFoods")) + excluded))
        if constraints["excludedFoods"]:
            candidates = [item for item in candidates if not any(name in " ".join(i["name"] for i in item["ingredients"]) for name in constraints["excludedFoods"])]
        if not candidates:
            raise BusinessError(409, "当前条件下没有可替换的候选食谱")

        latest_number = await self._db.scalar(
            select(__import__("sqlalchemy").func.max(MealPlanVersion.version_number)).where(
                MealPlanVersion.plan_id == plan.id
            )
        )
        version = MealPlanVersion(
            plan_id=plan.id,
            version_number=int(latest_number or 0) + 1,
            parent_version_id=base.id,
            constraints=to_jsonable(constraints),
            input_snapshot=to_jsonable({"reason": reason, "event": event, "fromVersion": base.version_number}),
            checks=to_jsonable({"code": "OK", "keptMeals": 0, "changedMeals": 0}),
            reason=(reason or "")[:255],
            status="draft",
        )
        self._db.add(version)
        await self._db.flush()

        shares = self._shares(sorted({meal.meal_type for meal in base_meals}) or ["lunch"])
        used_today: dict[date, set[int]] = {}
        previous_ids: set[int] = set()
        kept = changed = 0
        diff: list[dict[str, Any]] = []
        for meal in base_meals:
            if meal.execution_state in {"executed", "skipped"} or meal.locked:
                self._db.add(
                    PlanMeal(
                        plan_version_id=version.id,
                        meal_date=meal.meal_date,
                        meal_type=meal.meal_type,
                        recipe_id=meal.recipe_id,
                        recipe_version=meal.recipe_version,
                        servings=meal.servings,
                        nutrition_snapshot=meal.nutrition_snapshot,
                        execution_state=meal.execution_state,
                        locked=meal.locked,
                    )
                )
                kept += 1
                continue
            used_today.setdefault(meal.meal_date, set())
            chosen = self._pick(candidates, used_today[meal.meal_date], previous_ids)
            if chosen is None:
                chosen_id, servings, snapshot = meal.recipe_id, meal.servings, meal.nutrition_snapshot
            else:
                used_today[meal.meal_date].add(chosen["id"])
                servings, snapshot = self._portion(chosen, target * shares.get(meal.meal_type, Decimal("0.33")))
                chosen_id = chosen["id"]
            self._db.add(
                PlanMeal(
                    plan_version_id=version.id,
                    meal_date=meal.meal_date,
                    meal_type=meal.meal_type,
                    recipe_id=chosen_id,
                    recipe_version=None,
                    servings=servings,
                    nutrition_snapshot=to_jsonable(snapshot),
                    execution_state="planned",
                )
            )
            if chosen_id != meal.recipe_id or servings != meal.servings:
                changed += 1
            old_recipe = await self._db.get(Recipe, meal.recipe_id) if meal.recipe_id else None
            new_recipe = await self._db.get(Recipe, chosen_id) if chosen_id else None
            diff.append({"date": meal.meal_date.isoformat(), "mealType": meal.meal_type, "from": meal.recipe_id, "to": chosen_id, "fromName": old_recipe.name if old_recipe else None, "toName": new_recipe.name if new_recipe else None})
            previous_ids = used_today[meal.meal_date]
        version.checks = to_jsonable({"code": "OK", "keptMeals": kept, "changedMeals": changed, "diff": diff})
        await self._db.commit()
        # 返回草稿版本本身，而不是生效版本：否则调用方看不到 kept/changed 的结果，
        # 只能再查 list_versions 才知道草稿长什么样。
        return await self._serialize(plan, version)

    async def adopt(self, user_id: int, plan_id: int, version_number: int) -> dict[str, Any]:
        plan = await self._locked_plan(user_id, plan_id)
        version = await self._db.scalar(
            select(MealPlanVersion).where(
                MealPlanVersion.plan_id == plan_id,
                MealPlanVersion.version_number == version_number,
            )
        )
        if version is None:
            raise BusinessError(404, "计划版本不存在")
        if plan.active_version_id == version.id:
            return await self.get_plan(user_id, plan_id)
        if version.status != "draft":
            raise BusinessError(409, "该版本已不可采纳，请刷新后重试")
        if version.checks and version.checks.get("code") != "OK":
            raise BusinessError(409, "该草稿未通过校验，不能采纳")

        if version.parent_version_id != plan.active_version_id:
            raise BusinessError(409, "生效计划已变化，请基于最新计划重新生成调整")
        if plan.active_version_id:
            old_version = await self._db.get(MealPlanVersion, plan.active_version_id)
            if old_version:
                old_version.status = "superseded"
        plan.active_version_id = version.id
        plan.status = "active"
        version.status = "adopted"
        version.adopted_at = datetime.now()
        await self._release_legacy_reservations(user_id)
        await self._db.commit()
        return await self.get_plan(user_id, plan_id)

    async def set_execution(self, user_id: int, plan_meal_id: int, state: str) -> dict[str, Any]:
        if state not in {"planned", "executed", "skipped", "unknown"}:
            raise BusinessError(400, "执行状态不合法")
        meal = await self._db.get(PlanMeal, plan_meal_id)
        if meal is None:
            raise BusinessError(404, "餐次不存在")
        version = await self._db.get(MealPlanVersion, meal.plan_version_id)
        if version is None:
            raise BusinessError(404, "餐次不存在")
        plan = await self._locked_plan(user_id, version.plan_id)
        if plan.active_version_id != version.id:
            raise BusinessError(409, "只能修改当前生效计划的餐次，请刷新")
        await self._db.refresh(meal)
        if meal.execution_state != "executed" and state == "executed":
            from app.services.inventory_service import InventoryService
            if meal.recipe_id:
                await InventoryService(self._db).mark_used(user_id, meal.recipe_id)
        meal.execution_state = state
        await self._release_legacy_reservations(user_id)
        await self._db.commit()
        return {"id": meal.id, "executionState": meal.execution_state}

    # ---------------- 偏好 ----------------

    async def get_preference(self, user_id: int) -> dict[str, Any]:
        row = await self._preference(user_id)
        if row is None:
            return {
                "allergens": [],
                "dislikes": [],
                "cuisinePreferences": [],
                "equipment": [],
                "prepTimeLimit": None,
                "budgetMode": "soft",
                "budgetValue": None,
                "city": None,
                "ingredientMode": "prefer_available",
                "version": 0,
            }
        return {
            "allergens": _split(row.allergens),
            "dislikes": _split(row.dislikes),
            "cuisinePreferences": _split(row.cuisine_preferences),
            "equipment": _split(row.equipment),
            "prepTimeLimit": row.prep_time_limit,
            "budgetMode": row.budget_mode,
            "budgetValue": row.budget_value,
            "city": row.city,
            "ingredientMode": row.ingredient_mode,
            "version": row.version,
        }

    async def save_preference(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self._preference(user_id)
        if row is None:
            row = DietPreference(user_id=user_id)
            self._db.add(row)
        elif payload.get("version") is not None and int(payload["version"]) != row.version:
            raise BusinessError(409, "偏好已更新，请刷新后重试")
        row.version = (row.version or 0) + 1
        for field, column in (
            ("allergens", "allergens"),
            ("dislikes", "dislikes"),
            ("cuisinePreferences", "cuisine_preferences"),
            ("equipment", "equipment"),
            ("prepTimeLimit", "prep_time_limit"),
            ("budgetMode", "budget_mode"),
            ("budgetValue", "budget_value"),
            ("city", "city"),
            ("ingredientMode", "ingredient_mode"),
        ):
            if field in payload and payload[field] is not None:
                value = payload[field]
                if isinstance(value, list):
                    value = ",".join(str(item).strip() for item in value if str(item).strip())
                setattr(row, column, value)
        await self._db.commit()
        return await self.get_preference(user_id)

    # ---------------- 内部 ----------------

    async def _preference(self, user_id: int) -> DietPreference | None:
        return await self._db.scalar(select(DietPreference).where(DietPreference.user_id == user_id))

    async def _locked_plan(self, user_id: int, plan_id: int) -> MealPlan:
        plan = await self._db.scalar(select(MealPlan).where(MealPlan.id == plan_id, MealPlan.user_id == user_id).with_for_update().execution_options(populate_existing=True))
        if plan is None:
            raise BusinessError(404, "计划不存在")
        return plan

    async def _owned_plan(self, user_id: int, plan_id: int) -> MealPlan:
        plan = await self._db.get(MealPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            raise BusinessError(404, "计划不存在")
        return plan

    async def _active_or_latest_version(self, plan: MealPlan) -> MealPlanVersion | None:
        if plan.active_version_id:
            version = await self._db.get(MealPlanVersion, plan.active_version_id)
            if version is not None:
                return version
        return await self._db.scalar(
            select(MealPlanVersion)
            .where(MealPlanVersion.plan_id == plan.id)
            .order_by(MealPlanVersion.version_number.desc())
            .limit(1)
        )

    async def _serialize(self, plan: MealPlan, version: MealPlanVersion | None) -> dict[str, Any]:
        meals: list[PlanMeal] = []
        if version is not None:
            meals = list(
                await self._db.scalars(
                    select(PlanMeal)
                    .where(PlanMeal.plan_version_id == version.id)
                    .order_by(PlanMeal.meal_date.asc(), PlanMeal.id.asc())
                )
            )
        recipe_names: dict[int, str] = {}
        if meals:
            ids = {meal.recipe_id for meal in meals if meal.recipe_id}
            if ids:
                for recipe in await self._db.scalars(select(Recipe).where(Recipe.id.in_(ids))):
                    recipe_names[recipe.id] = recipe.name
        active_number = None
        if plan.active_version_id:
            active_row = await self._db.get(MealPlanVersion, plan.active_version_id)
            active_number = active_row.version_number if active_row else None
        return {
            "planId": plan.id,
            "status": plan.status,
            "startDate": plan.start_date.isoformat(),
            "endDate": plan.end_date.isoformat(),
            # activeVersion 是"正在生效"的版本；versionNumber 是本响应里这份计划
            # 数据对应的版本。调整草稿场景两者不同（草稿未生效），不能混成一个字段。
            "activeVersion": active_number,
            "versionNumber": version.version_number if version else None,
            "versionStatus": version.status if version else None,
            "checks": version.checks if version else None,
            "constraints": version.constraints if version else None,
            "reason": version.reason if version else None,
            "ingredientPolicy": "light_memory",
            "nutritionCoverage": version.nutrition_coverage if version else None,
            "meals": [
                {
                    "id": meal.id,
                    "date": meal.meal_date.isoformat(),
                    "mealType": meal.meal_type,
                    "recipeId": meal.recipe_id,
                    "recipeName": recipe_names.get(meal.recipe_id),
                    "servings": meal.servings,
                    "nutrition": meal.nutrition_snapshot,
                    "executionState": meal.execution_state,
                    "locked": meal.locked,
                }
                for meal in meals
            ],
        }

    async def _release_legacy_reservations(self, user_id: int) -> None:
        # 用户选择轻量食材记忆，旧预留保留历史但不再参与可用量判断。
        await self._db.execute(update(IngredientReservation).where(
            IngredientReservation.user_id == user_id, IngredientReservation.status == "active",
        ).values(status="released"))


def _split(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]
