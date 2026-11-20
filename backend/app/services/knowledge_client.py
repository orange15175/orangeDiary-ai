"""知识检索的进程内客户端。

Java 侧这里是 `PythonRagClient`——一次 HTTP 往返到 Python 的 `/internal/rag/v1/search`。
单进程后没必要再自己调自己，但**返回结构必须保持一致**：`answerable / busy / degraded /
rerank` 这几个字段直接决定聊天怎么措辞（"没有资料" vs "检索不可用" vs "稍后再试"），
也在追踪表里落库。所以这里保留与 Java 同名的字段语义，只把传输层换成本地调用。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.rag.circuit import CircuitOpenError
from app.rag.embedding.ollama import EmbeddingError
from app.rag.limiter import BusyError
from app.rag.retrieval.vector import VectorStoreError
from app.rag.runtime import limiter_for, service_for
from app.rag.schemas import DOMAIN_NUTRITION
from app.rag.service import RagNotReady

log = logging.getLogger("orange-diary-ai.rag.chat")


@dataclass(frozen=True)
class RagHit:
    chunk_id: str
    document_id: str
    title: str
    heading_path: tuple[str, ...]
    text: str
    source_url: str
    needs_review: bool
    score: float
    vector_score: float
    rank: int
    # nutrition / cooking：来源卡片与追踪据此区分域，避免菜谱冒充膳食指南。
    domain: str = ""


@dataclass(frozen=True)
class RagResult:
    """一次检索的结果。

    `rerank` 是**排序方式**（`local:bge-reranker-base` / `not_configured` / `failed` /
    `circuit_open`），`degraded` 是完整降级说明。两者不能混用：早先把 cache 写进
    rerank 字段，管理页的"重排状态"其实显示的是缓存命中情况。
    """

    answerable: bool
    hits: list[RagHit] = field(default_factory=list)
    rerank: str | None = None
    degraded: str | None = None
    took_ms: int = 0
    cache: str = "miss"
    busy: bool = False

    @classmethod
    def empty(cls, degraded: str | None = None) -> "RagResult":
        return cls(answerable=False, hits=[], rerank=None, degraded=degraded, took_ms=0, cache="bypass")

    @classmethod
    def saturated(cls, retry_after_seconds: float) -> "RagResult":
        """容量不足被限流：和"知识库没有依据"必须区分，前者应提示稍后重试。"""
        return cls(
            answerable=False,
            hits=[],
            rerank=None,
            degraded=f"busy:retry_after_ms={int(retry_after_seconds * 1000)}",
            took_ms=0,
            cache="bypass",
            busy=True,
        )


def _merge_degraded(detail: dict) -> str | None:
    """把 degraded 对象拍平成 `k=v; k=v`，与 Java 的拼接顺序一致。"""
    if not detail:
        return None
    return "; ".join(f"{key}={value}" for key, value in detail.items())


def _to_hits(outcome) -> list[RagHit]:
    return [
        RagHit(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            title=hit.title,
            heading_path=tuple(hit.heading_path),
            text=hit.text,
            source_url=hit.source_url,
            needs_review=hit.needs_review,
            score=hit.score,
            vector_score=hit.vector_score,
            rank=hit.rank,
            domain=getattr(hit, "domain", "") or "",
        )
        for hit in outcome.hits
    ]


async def search(
    settings: Settings,
    query: str,
    top_k: int,
    user_id: int | None = None,
    *,
    domain: str | None = DOMAIN_NUTRITION,
) -> RagResult | None:
    """执行一次检索。

    返回 `None` 表示检索服务不可用（关闭 / 熔断 / 索引未就绪 / 上游故障），
    调用方据此告诉模型"没有依据"，而不是当成"知识库确实没内容"。

    `domain` 由**服务端**指定（例如做菜固定 `cooking`、营养文献固定 `nutrition`），
    不收前端传参：让调用方随便选集合等于把索引结构暴露给外部。
    缺省是 `nutrition`——索引里同时有菜谱，不指定域就等于把两个域混在一起搜。
    """
    if not query or not query.strip():
        return RagResult.empty()
    if not settings.rag_enabled:
        # Java 侧 RAG_ENABLED=false 时内部接口直接 503，客户端拿到的是"不可用"。
        return None

    # 域不同则 BM25 索引与向量过滤条件都不同，必须各自持有实例；
    # 靠每次改全局 Settings 切域会反复关闭/重建运行时。
    service = service_for(settings, domain)
    limiter = limiter_for(settings)
    user_key = str(user_id) if user_id is not None else None
    try:
        # 名额在工作线程里释放：客户端断开时协程会被取消，但线程还在跑，
        # 若按协程的 finally 放行，实际并发会超过上限。
        outcome = await asyncio.to_thread(
            limiter.run, user_key, lambda: service.search(query, max(1, top_k), None)
        )
    except BusyError as error:
        log.info("[AI知识检索] 容量不足被限流, retryAfterMs=%d", int(error.retry_after_seconds * 1000))
        return RagResult.saturated(error.retry_after_seconds)
    except (CircuitOpenError, RagNotReady, EmbeddingError, VectorStoreError) as error:
        log.warning("[AI知识检索] 检索不可用已降级: %s", error.__class__.__name__)
        return None
    except ValueError as error:
        log.warning("[AI知识检索] 参数被拒: %s", error)
        return None
    except Exception as error:  # noqa: BLE001 - 检索失败不能拖垮聊天
        log.warning("[AI知识检索] 调用异常，已降级为无资料回答: %s", error.__class__.__name__)
        return None

    degraded = outcome.degraded or {}
    # Java 的 degraded 是对象：rerank 成功时 {"rerank":"local:xxx"}，失败时才带 reason。
    # 早先只取 reason，导致"排序正常"和"没有降级信息"都是 null。
    rerank = None if degraded.get("rerank") is None else str(degraded.get("rerank"))
    took_ms = int((outcome.timings or {}).get("total_ms") or 0)
    return RagResult(
        answerable=bool(outcome.answerable),
        hits=_to_hits(outcome),
        rerank=rerank,
        degraded=_merge_degraded(degraded),
        took_ms=took_ms,
        cache=outcome.cache or "miss",
        busy=False,
    )
