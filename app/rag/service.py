"""在线检索服务：缓存 → 向量 + BM25 并行 → RRF 融合 → rerank → 证据过滤。

几条硬约束：
1. 检索失败必须显式报错（RagNotReady / VectorStoreError / EmbeddingError），
   不允许悄悄返回空结果让上层以为“知识库里没有”；
2. rerank 是否真的跑过要如实标注 degraded：没配置写 `not_configured`，
   调用失败写 `failed`、熔断写 `circuit_open`，并降级到 RRF + 门槛；
3. 证据不足时 answerable=false，由调用方决定拒答，而不是硬凑几条来源；
4. 缓存命中要如实标注（`cache=hit`），不能拿上次的耗时冒充本次性能。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.rag.cache import CacheKey, QueryCache, normalize_query
from app.rag.circuit import CircuitBreaker, CircuitOpenError
from app.rag.embedding.ollama import OllamaEmbedder
from app.rag.rerank.base import RerankError, Reranker
from app.rag.retrieval.bm25 import BM25Index
from app.rag.retrieval.fusion import reciprocal_rank_fusion
from app.rag.retrieval.vector import QdrantStore

log = logging.getLogger("orange-diary-ai.rag")


class RagNotReady(RuntimeError):
    """检索资源缺失（索引未构建、集合不存在等），属于部署问题而非“没有答案”。"""


@dataclass(frozen=True)
class RetrievalHit:
    """一条可追溯的检索结果。"""

    chunk_id: str
    document_id: str
    title: str
    heading_path: tuple[str, ...]
    text: str
    source_url: str
    needs_review: bool
    score: float
    vector_score: float
    bm25_score: float
    vector_rank: int | None
    bm25_rank: int | None
    rank: int
    # rerank 分数是"这条是否回答了问题"的交互打分，与向量余弦不可直接比较；
    # 未接入 rerank 时为 None，调用方据此判断排序依据。
    rerank_score: float | None = None

    def to_payload(self) -> dict:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "heading_path": list(self.heading_path),
            "text": self.text,
            "source_url": self.source_url,
            "needs_review": self.needs_review,
            "score": round(self.score, 6),
            "vector_score": round(self.vector_score, 4),
            "bm25_score": round(self.bm25_score, 3),
            "rerank_score": None if self.rerank_score is None else round(self.rerank_score, 4),
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
        }


@dataclass
class RetrievalOutcome:
    query: str
    hits: list[RetrievalHit]
    answerable: bool
    degraded: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    candidates: dict = field(default_factory=dict)
    # miss = 本次真正检索；hit = 命中缓存；shared = 复用了并发请求的结果；
    # bypass = 关闭缓存直接检索。不能让调用方把 hit 的毫秒数当成本次性能。
    cache: str = "miss"


def _diversify(hits: list[RetrievalHit], limit: int, max_per_document: int) -> list[RetrievalHit]:
    """限制同篇文档的条数，避免来源卡片被同一段落占满。

    先按上限宽松选取，结果不足时再用溢出的片段补齐，保证召回数量不因多样性损失。
    """
    if max_per_document <= 0 or limit <= 0:
        return hits[:limit]
    picked: list[RetrievalHit] = []
    overflow: list[RetrievalHit] = []
    counts: dict[str, int] = {}
    for hit in hits:
        if len(picked) >= limit:
            break
        used = counts.get(hit.document_id, 0)
        if used < max_per_document:
            picked.append(hit)
            counts[hit.document_id] = used + 1
        else:
            overflow.append(hit)
    for hit in overflow:
        if len(picked) >= limit:
            break
        picked.append(hit)
    return picked


class RetrievalService:
    """单进程内的检索门面；索引与连接只加载一次。"""
    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        collection: str = "orange_diary_knowledge_v2",
        bm25_path: Path | None = None,
        index_manifest_path: Path | None = None,
        embedding_model: str = "bge-m3",
        ollama_url: str = "http://127.0.0.1:11434",
        embedding_timeout_seconds: float = 8.0,
        embedding_keep_alive: str = "30m",
        top_k: int = 5,
        min_score: float = 0.60,
        bm25_strong_score: float = 12.0,
        max_per_document: int = 2,
        reranker: Reranker | None = None,
        rerank_candidates: int = 12,
        rerank_min_score: float = 0.75,
        cache_enabled: bool = True,
        cache_ttl_seconds: float = 300.0,
        cache_max_entries: int = 256,
        breaker_failure_threshold: int = 3,
        breaker_reset_seconds: float = 30.0,
    ) -> None:
        self.qdrant_url = qdrant_url
        self.collection = collection
        self.bm25_path = Path(bm25_path) if bm25_path else None
        self.index_manifest_path = Path(index_manifest_path) if index_manifest_path else None
        self.embedding_model = embedding_model
        self.ollama_url = ollama_url
        self.embedding_timeout_seconds = embedding_timeout_seconds
        self.embedding_keep_alive = embedding_keep_alive
        self.top_k = max(1, top_k)
        self.min_score = min_score
        self.bm25_strong_score = bm25_strong_score
        self.max_per_document = max(1, max_per_document)
        self.reranker = reranker
        self.rerank_candidates = max(1, rerank_candidates)
        self.rerank_min_score = float(rerank_min_score)
        self.cache = (
            QueryCache(max_entries=cache_max_entries, ttl_seconds=cache_ttl_seconds)
            if cache_enabled
            else None
        )
        # 向量通道（Embedding + Qdrant）一起熔断：对调用方来说都是"检索不到"，
        # 拆成两个计数器只会让恢复判定变慢，没有额外信息。
        self._vector_breaker = CircuitBreaker(
            "vector",
            failure_threshold=breaker_failure_threshold,
            reset_timeout_seconds=breaker_reset_seconds,
        )
        self._rerank_breaker = CircuitBreaker(
            "rerank",
            failure_threshold=breaker_failure_threshold,
            reset_timeout_seconds=breaker_reset_seconds,
        )

        self._lock = threading.Lock()
        self._loaded = False
        self._bm25: BM25Index | None = None
        self._documents: dict[str, dict] = {}
        self._embedder: OllamaEmbedder | None = None
        self._store: QdrantStore | None = None

    # ---------- 资源加载 ----------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.bm25_path is None or not self.bm25_path.is_file():
                raise RagNotReady(f"BM25 索引不存在: {self.bm25_path}")
            started = time.perf_counter()
            self._bm25 = BM25Index.load(self.bm25_path)
            self._documents = self._load_document_meta()
            self._embedder = OllamaEmbedder(
                model=self.embedding_model,
                base_url=self.ollama_url,
                # 在线检索不能像离线入库那样等两分钟：Ollama 卡住时要尽快失败并降级。
                timeout_seconds=self.embedding_timeout_seconds,
                keep_alive=self.embedding_keep_alive,
            )
            self._store = QdrantStore(base_url=self.qdrant_url)
            self._loaded = True
            log.info(
                "检索资源加载完成：%s 片段，%s 篇文档，耗时 %.0fms",
                len(self._bm25.chunk_ids),
                len(self._documents),
                (time.perf_counter() - started) * 1000,
            )

    def prime(self) -> None:
        """预热下游模型：发一次最小向量请求，触发 Ollama 加载 bge-m3。

        只把本地索引读进内存解决不了首问变慢——Ollama 空闲 5 分钟就卸载模型，
        重新加载实测 5~6s，正好超过 Java 侧 6s 的检索读超时，表现为"第一句没有来源"。
        """
        self._ensure_loaded()
        assert self._embedder is not None
        started = time.perf_counter()
        self._embedder.embed(["预热"])
        log.info("Embedding 预热完成，耗时 %.0fms", (time.perf_counter() - started) * 1000)

    def _load_document_meta(self) -> dict[str, dict]:
        """读取发布清单中的文档元数据，用于补齐 needs_review 等展示字段。"""
        if self.index_manifest_path is None or not self.index_manifest_path.is_file():
            return {}
        try:
            body = json.loads(self.index_manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("索引清单不可读，needs_review 将统一按未知处理")
            return {}
        return {
            str(item.get("document_id")): item
            for item in body.get("documents") or []
            if item.get("document_id")
        }

    def close(self) -> None:
        with self._lock:
            if self._embedder is not None:
                self._embedder.close()
            if self._store is not None:
                self._store.close()
            if self.reranker is not None:
                self.reranker.close()
                self.reranker = None
            if self.cache is not None:
                # 索引换版、模型换版时服务会重建，旧缓存必须一起丢弃。
                self.cache.clear()
            self._embedder = None
            self._store = None
            self._loaded = False

    # ---------- 两个通道 ----------

    def _vector_channel(self, query: str, limit: int) -> tuple[list[dict], float]:
        if not self._vector_breaker.allow():
            # 熔断期内不碰 Ollama / Qdrant：Ollama 挂住时每次都要空等 8s，
            # 而 Java 侧 6s 就超时了，等待没有任何收益。
            raise CircuitOpenError(self._vector_breaker)
        started = time.perf_counter()
        assert self._embedder is not None and self._store is not None
        try:
            vector = self._embedder.embed([query])[0]
            hits = self._store.search(self.collection, vector, limit=limit)
        except Exception as error:
            self._vector_breaker.record_failure(error)
            raise
        self._vector_breaker.record_success()
        return hits, (time.perf_counter() - started) * 1000

    def _bm25_channel(self, query: str, limit: int) -> tuple[list[tuple[str, float]], float]:
        started = time.perf_counter()
        assert self._bm25 is not None
        hits = self._bm25.search(query, top_k=limit)
        return hits, (time.perf_counter() - started) * 1000

    # ---------- 对外入口 ----------

    def search(self, query: str, top_k: int | None = None, min_score: float | None = None) -> RetrievalOutcome:
        """执行一次混合检索。空查询、资源缺失、下游异常都会显式抛出。"""
        query = (query or "").strip()
        if not query:
            raise ValueError("查询不能为空")
        self._ensure_loaded()

        limit_k = max(1, top_k or self.top_k)
        threshold = self.min_score if min_score is None else float(min_score)
        if self.cache is None:
            return replace(self._run(query, limit_k, threshold), cache="bypass")

        key = CacheKey(normalize_query(query), limit_k, threshold)
        started = time.perf_counter()
        entry = self.cache.get(key)
        if entry is not None:
            cached, age_ms = entry
            return self._cached(cached, "hit", started, age_ms)

        leader, event = self.cache.begin(key)
        if not leader:
            # 同一查询已经在检索中：等它出结果，不重复跑 rerank。
            event.wait(self.cache.wait_timeout_seconds)
            entry = self.cache.get(key)
            if entry is not None:
                cached, age_ms = entry
                return self._cached(cached, "shared", started, age_ms)
            # 前一个请求失败或超时没写缓存：自己跑一遍，不再继续等。

        try:
            outcome = self._run(query, limit_k, threshold)
        finally:
            # 必须在 finally 里唤醒等待者，否则一次异常会让同查询全部卡到超时。
            if leader:
                self.cache.end(key)
        self.cache.put(key, outcome)
        return outcome

    @staticmethod
    def _cached(
        cached: RetrievalOutcome, state: str, started: float, age_ms: float = 0.0
    ) -> RetrievalOutcome:
        """把缓存结果包装成"本次调用"的样子：耗时换成本次真实耗时。"""
        degraded = dict(cached.degraded)
        degraded["cache"] = state
        return replace(
            cached,
            hits=list(cached.hits),
            degraded=degraded,
            timings={
                "total_ms": round((time.perf_counter() - started) * 1000, 2),
                # 原始那次的耗时与缓存年龄保留，便于对比缓存收益，但不冒充本次延迟。
                "origin_total_ms": cached.timings.get("total_ms"),
                "cache_age_ms": round(age_ms, 1),
            },
            candidates=dict(cached.candidates),
            cache=state,
        )

    def stats(self) -> dict:
        """运行态快照，供 /stats 与管理端排查；不参与检索逻辑。"""
        return {
            "collection": self.collection,
            "loaded": self._loaded,
            "cache": self.cache.stats() if self.cache is not None else {"enabled": False},
            "breakers": {
                "vector": self._vector_breaker.snapshot(),
                "rerank": self._rerank_breaker.snapshot(),
            },
        }

    def _run(self, query: str, limit_k: int, threshold: float) -> RetrievalOutcome:
        """真正执行一次检索（无缓存）。"""
        # 候选池取目标条数的数倍，给融合留出纠错空间；接了 rerank 时要喂满候选数。
        candidates = max(limit_k * 4, 20, self.rerank_candidates if self.reranker else 0)
        total_started = time.perf_counter()

        with ThreadPoolExecutor(max_workers=2) as pool:
            vector_future = pool.submit(self._vector_channel, query, candidates)
            bm25_future = pool.submit(self._bm25_channel, query, candidates)
            vector_hits, vector_ms = vector_future.result()
            bm25_hits, bm25_ms = bm25_future.result()

        vector_rank = {hit["chunk_id"]: index + 1 for index, hit in enumerate(vector_hits)}
        vector_score = {hit["chunk_id"]: hit["score"] for hit in vector_hits}
        bm25_rank = {chunk_id: index + 1 for index, (chunk_id, _score) in enumerate(bm25_hits)}
        bm25_score = {chunk_id: score for chunk_id, score in bm25_hits}

        fused = reciprocal_rank_fusion(
            [[hit["chunk_id"] for hit in vector_hits], [chunk_id for chunk_id, _ in bm25_hits]]
        )

        payloads = {hit["chunk_id"]: hit["payload"] for hit in vector_hits}
        missing = [chunk_id for chunk_id, _score in fused if chunk_id not in payloads]
        fill_started = time.perf_counter()
        if missing:
            assert self._store is not None
            payloads.update(self._store.fetch_by_chunk_ids(self.collection, missing))
        fill_ms = (time.perf_counter() - fill_started) * 1000

        # 先把候选整理成统一结构，排序与门槛在下一步分两条路走。
        prepared: list[dict] = []
        dropped = 0
        for chunk_id, score in fused:
            payload = payloads.get(chunk_id)
            if not payload:
                # 融合命中但取不到正文（例如索引被外部改动），跳过并计数。
                dropped += 1
                continue
            heading_path = tuple(payload.get("heading_path") or ())
            text = str(payload.get("text") or "")
            prepared.append(
                {
                    "chunk_id": chunk_id,
                    "payload": payload,
                    "score": score,
                    "vector_score": float(vector_score.get(chunk_id, 0.0)),
                    "bm25_score": float(bm25_score.get(chunk_id, 0.0)),
                    # rerank 只看正文：实测把章节标题并进输入会显著压低正确片段的分数
                    # （q19 的「谷薯类的选择」0.884 → 0.112，因 pad 到更长序列后
                    # 模型对短正文的判别力下降），与索引文本"标题+正文"的做法相反。
                    "rerank_text": text,
                    "heading_path": heading_path,
                    "vector_rank": vector_rank.get(chunk_id),
                    "bm25_rank": bm25_rank.get(chunk_id),
                }
            )
            if self.reranker is not None and len(prepared) >= self.rerank_candidates:
                break

        rerank_scores: list[float] | None = None
        rerank_ms = 0.0
        if self.reranker is not None:
            if not self._rerank_breaker.allow():
                # 熔断期内直接跳过重排，退回 RRF + 两级门槛；标注与失败区分开，
                # 免得运维以为模型加载出错。
                degraded = {
                    "rerank": "circuit_open",
                    "reason": f"rerank 连续失败已熔断，约 {self._rerank_breaker.retry_after_seconds():.0f}s 后重试",
                }
            else:
                try:
                    scored = self.reranker.score(query, [item["rerank_text"] for item in prepared])
                    rerank_scores = list(scored.scores)
                    rerank_ms = scored.latency_ms
                    self._rerank_breaker.record_success()
                    degraded = {
                        "rerank": f"{scored.provider}:{scored.model}",
                        "rerank_threshold": self.rerank_min_score,
                    }
                except RerankError as error:
                    # rerank 挂了不能让整个检索挂掉：退回 RRF + 两级门槛，并明确标注降级原因。
                    log.warning("rerank 调用失败，已降级为 RRF + 证据门槛：%s", error)
                    self._rerank_breaker.record_failure(error)
                    degraded = {"rerank": "failed", "reason": str(error)}
        else:
            degraded = {
                "rerank": "not_configured",
                "reason": "未接入 rerank 模型，排序仅由 RRF 融合与证据过滤决定",
            }

        hits: list[RetrievalHit] = []
        if rerank_scores is not None:
            # 有 rerank 时由它决定排序与"算不算证据"，向量/BM25 分数只作记录。
            # 理由：M4 实测正负例的向量分区间重叠，任何固定余弦阈值都会误杀；
            # cross-encoder 给的是 query-passage 交互分，才是该拿来卡的信号。
            for index in sorted(range(len(prepared)), key=lambda i: -rerank_scores[i]):
                if rerank_scores[index] < self.rerank_min_score:
                    dropped += 1
                    continue
                hits.append(
                    self._to_hit(prepared[index], rerank_score=rerank_scores[index])
                )
        else:
            for item in prepared:
                # 证据过滤分两级：向量相似度达标，或关键词出现"强命中"（分数远高于噪声水平）。
                # 早期用"向量 0.55 或 BM25 6.0"的宽松或规则，实测会让无关问题被
                # BM25 的零星词命中救活（20 道负例误答 8 道），因此抬高两侧门槛。
                if item["vector_score"] < threshold and item["bm25_score"] < self.bm25_strong_score:
                    dropped += 1
                    continue
                hits.append(self._to_hit(item, rerank_score=None))

        hits = _diversify(hits, limit_k, self.max_per_document)
        hits = [replace(hit, rank=index + 1) for index, hit in enumerate(hits)]

        total_ms = (time.perf_counter() - total_started) * 1000
        # 能通过证据过滤的片段才算可用证据；hits 为空即视为知识库无法支撑作答。
        answerable = bool(hits)
        return RetrievalOutcome(
            query=query,
            hits=hits,
            answerable=answerable,
            degraded=degraded,
            timings={
                "vector_ms": round(vector_ms, 1),
                "bm25_ms": round(bm25_ms, 1),
                "payload_fill_ms": round(fill_ms, 1),
                "rerank_ms": round(rerank_ms, 1),
                "total_ms": round(total_ms, 1),
            },
            candidates={
                "vector": len(vector_hits),
                "bm25": len(bm25_hits),
                "fused": len(fused),
                "prepared": len(prepared),
                "dropped": dropped,
            },
            cache="miss",
        )

    def _to_hit(self, item: dict, rerank_score: float | None) -> RetrievalHit:
        """把候选字典转成对外结构，同时补齐清单里的展示元数据。"""
        payload = item["payload"]
        document_id = str(payload.get("document_id") or "")
        meta = self._documents.get(document_id) or {}
        return RetrievalHit(
            chunk_id=item["chunk_id"],
            document_id=document_id,
            title=str(payload.get("title") or meta.get("title") or ""),
            heading_path=item["heading_path"],
            text=str(payload.get("text") or ""),
            source_url=str(payload.get("source_url") or meta.get("source_url") or ""),
            needs_review=bool(meta.get("needs_review", False)),
            score=item["score"],
            vector_score=item["vector_score"],
            bm25_score=item["bm25_score"],
            vector_rank=item["vector_rank"],
            bm25_rank=item["bm25_rank"],
            rank=0,
            rerank_score=rerank_score,
        )
