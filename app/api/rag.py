"""知识库检索接口，供 Java 端在需要循证建议时调用。

只做检索，不生成答案：回答由 Java 侧的模型调用完成，本接口负责给出可追溯的来源。
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.rag.circuit import CircuitOpenError
from app.rag.embedding.ollama import EmbeddingError
from app.rag.limiter import BusyError, RagLimiter
from app.rag.rerank import RerankError, build_reranker
from app.rag.retrieval.vector import VectorStoreError
from app.rag.service import RagNotReady, RetrievalService

log = logging.getLogger("orange-diary-ai.rag")

router = APIRouter(prefix="/internal/rag/v1", tags=["知识检索"])

_MAX_QUERY_CHARS = 500
_MAX_TOP_K = 20

_service: RetrievalService | None = None
_service_key: tuple | None = None
_service_lock = threading.Lock()

_limiter: RagLimiter | None = None
_limiter_key: tuple | None = None


def _trace(request: Request) -> str:
    value = request.headers.get("X-Trace-Id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) else uuid.uuid4().hex


def _authorized(request: Request, settings: Settings) -> bool:
    return bool(settings.ai_service_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + settings.ai_service_token
    )


def _service_for(settings: Settings) -> RetrievalService:
    """按配置复用检索服务；配置变化时重建，避免读到旧集合。"""
    global _service, _service_key
    index_dir = Path(settings.knowledge_index_dir)
    key = (
        settings.qdrant_url,
        settings.rag_collection,
        settings.rag_index_version,
        settings.ollama_base_url,
        settings.ollama_embedding_model,
        settings.ollama_keep_alive,
        settings.rerank_provider,
        settings.rerank_model_path,
        settings.rerank_url,
        settings.rerank_model_name,
        settings.rerank_candidates,
        settings.rerank_min_score,
        settings.rag_cache_enabled,
        settings.rag_cache_ttl_seconds,
        settings.rag_cache_max_entries,
        settings.rag_breaker_failure_threshold,
        settings.rag_breaker_reset_seconds,
    )
    with _service_lock:
        if _service is not None and _service_key == key:
            return _service
        if _service is not None:
            _service.close()
        reranker = None
        # 模型缺失或依赖没装时不能让整个检索接口 503：退回无 rerank 模式，
        # 由 degraded 标注说明这次排序没有经过重排。
        if settings.rerank_provider.strip().lower() not in {"", "none", "off"}:
            try:
                reranker = build_reranker(settings)
            except RerankError as error:
                log.warning("rerank 初始化失败，本次以无 rerank 模式运行：%s", error)
        _service = RetrievalService(
            qdrant_url=settings.qdrant_url,
            collection=settings.rag_collection,
            bm25_path=index_dir / "bm25" / f"{settings.rag_index_version}.json",
            index_manifest_path=index_dir / "manifest.json",
            embedding_model=settings.ollama_embedding_model,
            ollama_url=settings.ollama_base_url,
            embedding_timeout_seconds=settings.ollama_timeout_seconds,
            embedding_keep_alive=settings.ollama_keep_alive,
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
            bm25_strong_score=settings.rag_bm25_strong_score,
            max_per_document=settings.rag_max_per_document,
            reranker=reranker,
            rerank_candidates=settings.rerank_candidates,
            rerank_min_score=settings.rerank_min_score,
            cache_enabled=settings.rag_cache_enabled,
            cache_ttl_seconds=settings.rag_cache_ttl_seconds,
            cache_max_entries=settings.rag_cache_max_entries,
            breaker_failure_threshold=settings.rag_breaker_failure_threshold,
            breaker_reset_seconds=settings.rag_breaker_reset_seconds,
        )
        _service_key = key
        return _service


def _limiter_for(settings: Settings) -> RagLimiter:
    """按配置复用限流器；限流参数变化时重建（重建即清空频率窗口，可接受）。"""
    global _limiter, _limiter_key
    key = (
        settings.rag_max_concurrency,
        settings.rag_max_queue,
        settings.rag_queue_wait_ms,
        settings.rag_user_limit,
        settings.rag_user_window_seconds,
        settings.rag_global_limit,
        settings.rag_redis_url,
    )
    with _service_lock:
        if _limiter is not None and _limiter_key == key:
            return _limiter
        redis_client = _build_redis(settings)
        _limiter = RagLimiter(
            max_concurrency=settings.rag_max_concurrency,
            max_queue=settings.rag_max_queue,
            max_wait_seconds=settings.rag_queue_wait_ms / 1000.0,
            user_limit=settings.rag_user_limit,
            user_window_seconds=settings.rag_user_window_seconds,
            redis_client=redis_client,
            global_limit=settings.rag_global_limit or None,
        )
        _limiter_key = key
        return _limiter


def _build_redis(settings: Settings):
    """Redis 只用于跨进程总量上限；没配置或没装依赖就返回 None（进程内限额）。"""
    if not settings.rag_redis_url or settings.rag_global_limit <= 0:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError:
        log.warning("未安装 redis 客户端，RAG_GLOBAL_LIMIT 不生效（单实例部署可忽略）")
        return None
    try:
        return redis.from_url(
            settings.rag_redis_url,
            socket_timeout=0.2,
            socket_connect_timeout=0.2,
            decode_responses=True,
        )
    except Exception as error:  # noqa: BLE001 - 限流组件不可用时不影响主链路
        log.warning("Redis 限流连接失败，降级为进程内限额：%s", error)
        return None


def warmup() -> None:
    """启动预热：把 rerank 模型、索引与 Embedding 模型都加载到服务启动阶段。

    本地 cross-encoder 首次加载约 10s；Ollama 的 bge-m3 在空闲 5 分钟后会被卸载，
    重新加载实测 5~6s。两者若留到第一个用户请求，都会正好撞上 Java 侧 6s 的检索超时
    ——第一次问问题必然失败或丢来源。启动慢一次比线上抖一次好。
    """
    settings = Settings.from_env()
    if not settings.rag_enabled:
        return
    try:
        service = _service_for(settings)
        service._ensure_loaded()
        # 只加载索引不够：真正贵的是 Ollama 侧的模型加载，必须发一次真实向量请求。
        service.prime()
    except (RagNotReady, RerankError, EmbeddingError, VectorStoreError) as error:
        log.warning("检索预热失败，服务仍可启动：%s", error)
    except Exception as error:  # noqa: BLE001 - 预热不允许影响启动
        log.warning("检索预热异常：%s", error)


def _error(status: int, error_type: str, trace_id: str, detail: str = "") -> JSONResponse:
    body = {"error": {"type": error_type, "traceId": trace_id}}
    if detail:
        body["error"]["detail"] = detail
    return JSONResponse(body, status_code=status)


@router.post("/search")
async def search(request: Request):
    settings = Settings.from_env()
    trace_id = _trace(request)
    if not _authorized(request, settings):
        return _error(401, "unauthorized", trace_id)
    if not settings.rag_enabled:
        return _error(503, "rag_disabled", trace_id, "知识检索已被 RAG_ENABLED 关闭")

    try:
        body = await request.json()
    except ValueError:
        return _error(400, "invalid_request", trace_id, "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_request", trace_id, "请求体必须是对象")

    query = body.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_CHARS:
        return _error(400, "invalid_request", trace_id, f"query 必须为非空字符串且不超过 {_MAX_QUERY_CHARS} 字")

    top_k = body.get("top_k", settings.rag_top_k)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        return _error(400, "invalid_request", trace_id, "top_k 必须是整数")
    if not 1 <= top_k <= _MAX_TOP_K:
        return _error(400, "invalid_request", trace_id, f"top_k 取值范围为 1~{_MAX_TOP_K}")

    min_score = body.get("min_score")
    if min_score is not None:
        try:
            min_score = float(min_score)
        except (TypeError, ValueError):
            return _error(400, "invalid_request", trace_id, "min_score 必须是数字")
        if not -1.0 <= min_score <= 1.0:
            return _error(400, "invalid_request", trace_id, "min_score 取值范围为 -1~1")

    service = _service_for(settings)
    limiter = _limiter_for(settings)
    user_key = str(body.get("user_id") or "").strip() or None
    try:
        # 名额在工作线程里释放：客户端断开时协程会被取消，但线程还在跑，
        # 若按协程的 finally 放行，实际并发会超过上限。
        outcome = await asyncio.to_thread(
            limiter.run, user_key, lambda: service.search(query, top_k, min_score)
        )
    except BusyError as error:
        # 容量不足要快速拒绝：排队到超时对用户来说等于"没有来源"，429 至少可解释、可重试。
        response = _error(429, "busy", trace_id, str(error))
        response.headers["Retry-After"] = f"{error.retry_after_seconds:.1f}"
        return response
    except CircuitOpenError as error:
        # 熔断期的快速失败：仍按"暂时不可用"报，但耗时是毫秒级，不占用户 8s。
        return _error(503, "circuit_open", trace_id, str(error))
    except RagNotReady as error:
        return _error(503, "index_not_ready", trace_id, str(error))
    except (EmbeddingError, VectorStoreError) as error:
        return _error(502, "upstream_unavailable", trace_id, str(error))
    except ValueError as error:
        return _error(400, "invalid_request", trace_id, str(error))

    return JSONResponse({
        "traceId": trace_id,
        "query": outcome.query,
        "answerable": outcome.answerable,
        "cache": outcome.cache,
        "degraded": outcome.degraded,
        "timings": outcome.timings,
        "candidates": outcome.candidates,
        "results": [hit.to_payload() for hit in outcome.hits],
    })


@router.get("/stats")
async def stats(request: Request):
    """缓存命中率与熔断状态；验收和排障时不必去翻日志。"""
    settings = Settings.from_env()
    trace_id = _trace(request)
    if not _authorized(request, settings):
        return _error(401, "unauthorized", trace_id)
    if not settings.rag_enabled:
        return _error(503, "rag_disabled", trace_id, "知识检索已被 RAG_ENABLED 关闭")
    service = _service_for(settings)
    body = service.stats()
    body["limiter"] = _limiter_for(settings).stats()
    body["traceId"] = trace_id
    return JSONResponse(body)
