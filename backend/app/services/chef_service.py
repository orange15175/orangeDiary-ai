"""私人厨师：菜谱选择与烹饪指导。

定位见设计 §6.2——不是人格化智能体，而是「食谱检索 + 份量缩放 + 做法适配 +
替换建议」的能力集合。本服务只做这一件事，营养目标与多天计划归规划服务。

两条硬约束：
- **不编造**：营养、克重、来源都来自库里；缺就说明缺。
- **模型失败可降级**：没有模型或调用失败时，直接返回候选菜的结构化做法，
  不让"私人厨师"整个不可用。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.rag.schemas import DOMAIN_COOKING
from app.services import knowledge_client
from app.services.model_gateway import ModelFailure, ModelGateway
from app.services.recipe_service import RecipeService

log = logging.getLogger("orange-diary-ai.chef")

_MAX_CANDIDATES = 3
_KNOWLEDGE_TOP_K = 3


class ChefService:
    def __init__(self, db: AsyncSession, settings: Settings, user_id: int) -> None:
        self._db = db
        self._settings = settings
        self._user_id = user_id
        self._recipes = RecipeService(db)

    async def respond(
        self,
        message: str,
        *,
        servings: int | None = None,
        allergens: list[str] | None = None,
    ) -> dict[str, Any]:
        candidates = await self._candidates(message, allergens)
        if servings:
            candidates = [await self._recipes.scale_servings(item["id"], servings) for item in candidates]
        knowledge = await self._knowledge(message)
        if not candidates and not knowledge.hits:
            return {
                "reply": "这个菜我暂时没有可靠的做法或资料，不能凭空编一道菜给你。"
                "可以换个菜名，或先告诉我你手头有什么食材。",
                "recipes": [],
                "sources": [],
                "degraded": "no_candidates",
            }

        prompt = self._build_prompt(message, candidates, knowledge)
        from app.services.inventory_service import InventoryService
        memory = await InventoryService(self._db).list_known(self._user_id)
        if memory:
            prompt += "\n【食材线索，仅供推荐，非精确库存】\n" + "\n".join(f"{row['label']}：{row['hint']}" for row in memory[:10])
        reply, degraded = await self._ask_model(prompt)
        if not reply:
            reply = self._fallback_reply(candidates)
            degraded = degraded or "model_unavailable"
        return {
            "reply": reply,
            "recipes": [self._public_candidate(item) for item in candidates],
            "sources": self._sources(knowledge),
            "degraded": degraded,
        }

    # ---------------- 检索 ----------------

    async def _candidates(self, message: str, allergens: list[str] | None) -> list[dict[str, Any]]:
        # 先按整句找，再退化为找全部可用食谱；两层都不召回时返回空，让调用方如实说明。
        found = await self._recipes.search_for_chef(message, limit=_MAX_CANDIDATES)
        if not found:
            page = await self._recipes.list_recipes(page_size=_MAX_CANDIDATES)
            found = page["records"]
        details: list[dict[str, Any]] = []
        for item in found:
            detail = await self._recipes.get_recipe(int(item["id"]))
            if detail is not None:
                details.append(detail)
        if allergens:
            details = [item for item in details if not self._hits_allergen(item, allergens)]
        return details[:_MAX_CANDIDATES]

    async def _knowledge(self, message: str):
        try:
            return await knowledge_client.search(
                self._settings, message, _KNOWLEDGE_TOP_K, self._user_id, domain=DOMAIN_COOKING
            )
        except Exception:  # noqa: BLE001 - 检索失败不该让厨师整体不可用
            log.info("[私人厨师] 知识检索失败，按无资料继续")
            return knowledge_client.RagResult.empty()

    @staticmethod
    def _hits_allergen(candidate: dict[str, Any], allergens: list[str]) -> bool:
        names = " ".join(
            str(item.get("name") or "") for item in candidate.get("ingredients", [])
        )
        return any(allergen.strip() and allergen.strip() in names for allergen in allergens)

    # ---------------- 组装与调用 ----------------

    @staticmethod
    def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": candidate.get("id"),
            "name": candidate.get("name"),
            "servings": candidate.get("servings"),
            "prepMinutes": candidate.get("prepMinutes"),
            "cookMinutes": candidate.get("cookMinutes"),
            "equipment": candidate.get("equipment"),
            "nutrition": candidate.get("nutrition"),
            "sourceType": candidate.get("sourceType"),
            "sourceUrl": candidate.get("sourceUrl"),
            "ingredients": candidate.get("ingredients"),
            "steps": candidate.get("steps"),
        }

    def _build_prompt(self, message: str, candidates: list[dict[str, Any]], knowledge) -> str:
        lines = ["【用户需求】", message, "", "【候选食谱】"]
        if not candidates:
            lines.append("（候选库中没有匹配的菜，不要编造具体菜品。）")
        for index, item in enumerate(candidates, start=1):
            nutrition = item.get("nutrition") or {}
            total = nutrition.get("total") or {}
            lines.append(
                f"{index}. {item.get('name')}（{item.get('servings')} 人份，"
                f"热量 {total.get('calorie')} kcal，蛋白质 {total.get('protein')} g，"
                f"营养覆盖率 {nutrition.get('coverage')}）"
            )
            ingredients = item.get("ingredients") or []
            for step in item.get("steps", []):
                lines.append(f"   步骤 {step.get('index')}：{step.get('description')}")
            if ingredients:
                parts = []
                for ing in ingredients:
                    qty = ing.get("grams")
                    parts.append(
                        f"{ing.get('name')} {qty}g" if qty is not None else f"{ing.get('name')}（份量待定）"
                    )
                lines.append("   食材：" + "、".join(parts))
        lines.extend(["", "【营养资料】"])
        if knowledge.hits:
            for hit in knowledge.hits:
                lines.append(f"[来源: {hit.title}] {hit.text[:400]}")
        else:
            lines.append("（本轮没有检索到可引用的营养资料，不要声称根据资料。）")
        lines.extend(
            [
                "",
                "请据此回答；营养数值以候选食谱提供的为准，缺失的不要补。"
                "份量不足时说明缺什么，不要猜。",
            ]
        )
        return "\n".join(lines)

    async def _ask_model(self, prompt: str) -> tuple[str, str | None]:
        if not self._settings.deepseek_api_key:
            return "", "model_not_configured"
        try:
            result = await ModelGateway(self._settings).execute(
                {"model": "chef", "messages": [{"role": "user", "content": prompt}]},
                "",
            )
        except ModelFailure as error:
            log.info("[私人厨师] 模型调用失败：%s", error.code)
            return "", error.code
        message = (result.get("choices") or [{}])[0].get("message") or {}
        return (message.get("content") or "").strip(), None

    @staticmethod
    def _fallback_reply(candidates: list[dict[str, Any]]) -> str:
        if not candidates:
            return "暂时没有可用候选。"
        top = candidates[0]
        lines = [f"**{top.get('name')}**（{top.get('servings')} 人份）", "", "食材："]
        for ing in top.get("ingredients") or []:
            grams = ing.get("grams")
            lines.append(
                f"- {ing.get('name')}：{grams}g" if grams is not None else f"- {ing.get('name')}：份量待定"
            )
        lines.extend(["", "做法："])
        for step in top.get("steps") or []:
            lines.append(f"{step.get('index')}. {step.get('description')}")
        lines.extend(["", "（当前未连接模型，以上为库中做法原文。）"])
        return "\n".join(lines)

    @staticmethod
    def _sources(knowledge) -> list[dict[str, Any]]:
        return [
            {
                "rank": hit.rank,
                "documentId": hit.document_id,
                "title": hit.title,
                "headingPath": list(hit.heading_path),
                "excerpt": hit.text[:160],
                "sourceUrl": hit.source_url,
                "needsReview": hit.needs_review,
            }
            for hit in knowledge.hits
        ]
