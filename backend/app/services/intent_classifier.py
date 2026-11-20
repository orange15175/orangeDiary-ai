"""意图识别：规则优先，语义模型兜底。

单进程之后，这条链路有两个调用方——内部 HTTP 接口（`/internal/ai/v2/classify`，
保留给运维脚本与灰度对比）和聊天编排（`/api/ai/chat`）。两边必须拿到**逐字相同**的
结果，否则会出现"接口说缺克数、聊天却没追问"这种自相矛盾的表现。所以把逻辑放在
这里，接口只负责鉴权与状态码。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.intent_router import (
    build_clarification,
    clarification_for_slots,
    normalize_intents,
)
from app.services.intent_validator import normalize_semantic_result, parse_semantic_json
from app.services.model_gateway import ModelGateway

log = logging.getLogger("orange-diary-ai.intent")


def _intent_payload(result) -> dict:
    """规则与语义路径共用同一份下发协议（snake_case）。"""
    return {
        "intent": result.intent,
        "confidence": result.confidence,
        "slots": dict(result.slots),
        "missing_slots": list(result.missing_slots),
        "source": result.source,
    }


async def classify_message(
    settings: Settings,
    message: str,
    trace_id: str = "",
    history: list[str] | None = None,
) -> dict[str, Any]:
    """识别一条消息的意图。

    返回 `{"intents": [...], "reply": str | None}`：`reply` 是缺参追问文案，
    非空时调用方应直接回复它、不再调用生成模型。

    `history` 是最近几轮对话的纯文本行（不含当前问题）。只用于语义路径理解
    "第二道怎么做""没有生抽了""不放辣"这类省略句——规则层不依赖它，
    所以规则命中时行为与之前完全一致。

    抛 `ModelFailure`：语义路径不可用时由调用方决定是 502 还是降级到关键词路径。
    """
    results = normalize_intents(message)
    if results:
        return {
            "intents": [_intent_payload(result) for result in results],
            "reply": build_clarification(results),
        }

    content = message
    if history:
        # 没有历史时保持"单句分类"，避免无谓地加长提示词。
        content = f"【近期对话】\n" + "\n".join(history) + f"\n\n最新一句话：{message}"
    result = await ModelGateway(settings).execute(
        {
            "model": "intent-classification",
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        },
        trace_id,
    )
    content = (result.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
    parsed = parse_semantic_json(content)
    if parsed is None:
        # 模型没有给出可解析结果时退回追问，不静默透传空意图。
        return {
            "intents": [
                {
                    "intent": "clarification",
                    "confidence": 0.0,
                    "slots": {},
                    "missing_slots": [],
                    "source": "semantic_fallback",
                }
            ],
            "reply": None,
        }
    normalized = normalize_semantic_result(parsed)
    # 缺参时即使模型没写 reply 也要追问，避免拿空参数继续生成答案。
    reply = normalized["reply"] or clarification_for_slots(
        normalized["slots"], normalized["missing_slots"]
    )
    return {
        "intents": [
            {
                "intent": normalized["intent"],
                "confidence": normalized["confidence"],
                "slots": normalized["slots"],
                "missing_slots": normalized["missing_slots"],
                "source": "semantic",
            }
        ],
        "reply": reply,
    }
