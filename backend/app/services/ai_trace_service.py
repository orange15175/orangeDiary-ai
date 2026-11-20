"""问答追踪（对齐 `AiChatTraceServiceImpl`）。

追踪表只回答"这一次问答慢在哪一段"，不回答"用户问了什么"。所以：

- 只存问题前 60 字 + 哈希。前 60 字足够判断是不是同一类问题，够不上复原
  用户原话；哈希用来做聚合统计，两个都不能省。
- 保留 30 天，每 24 小时最多清理一次。每个请求都扫一遍旧数据是典型的
  "为了整洁把自己拖慢"。
- `rerank_state`（排序方式）与 `degraded`（降级原因）分开存：
  一个是"实际怎么排的"，一个是"为什么没按预期排"。合并会让排障时把
  "正常走了 RRF" 误读成"rerank 挂了"。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiChatTrace
from app.services.paging import mp_page, safe_page, safe_size

RETENTION_DAYS = 30
_PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_MAX_PAGE_SIZE = 100

_last_purge_at = 0.0


def trace_row(trace: AiChatTrace) -> dict[str, Any]:
    return {
        "id": trace.id,
        "traceId": trace.trace_id,
        "userId": trace.user_id,
        "conversationId": trace.conversation_id,
        "intent": trace.intent,
        "questionExcerpt": trace.question_excerpt,
        "questionHash": trace.question_hash,
        "sourceCount": trace.source_count,
        "retrievalMs": trace.retrieval_ms,
        "rerankState": trace.rerank_state,
        "degraded": trace.degraded,
        "knowledgeDomain": trace.knowledge_domain,
        "totalMs": trace.total_ms,
        "createdAt": trace.created_at.isoformat() if trace.created_at else None,
    }


class AiChatTraceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        *,
        trace_id: str,
        user_id: int | None,
        conversation_id: str | None,
        intent: str | None,
        question_excerpt: str | None,
        question_hash: str | None,
        source_count: int,
        retrieval_ms: int | None,
        rerank_state: str | None,
        degraded: str | None,
        total_ms: int | None,
        knowledge_domain: str | None = None,
    ) -> None:
        self._db.add(
            AiChatTrace(
                trace_id=trace_id,
                user_id=user_id,
                conversation_id=conversation_id,
                intent=intent,
                question_excerpt=question_excerpt,
                question_hash=question_hash,
                source_count=source_count,
                retrieval_ms=retrieval_ms,
                rerank_state=rerank_state,
                degraded=degraded,
                total_ms=total_ms,
                knowledge_domain=knowledge_domain,
                created_at=datetime.now(),
            )
        )
        await self._db.flush()
        await self._purge_if_due()

    async def page(
        self,
        *,
        page: int,
        size: int,
        user_id: int | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        current = safe_page(page)
        # 上限 100：追踪表按次数增长，单页拉太多只会让管理页卡住。
        limit = safe_size(size, _MAX_PAGE_SIZE)
        conditions: list[Any] = []
        if user_id is not None:
            conditions.append(AiChatTrace.user_id == user_id)
        if trace_id is not None and trace_id.strip():
            conditions.append(AiChatTrace.trace_id == trace_id.strip())

        total = await self._db.scalar(
            select(func.count()).select_from(AiChatTrace).where(*conditions)
        )
        total = int(total or 0)
        rows = (
            await self._db.scalars(
                select(AiChatTrace)
                .where(*conditions)
                .order_by(AiChatTrace.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return mp_page([trace_row(row) for row in rows], total=total, current=current, limit=limit)

    async def by_trace_id(self, trace_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(AiChatTrace)
                .where(AiChatTrace.trace_id == trace_id)
                .order_by(AiChatTrace.id.asc())
            )
        ).all()
        return [trace_row(row) for row in rows]

    async def purge_older_than(self, days: int = RETENTION_DAYS) -> int:
        cutoff = datetime.now() - timedelta(days=max(1, days))
        result = await self._db.execute(
            delete(AiChatTrace)
            .where(AiChatTrace.created_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def _purge_if_due(self) -> None:
        global _last_purge_at
        now = time.monotonic()
        if now - _last_purge_at < _PURGE_INTERVAL_SECONDS:
            return
        _last_purge_at = now
        try:
            await self.purge_older_than()
        except Exception:  # pragma: no cover - 清理失败不该影响问答
            # 清理是后台维护动作，失败就算了，下次到点再试。
            pass
