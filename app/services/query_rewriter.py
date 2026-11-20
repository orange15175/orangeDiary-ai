"""多轮对话的受限指代改写。

"那一天最多吃多少"单独检索不到任何东西——"那一天"指代的是几轮前的某天。
这里在检索前用近期对话把指代消解成独立查询，同时保留原问题；改写失败或不需要
改写时退回原问题，绝不阻断检索。

刻意"受限"：
- 只在命中最常见中文指代词时才触发，普通聊天与明确查询不调用改写模型；
- 输入只给最近几轮对话（不含当前问题本身），不做任意长上下文；
- 改写模型失败 / 超时 / 熔断一律返回原问题，退化为"未改写"，不影响主链路。
"""

from __future__ import annotations

import json
import logging
import re

from app.config import Settings
from app.services.model_gateway import ModelGateway

log = logging.getLogger("orange-diary-ai.rewrite")

# 只收"多轮对话里明确指代上下文"的触发词，不收"再/还/也"这类过宽的语气词，
# 否则"减脂还能吃什么"这种无需改写的句子也会白跑一次改写模型。
_ANAPHORA_RE = re.compile(
    r"那天|那一天|这天|这一天|那会儿|那时候|当时|刚才|刚刚|上次|上回|"
    r"这个|那个|这些|那些|这种|那种|它|它们|还有呢|然后呢|之前"
)


def needs_rewrite(message: str) -> bool:
    return bool(_ANAPHORA_RE.search(message or ""))


def _parse_query(content: str) -> str | None:
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        # 兼容模型直接输出纯文本（不带 JSON 外壳）。
        return content.strip() if content.strip() else None
    if not isinstance(data, dict):
        return None
    query = data.get("query")
    return str(query).strip() if isinstance(query, str) and query.strip() else None


async def rewrite_query(
    settings: Settings,
    message: str,
    history: list[str],
    trace_id: str = "",
    *,
    force: bool = False,
) -> str | None:
    """返回改写后的独立查询；不需要改写或改写失败时返回 None。

    `history` 是最近几轮对话的纯文本行（不含当前问题），按时间正序。

    `force=True` 用于"本句一定依赖上文"的场合（做菜的承接追问，如"第二道怎么做"
    "没有生抽了"）：这类句子不含代词，指代词表抓不到，但脱离上文同样检索不到东西。
    """
    if not message:
        return None
    if not force and not needs_rewrite(message):
        return None
    if not history:
        return None
    if not settings.deepseek_api_key:
        return None

    dialogue = "\n".join(history)
    try:
        result = await ModelGateway(settings).execute(
            {
                "model": "cooking-rewrite" if force else "query-rewrite",
                "messages": [{"role": "user", "content": f"近期对话：\n{dialogue}\n\n最新问题：{message}"}],
                "response_format": {"type": "json_object"},
            },
            trace_id,
        )
    except Exception as error:  # noqa: BLE001 - 改写失败退回原问题
        log.info("[指代改写] 失败，退回原问题：%s", error.__class__.__name__)
        return None

    rewritten = _parse_query((result.get("choices", [{}])[0].get("message", {}) or {}).get("content", ""))
    if not rewritten or rewritten.strip() == message.strip():
        # 保持原句也是结果：排查"追问为什么没命中"时需要知道是没改写还是改写无效。
        log.info("[指代改写] 保持原句：%s", message[:30])
        return None
    log.info("[指代改写] %s -> %s", message[:30], rewritten[:60])
    return rewritten.strip()
