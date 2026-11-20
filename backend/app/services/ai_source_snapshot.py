"""回答来源快照。

只存 `source_count` 时，索引一更新，旧回答就解释不了自己当初引用过什么。
这里在回答落库的同时，把本次实际送进模型的片段存一份副本（chunk_id、标题、
正文摘录、分数、索引版本）。存的是**公共文献**，不是用户自述——用户的健康
状况只留在会话表里，不进这张运维可查的表。

写入刻意做成幂等：同一条消息重复写不产生第二份快照，避免流式断线补写时
出现重复片段。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiAnswerSource
from app.services.paging import mp_page, safe_page, safe_size

# 快照比前端展示的 160 字摘录长：事后要能看明白这个片段到底说了什么。
EXCERPT_LIMIT = 1200
_MAX_PAGE_SIZE = 100


def _clip(value: str | None, limit: int = EXCERPT_LIMIT) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit] + "…"


def source_row(row: AiAnswerSource) -> dict[str, Any]:
    try:
        heading = json.loads(row.heading_path) if row.heading_path else []
    except (TypeError, ValueError):
        heading = []
    return {
        "id": row.id,
        "messageId": row.message_id,
        "traceId": row.trace_id,
        "rank": row.rank,
        "chunkId": row.chunk_id,
        "documentId": row.document_id,
        "title": row.title,
        "headingPath": heading if isinstance(heading, list) else [],
        "excerpt": row.excerpt,
        "sourceUrl": row.source_url,
        "needsReview": bool(row.needs_review),
        "score": row.score,
        "indexVersion": row.index_version,
        "knowledgeDomain": row.knowledge_domain,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


class AiSourceSnapshotService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        *,
        message_id: int,
        trace_id: str,
        hits: list[Any],
        index_version: str | None = None,
    ) -> int:
        """写入一条回答的来源快照，返回实际新增行数。

        `hits` 是 `knowledge_client.RagHit` 列表（用鸭子类型取字段，避免这层
        依赖检索实现）。按 `(message_id, rank)` 去重，重复调用是安全的。
        """
        if not hits or not message_id:
            return 0
        existing = set(
            (
                await self._db.scalars(
                    select(AiAnswerSource.rank).where(AiAnswerSource.message_id == message_id)
                )
            ).all()
        )
        added = 0
        for hit in hits:
            rank = int(getattr(hit, "rank", 0) or 0)
            if rank in existing:
                continue
            self._db.add(
                AiAnswerSource(
                    message_id=message_id,
                    trace_id=trace_id,
                    rank=rank,
                    chunk_id=getattr(hit, "chunk_id", None),
                    document_id=getattr(hit, "document_id", None),
                    title=_clip(getattr(hit, "title", None), 255),
                    heading_path=json.dumps(
                        list(getattr(hit, "heading_path", ()) or ()), ensure_ascii=False
                    ),
                    excerpt=_clip(getattr(hit, "text", None)),
                    source_url=_clip(getattr(hit, "source_url", None), 512),
                    needs_review=bool(getattr(hit, "needs_review", False)),
                    score=float(getattr(hit, "score", 0) or 0),
                    index_version=index_version,
                    knowledge_domain=getattr(hit, "domain", "") or None,
                )
            )
            existing.add(rank)
            added += 1
        if added:
            await self._db.flush()
        return added

    async def by_message(self, message_id: int) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(AiAnswerSource)
                .where(AiAnswerSource.message_id == message_id)
                .order_by(AiAnswerSource.rank.asc())
            )
        ).all()
        return [source_row(row) for row in rows]

    async def by_messages(self, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        """批量取出多条回答的快照，供历史接口一次装配，避免逐条查库。"""
        if not message_ids:
            return {}
        rows = (
            await self._db.scalars(
                select(AiAnswerSource)
                .where(AiAnswerSource.message_id.in_(message_ids))
                .order_by(AiAnswerSource.message_id.asc(), AiAnswerSource.rank.asc())
            )
        ).all()
        result: dict[int, list[dict[str, Any]]] = {mid: [] for mid in message_ids}
        for row in rows:
            if row.message_id in result:
                result[row.message_id].append(source_row(row))
        return result

    async def by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._db.scalars(
                select(AiAnswerSource)
                .where(AiAnswerSource.trace_id == trace_id)
                .order_by(AiAnswerSource.message_id.asc(), AiAnswerSource.rank.asc())
            )
        ).all()
        return [source_row(row) for row in rows]

    async def page(
        self,
        *,
        page: int,
        size: int,
        trace_id: str | None = None,
        message_id: int | None = None,
    ) -> dict[str, Any]:
        current = safe_page(page)
        limit = safe_size(size, _MAX_PAGE_SIZE)
        conditions: list[Any] = []
        if trace_id and trace_id.strip():
            conditions.append(AiAnswerSource.trace_id == trace_id.strip())
        if message_id is not None:
            conditions.append(AiAnswerSource.message_id == message_id)
        total = int(
            await self._db.scalar(
                select(func.count())
                .select_from(AiAnswerSource)
                .where(*conditions)
            )
            or 0
        )
        rows = (
            await self._db.scalars(
                select(AiAnswerSource)
                .where(*conditions)
                .order_by(AiAnswerSource.id.desc())
                .offset((current - 1) * limit)
                .limit(limit)
            )
        ).all()
        return mp_page([source_row(row) for row in rows], total=total, current=current, limit=limit)
