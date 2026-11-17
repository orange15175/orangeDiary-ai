"""检索服务（融合 + 证据过滤）的单元测试。

用假组件替代 Ollama / Qdrant，测试不依赖本机服务是否运行。
"""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from app.rag.circuit import CircuitOpenError
from app.rag.embedding.ollama import EmbeddingError
from app.rag.rerank.base import RerankError, RerankScores
from app.rag.service import RagNotReady, RetrievalService


class FakeBM25:
    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self._hits = hits
        self.chunk_ids = [chunk_id for chunk_id, _ in hits]

    def search(self, query: str, top_k: int = 20):
        return self._hits[:top_k]


class FakeEmbedder:
    def embed(self, texts: list[str]):
        return [[0.1, 0.2, 0.3] for _ in texts]

    def close(self) -> None:
        pass


class FakeStore:
    def __init__(self, vector_hits: list[tuple[str, float]], payloads: dict[str, dict]) -> None:
        self._vector_hits = vector_hits
        self._payloads = payloads
        self.fetch_calls: list[list[str]] = []

    def search(self, name: str, vector: list[float], limit: int = 20):
        return [
            {"chunk_id": chunk_id, "score": score, "payload": self._payloads.get(chunk_id, {})}
            for chunk_id, score in self._vector_hits[:limit]
        ]

    def fetch_by_chunk_ids(self, name: str, chunk_ids: list[str]):
        self.fetch_calls.append(list(chunk_ids))
        return {cid: self._payloads[cid] for cid in chunk_ids if cid in self._payloads}

    def close(self) -> None:
        pass


def build_service(
    vector_hits: list[tuple[str, float]],
    bm25_hits: list[tuple[str, float]],
    payloads: dict[str, dict],
    documents: dict[str, dict] | None = None,
    top_k: int = 5,
    min_score: float = 0.35,
    bm25_strong_score: float = 6.0,
    max_per_document: int = 2,
    cache_enabled: bool = False,
    breaker_failure_threshold: int = 3,
    breaker_reset_seconds: float = 30.0,
    reranker=None,
    rerank_min_score: float = 0.0,
    embedder=None,
) -> tuple[RetrievalService, FakeStore]:
    service = RetrievalService(
        top_k=top_k,
        min_score=min_score,
        bm25_strong_score=bm25_strong_score,
        max_per_document=max_per_document,
        cache_enabled=cache_enabled,
        breaker_failure_threshold=breaker_failure_threshold,
        breaker_reset_seconds=breaker_reset_seconds,
        reranker=reranker,
        rerank_min_score=rerank_min_score,
    )
    store = FakeStore(vector_hits, payloads)
    # 跳过磁盘加载，直接注入假组件。
    service._loaded = True
    service._bm25 = FakeBM25(bm25_hits)
    service._store = store
    service._embedder = embedder or FakeEmbedder()
    service._documents = documents or {}
    return service, store


def payload(chunk_id: str, document_id: str = "doc-a", text: str = "正文") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "title": "示例文档",
        "heading_path": ["示例文档", "小节"],
        "text": text,
        "source_url": "https://example.org/doc",
    }


class RetrievalServiceTest(unittest.TestCase):
    def test_bm25_only_candidate_gets_payload_and_joined(self):
        # b 只在关键词通道命中，仍需补取正文后进入结果。
        service, store = build_service(
            vector_hits=[("a", 0.62), ("c", 0.50)],
            bm25_hits=[("b", 9.0), ("a", 4.0)],
            payloads={"a": payload("a"), "b": payload("b", text="低钠盐的用法"), "c": payload("c")},
        )
        outcome = service.search("低钠盐怎么用")
        ids = [hit.chunk_id for hit in outcome.hits]
        self.assertIn("b", ids)
        self.assertEqual(store.fetch_calls, [["b"]])
        hit_b = next(hit for hit in outcome.hits if hit.chunk_id == "b")
        self.assertEqual(hit_b.text, "低钠盐的用法")
        self.assertEqual(hit_b.bm25_rank, 1)
        self.assertIsNone(hit_b.vector_rank)

    def test_low_score_candidates_are_filtered(self):
        service, _ = build_service(
            vector_hits=[("a", 0.20), ("b", 0.18)],
            bm25_hits=[("c", 2.0)],
            payloads={"a": payload("a"), "b": payload("b"), "c": payload("c")},
        )
        outcome = service.search("今天天气怎么样")
        self.assertEqual(outcome.hits, [])
        self.assertFalse(outcome.answerable)

    def test_strong_bm25_keeps_hit_even_if_vector_low(self):
        service, _ = build_service(
            vector_hits=[("a", 0.10)],
            bm25_hits=[("a", 12.5)],
            payloads={"a": payload("a")},
        )
        outcome = service.search("膳食宝塔第二层")
        self.assertEqual([hit.chunk_id for hit in outcome.hits], ["a"])
        self.assertTrue(outcome.answerable)

    def test_high_vector_hit_survives_without_bm25(self):
        service, _ = build_service(
            vector_hits=[("a", 0.71)],
            bm25_hits=[],
            payloads={"a": payload("a")},
        )
        outcome = service.search("每天应该吃多少蔬菜")
        self.assertEqual(len(outcome.hits), 1)
        self.assertTrue(outcome.answerable)

    def test_needs_review_flag_comes_from_document_meta(self):
        service, _ = build_service(
            vector_hits=[("a", 0.66)],
            bm25_hits=[],
            payloads={"a": payload("a", document_id="doc-review")},
            documents={"doc-review": {"needs_review": True, "source_url": ""}},
        )
        outcome = service.search("膳食宝塔")
        self.assertTrue(outcome.hits[0].needs_review)

    def test_top_k_limits_and_ranks_are_sequential(self):
        vector_hits = [(f"c{index}", 0.9 - index * 0.05) for index in range(8)]
        payloads = {chunk_id: payload(chunk_id) for chunk_id, _ in vector_hits}
        service, _ = build_service(vector_hits=vector_hits, bm25_hits=[], payloads=payloads, top_k=3)
        outcome = service.search("蔬菜")
        self.assertEqual(len(outcome.hits), 3)
        self.assertEqual([hit.rank for hit in outcome.hits], [1, 2, 3])

    def test_rerank_is_reported_as_degraded(self):
        service, _ = build_service(
            vector_hits=[("a", 0.71)], bm25_hits=[], payloads={"a": payload("a")}
        )
        outcome = service.search("蔬菜")
        self.assertEqual(outcome.degraded.get("rerank"), "not_configured")

    def test_timings_and_candidate_counts_present(self):
        service, _ = build_service(
            vector_hits=[("a", 0.71)], bm25_hits=[("a", 3.0)], payloads={"a": payload("a")}
        )
        outcome = service.search("蔬菜")
        for key in ("vector_ms", "bm25_ms", "payload_fill_ms", "total_ms"):
            self.assertIn(key, outcome.timings)
        self.assertEqual(outcome.candidates["vector"], 1)
        self.assertEqual(outcome.candidates["bm25"], 1)

    def test_same_document_is_capped_before_other_documents(self):
        vector_hits = [("a1", 0.90), ("a2", 0.88), ("a3", 0.86), ("b1", 0.85)]
        payloads = {
            chunk_id: payload(chunk_id, document_id="doc-a" if chunk_id.startswith("a") else "doc-b")
            for chunk_id, _ in vector_hits
        }
        service, _ = build_service(
            vector_hits=vector_hits, bm25_hits=[], payloads=payloads, top_k=3, max_per_document=2
        )
        outcome = service.search("蔬菜")
        document_ids = [hit.document_id for hit in outcome.hits]
        self.assertEqual(document_ids.count("doc-a"), 2)
        self.assertIn("doc-b", document_ids)
        self.assertEqual([hit.rank for hit in outcome.hits], [1, 2, 3])

    def test_empty_query_raises(self):
        service, _ = build_service(vector_hits=[], bm25_hits=[], payloads={})
        with self.assertRaises(ValueError):
            service.search("   ")

    def test_missing_index_raises_not_ready(self):
        service = RetrievalService(bm25_path=Path("does-not-exist.json"))
        with self.assertRaises(RagNotReady):
            service.search("蔬菜")


class CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]):
        self.calls += 1
        return super().embed(texts)


class ExplodingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]):
        self.calls += 1
        raise EmbeddingError("Ollama 连接被拒绝")


class CacheBehaviourTest(unittest.TestCase):
    def test_cache_disabled_reports_bypass(self):
        service, _ = build_service(
            vector_hits=[("a", 0.9)], bm25_hits=[], payloads={"a": payload("a")}
        )
        self.assertEqual(service.search("蔬菜").cache, "bypass")

    def test_second_identical_query_hits_cache_and_skips_channels(self):
        embedder = CountingEmbedder()
        service, store = build_service(
            vector_hits=[("a", 0.9)],
            bm25_hits=[],
            payloads={"a": payload("a")},
            cache_enabled=True,
            embedder=embedder,
        )
        first = service.search("蔬菜怎么吃")
        second = service.search("  蔬菜怎么吃  ")
        self.assertEqual(first.cache, "miss")
        self.assertEqual(second.cache, "hit")
        self.assertEqual(embedder.calls, 1)
        self.assertEqual([hit.chunk_id for hit in second.hits], ["a"])
        # 命中缓存不能把上一次的总耗时冒充成本次延迟。
        self.assertNotIn("vector_ms", second.timings)
        self.assertIn("origin_total_ms", second.timings)
        self.assertEqual(second.degraded.get("cache"), "hit")
        self.assertEqual(len(store.fetch_calls), 0)

    def test_different_top_k_is_a_different_key(self):
        service, _ = build_service(
            vector_hits=[("a", 0.9), ("b", 0.88)],
            bm25_hits=[],
            payloads={"a": payload("a"), "b": payload("b", document_id="doc-b")},
            cache_enabled=True,
        )
        service.search("蔬菜", top_k=1)
        wider = service.search("蔬菜", top_k=2)
        self.assertEqual(wider.cache, "miss")
        self.assertEqual(len(wider.hits), 2)

    def test_empty_result_is_cached_too(self):
        embedder = CountingEmbedder()
        service, _ = build_service(
            vector_hits=[("a", 0.1)], bm25_hits=[], payloads={"a": payload("a")},
            cache_enabled=True, embedder=embedder,
        )
        self.assertFalse(service.search("今天天气").answerable)
        second = service.search("今天天气")
        self.assertEqual(second.cache, "hit")
        self.assertFalse(second.answerable)
        self.assertEqual(embedder.calls, 1)

    def test_stats_expose_cache_counters(self):
        service, _ = build_service(
            vector_hits=[("a", 0.9)], bm25_hits=[], payloads={"a": payload("a")},
            cache_enabled=True,
        )
        service.search("蔬菜")
        service.search("蔬菜")
        stats = service.stats()
        self.assertEqual(stats["cache"]["hits"], 1)
        self.assertEqual(stats["cache"]["misses"], 1)
        self.assertEqual(stats["breakers"]["vector"]["state"], "closed")

    def test_concurrent_identical_query_shares_one_run(self):
        """并发同查询只跑一次：否则 rerank 会被重复执行。"""
        release = threading.Event()

        class BlockingEmbedder(FakeEmbedder):
            calls = 0

            def embed(self, texts):
                BlockingEmbedder.calls += 1
                release.wait(5)
                return super().embed(texts)

        service, _ = build_service(
            vector_hits=[("a", 0.9)], bm25_hits=[], payloads={"a": payload("a")},
            cache_enabled=True, embedder=BlockingEmbedder(),
        )
        results: list = []
        worker = threading.Thread(target=lambda: results.append(service.search("蔬菜")))
        worker.start()
        # 等检索线程真正进入 Embedding，再发起第二个相同查询。
        for _ in range(100):
            if BlockingEmbedder.calls:
                break
            time.sleep(0.01)
        follower = service.search("蔬菜")
        release.set()
        worker.join(5)
        self.assertEqual(follower.cache, "shared")
        self.assertEqual(BlockingEmbedder.calls, 1)
        self.assertEqual(results[0].cache, "miss")


class CircuitBreakerInServiceTest(unittest.TestCase):
    def test_vector_channel_opens_after_repeated_failures(self):
        embedder = ExplodingEmbedder()
        service, _ = build_service(
            vector_hits=[], bm25_hits=[], payloads={}, embedder=embedder,
            breaker_failure_threshold=3,
        )
        for _ in range(3):
            with self.assertRaises(EmbeddingError):
                service.search("蔬菜")
        with self.assertRaises(CircuitOpenError):
            service.search("蔬菜")
        # 熔断后不再打下游：第 4 次调用没有进入 Embedding。
        self.assertEqual(embedder.calls, 3)
        self.assertEqual(service.stats()["breakers"]["vector"]["state"], "open")

    def test_breaker_not_opened_by_insufficient_failures(self):
        embedder = ExplodingEmbedder()
        service, _ = build_service(
            vector_hits=[], bm25_hits=[], payloads={}, embedder=embedder,
            breaker_failure_threshold=5,
        )
        for _ in range(4):
            with self.assertRaises(EmbeddingError):
                service.search("蔬菜")
        self.assertEqual(service.stats()["breakers"]["vector"]["state"], "closed")

    def test_rerank_failure_opens_breaker_then_degrades_without_calling_model(self):
        class FailingReranker:
            calls = 0

            def score(self, query, documents):
                FailingReranker.calls += 1
                raise RerankError("模型推理失败")

            def close(self):
                pass

        reranker = FailingReranker()
        service, _ = build_service(
            vector_hits=[("a", 0.9)], bm25_hits=[], payloads={"a": payload("a")},
            reranker=reranker, breaker_failure_threshold=2, rerank_min_score=0.5,
        )
        first = service.search("蔬菜")
        self.assertEqual(first.degraded.get("rerank"), "failed")
        # 失败后仍按 RRF + 向量门槛给出结果，不因重排挂掉而拒答。
        self.assertTrue(first.answerable)

        service.search("膳食指南")
        third = service.search("每天吃多少蔬菜")
        self.assertEqual(third.degraded.get("rerank"), "circuit_open")
        self.assertEqual(FailingReranker.calls, 2)
        self.assertEqual(service.stats()["breakers"]["rerank"]["state"], "open")

    def test_rerank_success_marks_provider(self):
        class FixedReranker:
            def score(self, query, documents):
                return RerankScores(
                    scores=[0.9 for _ in documents], provider="fake", model="toy", latency_ms=1.0
                )

            def close(self):
                pass

        service, _ = build_service(
            vector_hits=[("a", 0.9), ("b", 0.8)],
            bm25_hits=[],
            payloads={"a": payload("a"), "b": payload("b", document_id="doc-b")},
            reranker=FixedReranker(),
            rerank_min_score=0.5,
        )
        outcome = service.search("蔬菜")
        self.assertEqual(outcome.degraded.get("rerank"), "fake:toy")
        self.assertEqual([hit.rank for hit in outcome.hits], [1, 2])
        self.assertAlmostEqual(outcome.hits[0].rerank_score, 0.9)


if __name__ == "__main__":
    unittest.main()
