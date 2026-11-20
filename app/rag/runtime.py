"""检索服务的进程内单例装配。

原先这套缓存挂在 `app/api/rag.py` 里，只有内部 HTTP 接口能用到。聊天迁移成
单进程后，`/api/ai/chat` 也要检索——如果它另建一个 `RetrievalService`，
索引、BM25 与 rerank 模型会各自加载一份（实测多占几百 MB），而且两份缓存的
计数器分开，管理页看到的命中率会凭空减半。所以把这套装配提到 rag 包内，
HTTP 接口与业务服务共用同一份实例。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import Settings
from app.rag.limiter import RagLimiter
from app.rag.rerank import RerankError, build_reranker
from app.rag.service import RetrievalService

log = logging.getLogger("orange-diary-ai.rag")

# 按知识域各持一个实例：BM25 索引文件与向量过滤条件都随域变化。
# 不能"切全局 Settings 换集合"——那会让实例在域之间反复关闭重建，
# 每次重建都要重新加载 BM25 与 rerank 模型。
_services: dict[str, RetrievalService] = {}
_service_keys: dict[str, tuple] = {}
_service_lock = threading.Lock()

_limiter: RagLimiter | None = None
_limiter_key: tuple | None = None

# 全部知识域共用一份 reranker：模型几百 MB，按域各建一份等于把内存翻倍，
# 而且每次重建都要重新加载一次（本地 cross-encoder 实测约 7s）。
_reranker = None
_reranker_key: tuple | None = None


def _shared_reranker(settings: Settings):
    """按 rerank 配置复用同一个 reranker；关闭时返回 None（走 RRF 排序）。"""
    global _reranker, _reranker_key
    if settings.rerank_provider.strip().lower() in {"", "none", "off"}:
        return None
    key = (
        settings.rerank_provider,
        settings.rerank_model_path,
        settings.rerank_url,
        settings.rerank_model_name,
        settings.rerank_min_score,
    )
    with _service_lock:
        if _reranker is not None and _reranker_key == key:
            return _reranker
        if _reranker is not None:
            _reranker.close()
            _reranker = None
        try:
            _reranker = build_reranker(settings)
        except RerankError as error:
            # 模型缺失或依赖没装时不能让整个检索接口 503：退回无 rerank 模式，
            # 由 degraded 标注说明这次排序没有经过重排。
            log.warning("rerank 初始化失败，本次以无 rerank 模式运行：%s", error)
            _reranker = None
        _reranker_key = key
        return _reranker


def service_for(settings: Settings, domain: str | None = None) -> RetrievalService:
    """按配置+知识域复用检索服务；配置变化时重建，避免读到旧集合。"""
    index_dir = Path(settings.knowledge_index_dir)
    slot = domain or ""
    # 每个域一份 BM25 索引：候选阶段就要按域限定，不能混搜后再过滤。
    bm25_name = f"{domain}.{settings.rag_index_version}.json" if domain else f"{settings.rag_index_version}.json"
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
    # 共享 reranker 必须在拿 _service_lock 之前取：_shared_reranker 内部也用它，
    # 而 threading.Lock 不可重入，先取会自锁。
    reranker = _shared_reranker(settings)
    with _service_lock:
        existing = _services.get(slot)
        if existing is not None and _service_keys.get(slot) == key:
            return existing
        if existing is not None:
            existing.close()
        service = RetrievalService(
            qdrant_url=settings.qdrant_url,
            collection=settings.rag_collection,
            bm25_path=index_dir / "bm25" / bm25_name,
            index_manifest_path=index_dir / "manifest.json",
            domain=domain,
            embedding_model=settings.ollama_embedding_model,
            ollama_url=settings.ollama_base_url,
            embedding_timeout_seconds=settings.ollama_timeout_seconds,
            embedding_keep_alive=settings.ollama_keep_alive,
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
            bm25_strong_score=settings.rag_bm25_strong_score,
            max_per_document=settings.rag_max_per_document,
            reranker=reranker,
            owns_reranker=False,
            rerank_candidates=settings.rerank_candidates,
            rerank_min_score=settings.rerank_min_score,
            cache_enabled=settings.rag_cache_enabled,
            cache_ttl_seconds=settings.rag_cache_ttl_seconds,
            cache_max_entries=settings.rag_cache_max_entries,
            breaker_failure_threshold=settings.rag_breaker_failure_threshold,
            breaker_reset_seconds=settings.rag_breaker_reset_seconds,
        )
        _services[slot] = service
        _service_keys[slot] = key
        return service


def limiter_for(settings: Settings) -> RagLimiter:
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
        redis_client = build_redis(settings)
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


def build_redis(settings: Settings):
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


def runtime_stats(settings: Settings) -> dict:
    """运行态快照（检索 + 限流）。

    必须是**本进程同一份**实例：另起一个实例去算，计数器全是 0，
    看起来像"缓存从没命中过"。
    """
    body = service_for(settings).stats()
    body["limiter"] = limiter_for(settings).stats()
    return body
