"""统一聊天里的做菜分支：只构建上下文，不产生任何写入副作用。

和 `ChefService` 的分工：
- `ChefService` 是 `/api/assistant/chef` 的独立入口（历史兼容），自带模型调用与降级。
- 本服务只做"**给聊天编排提供做菜素材**"：候选食谱 + 烹饪资料 + 明确缺失说明。
  它不扣配额、不写会话、不调模型——这些都归外层 `AiChatService` 统一处理，
  否则做菜会变成"第二个会扣次和记历史的子服务"（设计 §7 明确禁止）。

三条不能含糊的边界：
1. 检索不到就返回空候选，**不拿库里前三条无关菜凑数**。
2. 营养/克重只取库内值，缺失写"份量待定"，不补 0、不猜。
3. 烹饪资料域没建索引时，明确告诉模型"本轮没有资料"，而不是让它凭记忆报来源。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import knowledge_client
from app.services.recipe_service import RecipeService

log = logging.getLogger("orange-diary-ai.cooking")

MAX_CANDIDATES = 3
# 用户没说人数时按 2 人份给参考量并说明（设计 §4.1），不要求先填问卷。
DEFAULT_SERVINGS = 2
COOKING_DOMAIN = "cooking"
_KNOWLEDGE_TOP_K = 4
_TEXT_LIMIT = 600


@dataclass
class CookingContext:
    """本轮做菜可用的素材与状态。"""

    text: str = ""
    # ok（有菜谱或有资料）/ no_evidence（都没有）/ partial（只有菜谱，没有资料域）/ skipped（纯追问）
    status: str = "skipped"
    candidates: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    hits: list[knowledge_client.RagHit] = field(default_factory=list)
    index_version: str | None = None
    degraded: str | None = None


class CookingService:
    def __init__(self, db: AsyncSession, settings: Settings, user_id: int) -> None:
        self._db = db
        self._settings = settings
        self._user_id = user_id
        self._recipes = RecipeService(db)

    async def build(
        self,
        message: str,
        *,
        dish_name: str | None = None,
        ingredients: Iterable[str] | None = None,
        servings: int | None = None,
        allergens: Iterable[str] | None = None,
        excluded: Iterable[str] | None = None,
        subtype: str | None = None,
        resolved_query: bool = False,
    ) -> CookingContext:
        ingredients = [item for item in (ingredients or []) if item]
        allergens = [item for item in (allergens or []) if item]
        excluded = [item for item in (excluded or []) if item]

        if subtype == "followup" and not dish_name and not ingredients and not resolved_query:
            # 「就做第二道」「没有生抽」这类追问本身没有新的检索词汇：
            # 拿原句去检索只会召回无关菜。调用方已用会话历史补全成独立问句时
            # （resolved_query=True）就正常检索，这样追问也有依据和来源。
            return CookingContext(
                text="【本轮状态】用户是在上一轮做菜基础上追问，"
                "请沿用对话历史里已确定的菜名、人数和忌口继续指导；不要重新推荐其他菜。",
                status="skipped",
            )

        candidates = await self._candidates(dish_name, ingredients, message, allergens, excluded)
        # 未指定人数时按 2 人份折算：库里的菜可能是 4 人份，直接照抄会让用户误以为那是他的用量。
        explicit_servings = servings is not None
        target_servings = servings or DEFAULT_SERVINGS
        if candidates:
            scaled = []
            for item in candidates:
                detail = await self._recipes.scale_servings(int(item["id"]), target_servings)
                scaled.append(detail or item)
            candidates = scaled

        # 检索用完整原话：只拿菜名（"红烧肉"）去查会因信息量太少被证据门槛判为不相关，
        # 而"红烧肉怎么做"才带得动向量与 BM25 两路信号。
        knowledge = await self._knowledge(message)
        if dish_name and dish_name in message and not _wants_alternatives(message):
            # 用户**这一句**点了菜名才只保留"这道菜"的资料：cross-encoder 会把「葱爆牛肉」
            # 也判成能回答「红烧肉怎么做」（同为炖肉），混进去只会让模型串味。
            # 菜名来自历史（"不放辣"沿用红烧肉）时不能过滤：追问问的是调料替换，
            # 命中的《食材搭配与替换》不含菜名，一过滤就被当成"别的菜"删光。
            # "除了红烧还能怎么做"是在要替代方案，过滤同样会把答案要的菜全删掉。
            knowledge = _keep_dish(knowledge, dish_name)

        if not candidates and not knowledge.hits:
            return CookingContext(
                text="【本轮状态】食谱库里没有匹配这道菜的做法，也没有可引用的烹饪资料。"
                "请如实说明没有可靠做法，可以请用户换个菜名或说说手头有什么食材；不要凭记忆编一道菜、"
                "不要编造克重和来源。",
                status="no_evidence",
                degraded=knowledge.degraded,
            )

        status = "ok"
        if not knowledge.hits:
            # 只有结构化菜谱、没有烹饪资料域时，明确区分，避免模型以为"资料就是这些"。
            status = "partial"

        return CookingContext(
            text=self._render(candidates, knowledge, explicit_servings=explicit_servings),
            status=status,
            candidates=candidates,
            sources=_sources(knowledge.hits),
            hits=knowledge.hits,
            index_version=self._settings.rag_index_version,
            degraded=knowledge.degraded,
        )

    # ---------------- 候选食谱 ----------------

    async def _candidates(
        self,
        dish_name: str | None,
        ingredients: list[str],
        message: str,
        allergens: list[str],
        excluded: list[str],
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if dish_name:
            found = await self._recipes.search_for_chef(dish_name, limit=MAX_CANDIDATES)
        elif ingredients:
            # 食材模式：每个食材各查一次，按 id 去重，避免只命中最后一种食材。
            seen: set[int] = set()
            for item in ingredients:
                for row in await self._recipes.search_for_chef(item, limit=2):
                    if int(row["id"]) not in seen:
                        seen.add(int(row["id"]))
                        found.append(row)
        else:
            found = await self._recipes.search_for_chef(message, limit=MAX_CANDIDATES)

        details: list[dict[str, Any]] = []
        for row in found[: MAX_CANDIDATES * 2]:
            detail = await self._recipes.get_recipe(int(row["id"]))
            if detail is not None:
                details.append(detail)
        details = [item for item in details if not _blocked(item, allergens, excluded)]
        return details[:MAX_CANDIDATES]

    # ---------------- 烹饪资料 ----------------

    async def _knowledge(self, query: str):
        try:
            result = await knowledge_client.search(
                self._settings, query, _KNOWLEDGE_TOP_K, self._user_id, domain=COOKING_DOMAIN
            )
        except Exception as error:  # noqa: BLE001 - 资料域异常不该让整条回答失败
            log.info("[做菜] 烹饪资料检索失败：%s", error.__class__.__name__)
            return knowledge_client.RagResult.empty(degraded="retrieval_failed")
        if result is None:
            # 烹饪域还没有索引时也会走到这里：如实标注，不冒充"已检索"。
            return knowledge_client.RagResult.empty(degraded="cooking_domain_unavailable")
        return result

    # ---------------- 组装 ----------------

    def _render(
        self, candidates: list[dict[str, Any]], knowledge, *, explicit_servings: bool
    ) -> str:
        lines: list[str] = ["【候选食谱】（库内结构化数据，克重与营养缺失即为未标注）"]
        if candidates and not explicit_servings:
            lines.append(
                f"（用户没有说明人数，以下用量已按 {DEFAULT_SERVINGS} 人份折算；"
                "回答里要说明这是参考人数，人数不同需按比例调整。）"
            )
        if not candidates:
            lines.append("（食谱库没有匹配项，不要编造菜品。）")
        for index, item in enumerate(candidates, start=1):
            nutrition = item.get("nutrition") or {}
            total = nutrition.get("total") or {}
            lines.append(
                f"{index}. {item.get('name')}（{item.get('servings')} 人份，"
                f"热量 {_num(total.get('calorie'))} kcal，蛋白质 {_num(total.get('protein'))} g，"
                f"营养覆盖率 {_num(nutrition.get('coverage'))}）"
            )
            parts = []
            for ing in item.get("ingredients") or []:
                grams = ing.get("grams")
                parts.append(
                    f"{ing.get('name')} {_grams(grams)}"
                    if grams is not None
                    else f"{ing.get('name')}（份量待定）"
                )
            if parts:
                lines.append("   食材：" + "、".join(parts))
            for step in item.get("steps") or []:
                lines.append(f"   步骤 {step.get('index')}：{step.get('description')}")
            if item.get("sourceUrl"):
                lines.append(f"   来源链接：{item.get('sourceUrl')}")

        lines.append("")
        if knowledge.hits:
            lines.append("【烹饪资料】（只可引用以下片段，引用时保留 [来源: 标题]）")
            for hit in knowledge.hits:
                lines.append(f"[来源: {hit.title}] {hit.text[:_TEXT_LIMIT]}")
        else:
            lines.append(
                "【烹饪资料】（本轮没有可引用的烹饪资料：烹饪资料域尚未就绪或未命中。"
                "只能依据候选食谱作答，不要声称根据资料，也不要编造来源。）"
            )
        lines.append("")
        lines.append(
            "请按「菜名与人数 → 食材与用量 → 调料及还需准备 → 步骤（火候/时间/完成判断）→ 替代与注意事项」输出。"
            "用量只作参考，不是购买量或摄入量；缺失的写待定，不要补 0。"
        )
        return "\n".join(lines)


def _grams(value: Any) -> str:
    """克数展示成 500g 而不是 500.00g；保留非整数的两位小数。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}g"
    return f"{int(number)}g" if number.is_integer() else f"{number:g}g"


def _num(value: Any) -> str:
    """数值缺失时明确写"未标注"，不用 0 冒充。"""
    if value is None:
        return "未标注"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def _blocked(candidate: dict[str, Any], allergens: list[str], excluded: list[str]) -> bool:
    """命中过敏原或忌口就直接剔除：宁可少推荐，也不推一道用户不能吃的菜。"""
    names = " ".join(str(item.get("name") or "") for item in candidate.get("ingredients") or [])
    for word in [*allergens, *excluded]:
        word = word.strip()
        if word and word in names:
            return True
    return False


_ALTERNATIVE_MARKERS = ("还能", "还有", "别的", "其他", "其它", "换个", "换一道", "除了")


def _wants_alternatives(message: str) -> bool:
    return any(marker in (message or "") for marker in _ALTERNATIVE_MARKERS)


def _keep_dish(result, dish_name: str):
    """点名的菜只留同名资料：同门类的其它菜会把步骤带偏。"""
    total = len(result.hits)
    kept = [
        hit for hit in result.hits
        if dish_name in f"{hit.title}{hit.text}"
    ]
    if len(kept) == total:
        return result
    return replace(result, hits=kept, answerable=bool(kept))


def _sources(hits: list[knowledge_client.RagHit]) -> list[dict[str, Any]]:
    return [
        {
            "rank": hit.rank,
            "documentId": hit.document_id,
            "title": hit.title,
            "headingPath": list(hit.heading_path),
            "excerpt": hit.text[:160],
            "sourceUrl": hit.source_url,
            "needsReview": hit.needs_review,
            "domain": getattr(hit, "domain", "") or COOKING_DOMAIN,
        }
        for hit in hits
    ]
