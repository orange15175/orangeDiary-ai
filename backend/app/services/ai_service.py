"""AI 聊天编排（对齐 `AiServiceImpl`，828 行那条链路）。

一次提问要经过：配额 → 会话 → 意图 → 用户消息落库 → 缺参追问 → 预取真实数据
（含知识检索）→ 模型（必要时调工具）→ 回答落库 → 记录预览 → 追踪。

**顺序不能重排**，几处是被线上问题逼出来的：

- 配额在意图识别**之前**扣：意图识别本身也是一次模型调用，放后面等于留了个免费口子。
- 用户消息在调模型**之前**落库：模型失败时历史里要留下提问，不能连提问一起回滚。
- 检索在**开流之前**完成：流式也必须有来源，边出字边渲染引用。
- 缺参（如"吃了碗面"没有克数）先追问，不调生成模型，免得编一份能保存的错误预览。

**与 Java 的一处有意保留的差异**：非流式通道在"缺参追问"时**不退还**配额，
流式通道会退。这是 Java 自身的两处不一致（流式注释写明"追问没走生成模型，
不该占额度"，非流式那条路径漏了）。这里照原样保留——切流差分时两边剩余次数一致，
改的话必须两个通道一起改，并同步通知前端。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError
from app.core.javacompat import local_datetime_string
from app.core.limiter import APP_ZONE
from app.core.trace import get_trace_id
from app.rag.schemas import DOMAIN_NUTRITION
from app.services import ai_tools, knowledge_client, query_rewriter
from app.services.ai_conversation import ConversationService, default_conversation_id, role_name, truncate
from app.services.ai_quota import AiQuota, build_quota
from app.services.ai_source_snapshot import AiSourceSnapshotService
from app.services.ai_trace_service import AiChatTraceService
from app.services.cooking_service import CookingService
from app.services.exercise_service import ExerciseService
from app.services.food_service import FoodService
from app.services.intent_classifier import classify_message
from app.services.intent_router import clarification_for_slots, is_future_plan
from app.services.model_gateway import ModelFailure, ModelGateway

log = logging.getLogger("orange-diary-ai.chat")

# 做菜分支用的两个场景：非流式与流式分开，避免"只改了一条路径"。
COOKING_SCENES = {"cooking", "cooking-stream"}
COOKING_INTENT = "cooking"

# 后台落库任务的强引用集合：不持引用时任务可能被 GC 提前回收（CPython 的经典坑）。
_background: set[asyncio.Task] = set()

# 知识检索默认取 4 条；过多来源会稀释上下文并放大噪声。
KNOWLEDGE_TOP_K = 4
# 单条知识片段注入上下文时的字符上限，避免挤掉用户数据。
KNOWLEDGE_TEXT_LIMIT = 600
# 工具轮次上限。正常情况一轮就收敛；这里兜住"模型反复调同一个工具"的死循环。
MAX_TOOL_ROUNDS = 3
# 指代改写最多回看几轮历史；"那一天"通常只在最近几轮内，过长反而引入噪声。
REWRITE_HISTORY_LIMIT = 8

DIET_REQUEST = re.compile(
    r"(?:吃了?|喝了?|摄入了?|记录了?)\s*(\d+(?:\.\d+)?)\s*(?:克|g|公克)\s*"
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9]*)",
    re.IGNORECASE,
)


@dataclass
class Knowledge:
    """承载本次检索到的来源，供响应体（非流式）或 sources 事件（流式）回传。"""

    sources: list[dict[str, Any]] = field(default_factory=list)
    # 原始命中留给来源快照：响应里的 sources 是截断过的展示数据，不够事后解释引用。
    hits: list[knowledge_client.RagHit] = field(default_factory=list)
    index_version: str | None = None
    retrieval_ms: int = 0
    rerank_state: str | None = None
    degraded: str | None = None
    # ok / no_evidence / busy / unavailable / skipped。
    # 没有这个字段时，"确实没资料"和"检索挂了"在界面上长得一样，用户只能从模型措辞里猜。
    status: str = "skipped"
    # 本轮走的是哪个知识域（nutrition / cooking）；只用于追踪与排障，不影响措辞。
    domain: str = ""


def _contains_any(text: str, *keywords: str) -> bool:
    return any(keyword in text for keyword in keywords)


def needs_knowledge(text: str) -> bool:
    """关键词降级判定是否需要查知识库。

    只匹配"指南/标准/上限"这类循证语境；单纯问某个食物热量仍走食物库，
    否则会把"100 克米饭多少卡"也拉到文献检索上。
    """
    return _contains_any(
        text,
        "膳食指南",
        "居民膳食",
        "平衡膳食",
        "世卫",
        "WHO",
        "推荐摄入",
        "推荐量",
        "每日上限",
        "每天上限",
        "标准",
        "建议每天",
        "每天应该",
        "应该吃多少",
        "有什么好处",
        "有哪些危害",
        "注意事项",
        "营养建议",
    )


def needs_daily_summary(text: str) -> bool:
    # 只在明确的"今日汇总"语境触发，避免"鸡蛋的热量"被当成今日汇总。
    if _contains_any(text, "还能吃", "还可以吃", "还剩", "剩余"):
        return True
    return _contains_any(text, "今天", "今日", "摄入") and _contains_any(
        text, "热量", "卡路里", "大卡", "千卡"
    )


def needs_today_diet(text: str) -> bool:
    return _contains_any(text, "今天吃", "吃了什么", "饮食", "明细", "早餐", "午餐", "晚餐", "加餐")


def needs_user_profile(text: str) -> bool:
    return _contains_any(text, "目标", "推荐", "个人", "档案", "资料", "身高", "体重", "减脂", "增肌", "维持")


def resolve_needs(classification: dict, message: str) -> set[str]:
    """把意图结果映射为需要预取的数据集合；意图不可用时回退到关键词判断。"""
    needs: set[str] = set()
    intents = classification.get("intents")
    if isinstance(intents, list):
        for intent in intents:
            if not isinstance(intent, dict):
                continue
            name = str(intent.get("intent"))
            if name == "daily_summary":
                needs.add("daily_summary")
            elif name == "diet_detail":
                needs.add("today_diet")
            elif name == "food_nutrition":
                # 一般食物营养必须查食物库，不能用今日饮食回答。
                needs.add("food_nutrition")
            elif name in ("user_goal", "weight_progress"):
                needs.add("user_profile")
            elif name == "health_advice":
                # 健康建议类问题需要循证来源，走知识库检索而不是凭模型记忆。
                needs.add("knowledge")
            elif name == COOKING_INTENT:
                # 做菜走自己的分支：结构化食谱 + 烹饪资料域，不复用营养文献检索。
                needs.add("cooking")

    text = message or ""
    if needs:
        # 命中意图不等于不需要文献：「减重期间主食应该吃多少克」会被判成 weight_progress，
        # 于是只查了用户档案，模型只能凭记忆报"每公斤 2~3 克"，无法追溯到指南原文。
        if "knowledge" not in needs and needs_knowledge(text):
            needs.add("knowledge")
        return needs

    if needs_daily_summary(text):
        needs.add("daily_summary")
    if needs_today_diet(text):
        needs.add("today_diet")
    if needs_user_profile(text):
        needs.add("user_profile")
    if needs_knowledge(text):
        needs.add("knowledge")
    return needs


def _intent_names(classification: dict) -> set[str]:
    intents = classification.get("intents")
    if not isinstance(intents, list):
        return set()
    return {str(item.get("intent")) for item in intents if isinstance(item, dict)}


def is_cooking(classification: dict) -> bool:
    """本轮是否包含做菜子请求。"""
    return COOKING_INTENT in _intent_names(classification)


def pending_record_question(classification: dict) -> str | None:
    """做菜与记录同时出现时，记录缺的那一句追问。

    这类追问不能直接下发：否则用户问"晚上鸡腿怎么做"，只得到"米饭多少克？"。
    改成把追问留到做菜回答之后，一次说清两件事。
    """
    intents = classification.get("intents")
    if not isinstance(intents, list):
        return None
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        if str(intent.get("intent")) in (COOKING_INTENT, "clarification"):
            continue
        missing = intent.get("missing_slots")
        if isinstance(missing, list) and missing:
            return clarification_for_slots(dict(intent.get("slots") or {}), missing)
    return None


def clarification_reply(classification: dict) -> str | None:
    """读取意图层给出的缺参结论；有缺参时返回追问，不再进入模型生成。"""
    intents = classification.get("intents")
    if not isinstance(intents, list):
        return None
    # 做菜是可独立回答的子请求：另一句记录缺克数时不能把整个回答截成追问，
    # 具体补问交给 pending_record_question 放在做菜回答之后。
    if is_cooking(classification):
        return None
    reply_text = classification.get("reply")
    if not isinstance(reply_text, str) or not reply_text.strip():
        reply_text = None
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        missing = intent.get("missing_slots")
        if isinstance(missing, list) and missing:
            return reply_text or "请补充更具体的信息，我才能准确回答。"
        if str(intent.get("intent")) == "clarification":
            return reply_text or "请再具体描述一下你想记录或查询的内容。"
    return None


def meal_type_now() -> str:
    hour = datetime.now(APP_ZONE).hour
    if hour < 10:
        return "breakfast"
    if hour < 14:
        return "lunch"
    if hour < 18:
        return "snack"
    return "dinner"


def _first_slot_value(classification: dict, intent_name: str, slot_key: str) -> str | None:
    intents = classification.get("intents")
    if not isinstance(intents, list):
        return None
    for intent in intents:
        if not isinstance(intent, dict) or str(intent.get("intent")) != intent_name:
            continue
        slots = intent.get("slots")
        if isinstance(slots, dict) and slots.get(slot_key) is not None:
            return str(slots.get(slot_key))
    return None


# "第二道""第二个"是相对的，脱离上文没有意义，不能当成菜名去检索。
_ORDINAL_DISH_RE = re.compile(r"^第[一二三四五六七八九十两\d]+[道个样]?$")


def _needs_history_for_dish(
    dish_name: str | None, ingredients: list[str], message: str = ""
) -> bool:
    """这一句做菜问话是否必须靠上文才能检索。

    本句真的点了菜名（红烧肉）或给了食材（辣椒、五花肉）时句子自身信息足够；
    否则（"第二道怎么做""没有生抽了""不放辣"）只能先补全再检索。

    注意 `dish_name` 可能是语义模型从历史补进来的（"不放辣"也能带出"红烧肉"），
    所以要看它是否出现在**本句原文**里，而不是只看它非空。
    """
    if ingredients:
        return False
    if dish_name and not _ORDINAL_DISH_RE.match(dish_name.strip()) and dish_name in message:
        return False
    return True


def _slot_list(classification: dict, intent_name: str, slot_key: str) -> list[str]:
    """读取做菜用的列表槽位（食材/过敏原/忌口），非法值一律当空。"""
    intents = classification.get("intents")
    if not isinstance(intents, list):
        return []
    for intent in intents:
        if not isinstance(intent, dict) or str(intent.get("intent")) != intent_name:
            continue
        slots = intent.get("slots")
        if not isinstance(slots, dict):
            continue
        value = slots.get(slot_key)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def _slot_int(classification: dict, intent_name: str, slot_key: str) -> int | None:
    """读取整数槽位（例如做菜人数），拿不到合法值就返回 None。"""
    raw = _first_slot_value(classification, intent_name, slot_key)
    if raw is None:
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def intent_label(classification: dict) -> str:
    intents = classification.get("intents") if classification else None
    if isinstance(intents, list) and intents and isinstance(intents[0], dict):
        value = intents[0].get("intent")
        if value is not None:
            return str(value)
    return "-"


def _to_sources(hits: list[knowledge_client.RagHit]) -> list[dict[str, Any]]:
    """转成前端来源卡片需要的最小字段；sourceUrl 为空表示链接未核实。"""
    return [
        {
            "rank": hit.rank,
            "documentId": hit.document_id,
            "title": hit.title,
            "headingPath": list(hit.heading_path),
            "excerpt": truncate(hit.text, 160),
            "sourceUrl": hit.source_url,
            "needsReview": hit.needs_review,
            "domain": hit.domain,
        }
        for hit in hits
    ]


def _is_rerank_degraded(rerank: str | None) -> bool:
    return rerank in {"failed", "not_configured", "circuit_open"}


def _sse(event: str, data: str) -> str:
    """一条 SSE 帧；多行数据按规范逐行加 `data:` 前缀。"""
    body = "".join(f"data: {line}\n" for line in str(data).split("\n"))
    return f"event: {event}\n{body}\n"


class AiChatService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        user_id: int,
        is_admin: bool = False,
        *,
        conversation_service: ConversationService | None = None,
        quota: AiQuota | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._user_id = user_id
        self._is_admin = is_admin
        self._conversations = conversation_service or ConversationService(db, settings)
        self._quota = quota or build_quota(settings)
        # 部分回答只落一次库；见 `_persist_partial_async`。
        self._partial_persisted = False

    # ---------------- 非流式 ----------------

    async def chat(self, message: str) -> dict[str, Any]:
        started_at = time.perf_counter()
        conversation_id = default_conversation_id(self._user_id)
        # 配额在意图识别之前扣：意图识别本身也是一次模型调用。
        ticket = await self._quota.acquire(self._user_id, self._is_admin)
        try:
            conversation = await self._conversations.get_or_create(self._user_id, conversation_id)
            direct = await self._recall_previous(message, conversation)
            if direct is not None:
                await self._conversations.append_message(conversation, "user", message)
                await self._conversations.append_message(conversation, "assistant", direct)
                await self._quota.refund(ticket)
                return {"reply": direct, "conversationId": conversation_id}
            classification = await self._classify(message)
            await self._fill_amount_from_serving(classification)
            await self._conversations.append_message(conversation, "user", message)

            clarification = clarification_reply(classification)
            if clarification is not None:
                await self._conversations.append_message(conversation, "assistant", clarification)
                return {"reply": clarification, "conversationId": conversation_id}

            knowledge = Knowledge()
            context = await self._build_context(message, conversation, classification, knowledge)
            reply = await self._complete(context, self._scene(classification, stream=False))
            if reply is None or not reply.strip():
                raise BusinessError(502, "AI 返回了空回答")
            assistant = await self._conversations.append_message(conversation, "assistant", reply)
            await self._record_snapshot(assistant.id, knowledge)

            response = await self._build_response(message, reply, conversation_id, classification)
            # 来源只在真正取到知识片段时返回；没有来源就不渲染引用卡片。
            if knowledge.sources:
                response["sources"] = knowledge.sources
            # 状态始终回传：前端要能区分"没资料"和"检索不可用"。
            response["knowledgeStatus"] = knowledge.status
            await self._record_trace(
                conversation_id, message, classification, knowledge, started_at
            )
            return response
        except BusinessError:
            # 用户没拿到回答（含"AI 返回空回答"）就退还额度，避免一次故障白掉一次次数。
            await self._quota.refund(ticket)
            raise
        except Exception as error:  # noqa: BLE001 - 上游异常统一转 502
            await self._quota.refund(ticket)
            log.error("[AI chat] 失败 userId=%s error=%s", self._user_id, error.__class__.__name__)
            raise BusinessError(502, "AI 服务调用失败") from error

    # ---------------- 流式 ----------------

    async def chat_stream(self, message: str) -> AsyncIterator[str]:
        """产出 SSE 帧。调用方负责把它包成 `text/event-stream` 响应。"""
        conversation_id = default_conversation_id(self._user_id)
        conversation = await self._conversations.get_or_create(self._user_id, conversation_id)
        ticket = await self._quota.acquire(self._user_id, self._is_admin)

        try:
            direct = await self._recall_previous(message, conversation)
            if direct is None:
                classification = await self._classify(message)
                await self._fill_amount_from_serving(classification)
            await self._conversations.append_message(conversation, "user", message)
            if direct is not None:
                await self._conversations.append_message(conversation, "assistant", direct)
        except BaseException:
            await self._quota.refund(ticket)
            raise
        if direct is not None:
            await self._quota.refund(ticket)
            yield _sse("message", direct)
            yield _sse("done", conversation_id)
            return

        clarification = clarification_reply(classification)
        if clarification is not None:
            await self._conversations.append_message(conversation, "assistant", clarification)
            # 追问没有走生成模型，不该占一次额度。
            await self._quota.refund(ticket)
            yield _sse("message", clarification)
            yield _sse("done", conversation_id)
            return

        # 记录请求由数据库候选生成确认卡，确认前不写业务记录、不让模型编造消耗。
        try:
            preview = await self._build_response(message, "", conversation_id, classification)
            if preview.get("action") or preview.get("reply"):
                text = (
                    "请核对下方记录卡，确认后点击保存；目前尚未写入记录。"
                    if preview.get("action") else preview["reply"]
                )
                await self._conversations.append_message(conversation, "assistant", text)
        except BaseException:
            await self._quota.refund(ticket)
            raise
        if preview.get("action") or preview.get("reply"):
            # 同一句里还带着做菜请求时不能就此收尾：先把记录卡发出去，再继续生成做菜回答。
            # 否则"中午吃了200克米饭，晚上鸡腿怎么做"只得到一张记录卡，做菜部分被吞掉。
            if not is_cooking(classification):
                await self._quota.refund(ticket)
                yield _sse("message", text)
                if preview.get("action"):
                    yield _sse("action", _json({key: value for key, value in preview.items() if key != "reply"}))
                yield _sse("done", conversation_id)
                return
            # 做菜场景是只读的（不带 tools），继续生成不会二次产出记录卡。
            yield _sse("message", text)
            if preview.get("action"):
                yield _sse("action", _json({key: value for key, value in preview.items() if key != "reply"}))

        # 检索在开流之前完成：命中时先发一条 sources 事件，前端可在出字过程中就渲染引用。
        knowledge = Knowledge()
        context = await self._build_context(message, conversation, classification, knowledge)
        yield _sse("knowledge", _json({"status": knowledge.status}))
        if knowledge.sources:
            yield _sse("sources", _json(knowledge.sources))

        reply_parts: list[str] = []
        persisted = False
        scene = self._scene(classification, stream=True)
        try:
            messages: list[dict[str, Any]] = [{"role": "user", "content": context}]
            for _ in range(MAX_TOOL_ROUNDS):
                tool_calls: list[dict[str, Any]] = []
                async for token in self._stream_round(messages, tool_calls, scene):
                    reply_parts.append(token)
                    yield _sse("message", token)
                if not tool_calls:
                    break
                messages = await self._apply_tool_calls(messages, tool_calls)

            text = "".join(reply_parts)
            if text.strip():
                assistant = await self._conversations.append_message(conversation, "assistant", text)
                await self._record_snapshot(assistant.id, knowledge)
            persisted = True
            yield _sse("done", conversation_id)
        except ModelFailure as error:
            await self._quota.refund(ticket)
            log.error("[AI流式] 模型调用失败 userId=%s error=%s", self._user_id, error.code)
            yield _sse("error", "AI 服务调用失败")
        except asyncio.CancelledError:
            # 用户点"停止"或断网：已经生成的部分也要落库，
            # 否则历史里只留下一条提问没有回答，刷新后看起来像丢消息。
            self._persist_partial_async(conversation.id, reply_parts, persisted)
            raise
        except Exception as error:  # noqa: BLE001 - 未预期异常也要保住已生成的部分
            self._persist_partial_async(conversation.id, reply_parts, persisted)
            log.error("[AI流式] 失败 userId=%s error=%s", self._user_id, error.__class__.__name__)
            yield _sse("error", "AI 服务调用失败")
        finally:
            if not persisted:
                # 生成器被 aclose()（客户端断开）时会在 yield 处抛 GeneratorExit，
                # 这时**不能 await**（会报"async generator ignored GeneratorExit"），
                # 所以只投递一个独立会话的后台写。
                self._persist_partial_async(conversation.id, reply_parts, persisted)

    def _persist_partial_async(
        self, conversation_id: int, parts: list[str], already_persisted: bool
    ) -> None:
        """把已生成的部分回答交给后台任务落库。

        三条调用路径（取消、未预期异常、生成器被关闭）可能连续触发，
        用 `_partial_persisted` 保证只写一次，否则同一条回答会在历史里出现两遍。
        """
        if already_persisted or self._partial_persisted:
            return
        text = "".join(parts)
        if not text.strip():
            return
        self._partial_persisted = True
        try:
            task = asyncio.create_task(
                self._conversations.append_message_by_id(conversation_id, "assistant", text)
            )
        except RuntimeError:  # 事件循环已关闭（进程退出路径）
            return
        _background.add(task)
        task.add_done_callback(_background.discard)

    async def _stream_round(
        self,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        scene: str = "diary-chat-stream",
    ) -> AsyncIterator[str]:
        """跑一轮流式生成：产出文本增量，把 tool_calls 攒进传入的列表。"""
        body: dict[str, Any] = {"model": scene, "messages": messages, "stream": True}
        if scene not in COOKING_SCENES:
            # 做菜场景是只读的：不给工具，模型就不可能"顺手"写记录。
            body["tools"] = ai_tools.tool_schemas()
            body["tool_choice"] = "auto"
        chunks = await ModelGateway(self._settings).execute(body, "")

        builders: dict[int, dict[str, Any]] = {}
        async for frame in chunks:
            value = frame[len("data: ") :].strip()
            if value == "[DONE]":
                break
            try:
                chunk = _json_loads(value)
            except ValueError:
                continue
            error = chunk.get("error") if isinstance(chunk, dict) else None
            if error:
                raise ModelFailure("upstream_stream_error")
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text
                for item in delta.get("tool_calls") or []:
                    index = int(item.get("index") or 0)
                    builder = builders.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if item.get("id"):
                        builder["id"] = item["id"]
                    function = item.get("function") or {}
                    if function.get("name"):
                        builder["name"] = function["name"]
                    if function.get("arguments"):
                        builder["arguments"] += function["arguments"]

        for index in sorted(builders):
            builder = builders[index]
            if not builder["name"]:
                continue
            tool_calls.append(
                {
                    "id": builder["id"] or f"call_{index}",
                    "type": "function",
                    "function": {"name": builder["name"], "arguments": builder["arguments"] or "{}"},
                }
            )

    # ---------------- 模型调用（非流式 + 工具轮次） ----------------

    async def _complete(self, context: str, scene: str = "diary-chat") -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": context}]
        for _ in range(MAX_TOOL_ROUNDS):
            body: dict[str, Any] = {"model": scene, "messages": messages}
            if scene not in COOKING_SCENES:
                # 非流式的做菜回答同样不给工具；只读场景传 tools 会被网关直接拒掉。
                body["tools"] = ai_tools.tool_schemas()
                body["tool_choice"] = "auto"
            result = await ModelGateway(self._settings).execute(body, "")
            message = (result.get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls") or []
            if not calls:
                return message.get("content") or ""
            messages = await self._apply_tool_calls(messages, calls, content=message.get("content"))
        # 轮次用尽仍未收敛：最后再要一次纯文本回答，不让用户看到空白。
        result = await ModelGateway(self._settings).execute(
            {"model": scene, "messages": messages}, ""
        )
        message = (result.get("choices") or [{}])[0].get("message") or {}
        return message.get("content") or ""

    async def _apply_tool_calls(
        self,
        messages: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        content: str | None = None,
    ) -> list[dict[str, Any]]:
        """把工具结果拼回消息列表，进入下一轮。"""
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": content or "",
            "tool_calls": calls,
        }
        next_messages = [*messages, assistant]
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            result = await ai_tools.call_tool(name, self._db, self._user_id)
            next_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result,
                }
            )
        return next_messages

    # ---------------- 意图与上下文 ----------------

    def _scene(self, classification: dict, *, stream: bool) -> str:
        """按意图选场景。做菜必须走只读场景，且流式/非流式两条路径一起改。"""
        if is_cooking(classification):
            return "cooking-stream" if stream else "cooking"
        return "diary-chat-stream" if stream else "diary-chat"

    async def _fill_amount_from_serving(self, classification: dict) -> None:
        """把"2 个鸡蛋""1 碗米饭"按食物库自带的份量映射换算成克。

        库里已经有 1 个鸡蛋 = 50g 这类映射时还去追问"大约多少克"，等于让用户
        做一次无意义的换算。没有映射就保持追问，绝不按经验值猜。
        """
        intents = classification.get("intents")
        if not isinstance(intents, list):
            return
        for intent in intents:
            if not isinstance(intent, dict) or str(intent.get("intent")) != "food_record":
                continue
            slots = intent.get("slots")
            if not isinstance(slots, dict) or slots.get("amount_g") is not None:
                continue
            name = str(slots.get("food_name") or "").strip()
            unit = str(slots.get("unit") or "").strip()
            quantity = slots.get("quantity")
            if not name or not unit or quantity is None:
                continue
            grams = await FoodService(self._db).grams_for_serving(name, float(quantity), unit)
            if grams is None:
                continue
            slots["amount_g"] = grams
            # 换算成功后不能再问克数，否则前端会出现"已经能记录却还在追问"。
            intent["missing_slots"] = [
                item for item in (intent.get("missing_slots") or []) if item != "amount_g"
            ]
            log.info("[记录] %s%s%s 按份量映射换算为 %sg", quantity, unit, name, grams)

    async def _classify(self, message: str) -> dict[str, Any]:
        """意图识别不可用时返回空结果，由关键词降级路径兜底。"""
        if not message or not message.strip():
            return {}
        try:
            # 短追问（"不放辣""没有生抽"）单看一句无法判断，必须带上上文。
            history = await self._history_lines(message)
            return await classify_message(self._settings, message, history=history)
        except Exception as error:  # noqa: BLE001 - 意图层失败必须降级而不是 502
            log.warning("[AI意图] 不可用，按关键词降级：%s", error.__class__.__name__)
            return {}

    async def _build_context(
        self,
        message: str,
        conversation,
        classification: dict,
        knowledge: Knowledge,
    ) -> str:
        context = await self._conversations.build_context(conversation)
        history = await self._history_lines(message)
        user_data = await self._collect_user_data(message, classification, knowledge, history)
        pending = pending_record_question(classification) if is_cooking(classification) else None
        if pending:
            # 复合请求：做菜照常回答，记录缺的那一项放在末尾补问，不让用户问两次。
            user_data = (user_data or "") + (
                "\n【同一句话里的待补记录】用户还有一个饮食记录缺少数量，需要补问："
                + pending
                + "\n请在完整回答做菜之后，用一句话补问这个数量；不要因此拒答做菜，也不要替用户填一个估计值。"
            )
        if not user_data.strip():
            return context
        return (
            context
            + "\n\n【本次问题需要优先使用的信息】\n"
            + "\n【用户真实数据】\n"
            + user_data
            + "\n"
            + "\n请优先依据以上信息回答本次用户问题，不要重新输出欢迎语。"
        )

    async def _recall_previous(self, message: str, conversation) -> str | None:
        text = re.sub(r"[\s，。？！?！]", "", message)
        if not re.fullmatch(r"(?:我)?(?:上一个问题|上一条(?:问题|消息)|上次问的(?:问题)?|刚才问的(?:问题)?)(?:是|是什么|是啥|是什么来着|啥|什么)?", text) and text not in {"我刚才问了什么", "我刚才问了啥"}:
            return None
        previous = await self._conversations.previous_user_message(conversation)
        return f"你上一条消息是：{previous}" if previous is not None else "当前会话中还没有更早的用户消息。"

    async def _history_lines(self, current_message: str) -> list[str]:
        """取最近几轮对话（不含当前问题），供指代改写消解上下文。"""
        rows = await self._conversations.recent_messages(self._user_id, REWRITE_HISTORY_LIMIT + 1)
        # 仅移除刚追加的当前用户消息；之前重复发送的相同文字仍属于历史。
        prior = rows[:-1] if rows and rows[-1].role == "user" and rows[-1].content == current_message else rows
        if not prior:
            return []
        return [f"[{role_name(row.role)}] {truncate(row.content, 200)}" for row in prior]

    async def _collect_user_data(
        self, message: str, classification: dict, knowledge: Knowledge, history: list[str]
    ) -> str:
        needs = resolve_needs(classification, message)
        blocks: list[str] = []

        if "daily_summary" in needs:
            blocks.append(await ai_tools.call_tool(ai_tools.TOOL_DAILY_SUMMARY, self._db, self._user_id))
        if "today_diet" in needs:
            blocks.append(await ai_tools.call_tool(ai_tools.TOOL_TODAY_DIET, self._db, self._user_id))
        if "user_profile" in needs:
            blocks.append(await ai_tools.call_tool(ai_tools.TOOL_USER_PROFILE, self._db, self._user_id))
        if "food_nutrition" in needs:
            block = await self._food_nutrition(classification)
            if block:
                blocks.append(block)
        if "cooking" in needs:
            block = await self._cooking_context(message, classification, knowledge, history)
            if block:
                blocks.append(block)
        if "knowledge" in needs:
            block = await self._append_knowledge(message, knowledge, history)
            if block:
                blocks.append(block)

        return "\n\n".join(block for block in blocks if block and block.strip())

    async def _cooking_context(
        self, message: str, classification: dict, knowledge: Knowledge, history: list[str]
    ) -> str:
        """做菜分支的上下文：结构化食谱 + 烹饪资料域，不碰营养文献。

        检索结果同时写回 `knowledge`，让来源卡片和追踪记录与截图/引用一致。
        """
        dish_name = _first_slot_value(classification, COOKING_INTENT, "dish_name")
        if dish_name and _ORDINAL_DISH_RE.match(dish_name.strip()):
            # "第二道"不是菜名：留着会被同菜过滤当关键词用，把检索命中全删光。
            dish_name = None
        ingredients = _slot_list(classification, COOKING_INTENT, "ingredients")
        # 语义分类会把上文里的食材带进这句的槽位（"不放辣"也能拿到辣椒/五花肉/鸡腿），
        # 于是"本句自带食材"的假象让追问跳过改写、又拿原句去检索，必然零命中。
        # 只认本句真的出现过的食材。
        if ingredients and not any(item in message for item in ingredients):
            ingredients = []
        # 承接型追问（"第二道怎么做""没有生抽了""不放辣"）本身不含菜名，直接拿原句检索
        # 必然零命中，模型只能凭记忆编步骤。先用上文补成"宫保鸡丁没有生抽怎么办"再检索。
        query = message
        if _needs_history_for_dish(dish_name, ingredients, message):
            query = (
                await query_rewriter.rewrite_query(
                    self._settings, message, history, force=True
                )
                or message
            )
        service = CookingService(self._db, self._settings, self._user_id)
        options = {
            "dish_name": dish_name,
            "ingredients": ingredients,
            "servings": _slot_int(classification, COOKING_INTENT, "servings"),
            "allergens": _slot_list(classification, COOKING_INTENT, "allergens"),
            "excluded": _slot_list(classification, COOKING_INTENT, "excluded_ingredients"),
            "subtype": _first_slot_value(classification, COOKING_INTENT, "subtype"),
        }
        context = await service.build(
            query,
            # 追问句被上文补全过就照常检索；没补全时让 CookingService 沿用会话历史。
            resolved_query=query != message,
            **options,
        )
        if not context.hits and query != message:
            # 补全会猜菜（"不放辣"被补成"宫保鸡丁不放辣"，而用户其实在说忌口）。
            # 猜错就没有命中；此时按"原句 → 本句已确认的菜名"依次重试：
            # 忌口与调料替换资料通常不含菜名，按原句能命中；纯指代句则靠菜名兜底。
            # subtype 置空是为了绕开"追问不检索"的早退。
            retries = [message]
            if dish_name and dish_name in query:
                retries.append(f"{dish_name}怎么做")
            for attempt in retries:
                context = await service.build(attempt, **{**options, "subtype": None})
                if context.hits:
                    break
        knowledge.domain = "cooking"
        if context.hits:
            knowledge.sources = context.sources
            knowledge.hits = context.hits
            knowledge.index_version = context.index_version
            knowledge.status = "ok"
        elif knowledge.status == "skipped":
            # 没有烹饪资料时如实标"没有依据"；不能因为菜谱库有数据就说资料检索成功。
            knowledge.status = "no_evidence"
        if context.degraded:
            knowledge.degraded = truncate(context.degraded, 120)
        return context.text

    async def _food_nutrition(self, classification: dict) -> str:
        """一般食物营养走食物库，不依赖通用文献或旧对话摘要。"""
        food_name = _first_slot_value(classification, "food_nutrition", "food_name")
        if not food_name or not food_name.strip():
            return ""
        match = await FoodService(self._db).match_food(food_name)
        candidates = match.get("candidates") or []
        if not candidates:
            return (
                f"【食物库】未找到「{food_name}」，不要编造该食物的营养数值，"
                "可提示用户换一个常见名称。"
            )
        lines = ["【食物库营养数据（每100克）】"]
        for candidate in candidates[:3]:
            lines.append(
                f"- {candidate.get('name')}：热量 {candidate.get('caloriePer100g')} 千卡"
                f"，蛋白质 {candidate.get('proteinPer100g')} 克"
                f"，脂肪 {candidate.get('fatPer100g')} 克"
                f"，碳水 {candidate.get('carbPer100g')} 克"
            )
        return "\n".join(lines)

    async def _append_knowledge(self, message: str, knowledge: Knowledge, history: list[str]) -> str:
        """检索知识库并写入上下文。

        三种情况必须区分：服务不可用、知识库无相关证据、取到证据。
        前两种都要明确告知模型"没有依据"，避免它用常识补出具体数字。
        """
        # 多轮追问里的指代先消解成独立查询再检索；改写失败退回原问题，不阻断。
        query = await query_rewriter.rewrite_query(self._settings, message, history) or message
        if query != message:
            log.info("[AI知识检索] 指代改写：%r -> %r", message, query)
        result = await knowledge_client.search(
            self._settings, query, KNOWLEDGE_TOP_K, self._user_id, domain=DOMAIN_NUTRITION
        )
        if result is None:
            knowledge.status = "unavailable"
            return (
                "\n【知识库资料】\n本次未取到知识库资料（检索服务不可用）。"
                "不要声称“根据资料”，涉及指南数值时如不确定请说明无法核实。\n"
            )
        if result.busy:
            knowledge.status = "busy"
            # 排队满被限流 ≠ 知识库没有内容：让模型说明"稍后再试"，而不是断言没有依据。
            return (
                "\n【知识库资料】\n本次检索排队已满，暂时取不到资料（不代表知识库没有相关内容）。"
                "请告知用户稍后再问，不要编造具体克数、百分比或条目编号。\n"
            )
        if not result.answerable or not result.hits:
            knowledge.status = "no_evidence"
            return (
                "\n【知识库资料】\n知识库未检索到与该问题相关的内容。"
                "请如实说明没有找到可靠依据，不要编造具体克数、百分比或条目编号。\n"
            )

        knowledge.status = "ok"
        knowledge.domain = DOMAIN_NUTRITION
        knowledge.sources = _to_sources(result.hits)
        knowledge.hits = result.hits
        knowledge.index_version = self._settings.rag_index_version
        knowledge.retrieval_ms = result.took_ms
        # 这里要的是"排序方式"，不是"缓存命中情况"。
        knowledge.rerank_state = truncate(result.rerank or "unknown", 60)
        knowledge.degraded = truncate(result.degraded, 120) if result.degraded else None
        log.info(
            "[AI知识检索] 命中 %d 条, rerank=%s, cache=%s, tookMs=%d",
            len(result.hits),
            knowledge.rerank_state,
            result.cache,
            result.took_ms,
        )

        lines = ["\n【知识库资料】（已按相关度排序，回答中的数值必须与此处一致）"]
        for hit in result.hits:
            head = f"- [{hit.rank}] 《{hit.title}》"
            if hit.heading_path:
                head += " · " + " > ".join(hit.heading_path)
            lines.append(head)
            lines.append(truncate(hit.text, KNOWLEDGE_TEXT_LIMIT))
        # 只有真正的降级（失败/未配置/熔断）才提示"未经重排"，正常排序时不打扰模型。
        if _is_rerank_degraded(result.rerank):
            lines.append("（提示：来源排序未经重排模型校准，仅按混合召回分数排序。）")
        return "\n".join(lines) + "\n"

    # ---------------- 记录预览 ----------------

    async def _build_response(
        self,
        message: str,
        reply: str,
        conversation_id: str,
        classification: dict,
    ) -> dict[str, Any]:
        intents = classification.get("intents")
        if isinstance(intents, list):
            for intent in intents:
                if not isinstance(intent, dict):
                    continue
                slots = intent.get("slots")
                if not isinstance(slots, dict):
                    continue
                if (
                    str(intent.get("intent")) == "food_record"
                    and slots.get("food_name") is not None
                    and slots.get("amount_g") is not None
                ):
                    return await self._diet_preview(
                        str(slots["food_name"]),
                        _decimal(slots["amount_g"]),
                        reply,
                        conversation_id,
                    )
                if (
                    str(intent.get("intent")) == "exercise_record"
                    and slots.get("exercise_name") is not None
                    and slots.get("duration_min") is not None
                    # "我准备跑步30分钟"还没发生：语义模型判成记录也不能出卡。
                    and not is_future_plan(message)
                ):
                    return await self._exercise_preview(
                        str(slots["exercise_name"]),
                        _decimal(slots["duration_min"]),
                        reply,
                        conversation_id,
                    )

        match = DIET_REQUEST.search(message or "")
        if match is None:
            return {"reply": reply, "conversationId": conversation_id}
        return await self._diet_preview(
            match.group(2), _decimal(match.group(1)), reply, conversation_id
        )

    async def _diet_preview(
        self, food_name: str, grams, reply: str, conversation_id: str
    ) -> dict[str, Any]:
        match = await FoodService(self._db).match_food(food_name)
        candidates = match.get("candidates") or []
        if not candidates:
            return {"reply": reply, "conversationId": conversation_id}
        preview = {
            "mealType": meal_type_now(),
            "quantityG": grams,
            "recordTime": local_datetime_string(datetime.now(APP_ZONE)),
            "candidates": [
                {
                    "id": candidate.get("id"),
                    "name": candidate.get("name"),
                    "caloriePer100g": candidate.get("caloriePer100g"),
                    "proteinPer100g": candidate.get("proteinPer100g"),
                    "fatPer100g": candidate.get("fatPer100g"),
                    "carbPer100g": candidate.get("carbPer100g"),
                }
                for candidate in candidates
            ],
            "selectedFoodId": candidates[0].get("id"),
        }
        return {
            "reply": reply,
            "conversationId": conversation_id,
            "action": "confirm_diet_record",
            "recordPreview": preview,
        }

    async def _exercise_preview(
        self, exercise_name: str, minutes, reply: str, conversation_id: str
    ) -> dict[str, Any]:
        if minutes is None or minutes <= 0:
            return {"reply": reply, "conversationId": conversation_id}
        matches = await ExerciseService(self._db).search_items(exercise_name, 5)
        if not matches:
            return {
                "reply": f"没有找到「{exercise_name}」对应的运动项目，请换一个名称再试。",
                "conversationId": conversation_id,
            }
        preview = {
            "durationMin": minutes,
            "recordTime": local_datetime_string(datetime.now(APP_ZONE)),
            "candidates": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "caloriePerUnit": item.get("caloriePerUnit"),
                    "unitType": item.get("unitType"),
                    "metValue": item.get("metValue"),
                }
                for item in matches
            ],
            "selectedExerciseId": matches[0].get("id"),
        }
        text = (
            "已找到「" + str(matches[0].get("name")) + "」，请确认时长后保存。"
            if not reply or not reply.strip()
            else reply
        )
        return {
            "reply": text,
            "conversationId": conversation_id,
            "action": "confirm_exercise_record",
            "exercisePreview": preview,
        }

    # ---------------- 快照与追踪 ----------------

    async def _record_snapshot(self, message_id: int, knowledge: Knowledge) -> None:
        """把本次回答引用的片段存一份副本。

        best-effort：快照写失败不能把已经生成好的回答变成 500。索引更新后
        旧回答解释不了引用是"功能缺失"，但为了它把用户已经拿到的回答回滚
        是更糟的选择。
        """
        if not knowledge.hits:
            return
        try:
            added = await AiSourceSnapshotService(self._db).record(
                message_id=message_id,
                trace_id=get_trace_id() or "-",
                hits=knowledge.hits,
                index_version=knowledge.index_version,
            )
            if added:
                await self._db.commit()
        except Exception as error:  # noqa: BLE001 - 快照是事后解释，不能影响回答
            await self._db.rollback()
            log.warning("[AI来源快照] 写入失败（不影响回答）error=%s", error.__class__.__name__)

    async def _record_trace(
        self,
        conversation_id: str,
        message: str,
        classification: dict,
        knowledge: Knowledge,
        started_at: float,
    ) -> None:
        """写一条问答追踪。

        刻意做成 best-effort：追踪失败绝不能把已经生成好的回答变成错误。
        不落问题全文与回答正文，只存前 20 字 + 哈希，避免个人健康内容进入运维表。
        """
        try:
            text = message or ""
            await AiChatTraceService(self._db).record(
                trace_id=get_trace_id() or "-",
                user_id=self._user_id,
                conversation_id=conversation_id,
                intent=intent_label(classification),
                question_excerpt=text[:20],
                question_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                source_count=len(knowledge.sources),
                retrieval_ms=min(knowledge.retrieval_ms, 2_147_483_647),
                rerank_state=knowledge.rerank_state,
                degraded=knowledge.degraded,
                total_ms=min(int((time.perf_counter() - started_at) * 1000), 2_147_483_647),
                knowledge_domain=knowledge.domain or None,
            )
            await self._db.commit()
        except Exception as error:  # noqa: BLE001 - 追踪是运维数据，不能影响回答
            log.warning("[AI追踪] 写入失败（不影响回答）error=%s", error.__class__.__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))
