"""rerank 子系统测试：分数归一、降级语义、阈值与跳过逻辑。

不加载真实模型（加载一次十几秒，且不同机器结果不同）；用一个返回固定分数的
假 reranker 验证"排序由谁决定"和"失败时退化成什么"，真实效果看
`scripts/tune_rerank.py` 的离线评估。
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.config import Settings
from app.rag.rerank import RerankError, build_reranker, normalize_scores
from app.rag.rerank.base import RerankScores
from app.rag.rerank.http import HttpReranker
from app.rag.rerank.local import LocalReranker
from app.rag.service import RetrievalService


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

    def search(self, name: str, vector: list[float], limit: int = 20):
        return [
            {"chunk_id": chunk_id, "score": score, "payload": self._payloads.get(chunk_id, {})}
            for chunk_id, score in self._vector_hits[:limit]
        ]

    def fetch_by_chunk_ids(self, name: str, chunk_ids: list[str]):
        return {cid: self._payloads[cid] for cid in chunk_ids if cid in self._payloads}

    def close(self) -> None:
        pass


class FixedReranker:
    """按 chunk 正文关键字返回固定分数，用来断言排序真的被 rerank 改写。"""

    name = "fake"
    model = "fixed"

    def __init__(self, scores: dict[str, float], raise_error: bool = False) -> None:
        self._scores = scores
        self._raise = raise_error
        self.calls: list[list[str]] = []

    def score(self, query: str, documents) -> RerankScores:
        self.calls.append(list(documents))
        if self._raise:
            raise RerankError("模拟 rerank 服务不可用")
        return RerankScores(
            scores=tuple(self._scores.get(text, 0.0) for text in documents),
            provider=self.name,
            model=self.model,
            latency_ms=1.0,
        )

    def close(self) -> None:
        pass


def payload(chunk_id: str, document_id: str = "doc-a", text: str = "正文") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "title": "示例文档",
        "heading_path": ["示例文档", "小节"],
        "text": text,
        "source_url": "https://example.org/doc",
    }


def build_service(
    vector_hits: list[tuple[str, float]],
    bm25_hits: list[tuple[str, float]],
    payloads: dict[str, dict],
    reranker=None,
    top_k: int = 5,
    min_score: float = 0.60,
    bm25_strong_score: float = 12.0,
    max_per_document: int = 2,
    rerank_min_score: float = 0.30,
    rerank_candidates: int = 20,
) -> RetrievalService:
    service = RetrievalService(
        top_k=top_k,
        min_score=min_score,
        bm25_strong_score=bm25_strong_score,
        max_per_document=max_per_document,
        reranker=reranker,
        rerank_min_score=rerank_min_score,
        rerank_candidates=rerank_candidates,
    )
    service._loaded = True
    service._bm25 = FakeBM25(bm25_hits)
    service._store = FakeStore(vector_hits, payloads)
    service._embedder = FakeEmbedder()
    service._documents = {}
    return service


class NormalizeScoresTest(unittest.TestCase):
    def test_probability_scores_pass_through(self):
        self.assertEqual(normalize_scores([0.1, 0.9], ["a", "b"]), (0.1, 0.9))

    def test_logits_are_squashed(self):
        scores = normalize_scores([-2.0, 0.0, 2.0], ["a", "b", "c"])
        self.assertAlmostEqual(scores[1], 0.5, places=6)
        self.assertLess(scores[0], 0.2)
        self.assertGreater(scores[2], 0.8)

    def test_length_mismatch_raises(self):
        with self.assertRaises(RerankError):
            normalize_scores([0.5], ["a", "b"])


class RerankIntegrationTest(unittest.TestCase):
    def test_rerank_can_rescue_candidate_below_vector_threshold(self):
        # 向量 0.55 低于门槛，但 rerank 认为它就是答案：有 rerank 时不该被门槛误杀。
        reranker = FixedReranker({"谷类每天 200g": 0.88, "红烧肉做法": 0.02})
        service = build_service(
            vector_hits=[("a", 0.55), ("b", 0.72)],
            bm25_hits=[],
            payloads={"a": payload("a", text="谷类每天 200g"), "b": payload("b", text="红烧肉做法")},
            reranker=reranker,
        )
        outcome = service.search("主食吃多少克")
        self.assertTrue(outcome.answerable)
        self.assertEqual(outcome.hits[0].chunk_id, "a")
        self.assertAlmostEqual(outcome.hits[0].rerank_score, 0.88, places=6)
        self.assertEqual(outcome.degraded["rerank"], "fake:fixed")

    def test_rerank_threshold_rejects_everything_below(self):
        reranker = FixedReranker({"正文": 0.12})
        service = build_service(
            vector_hits=[("a", 0.80)],
            bm25_hits=[("a", 14.0)],
            payloads={"a": payload("a")},
            reranker=reranker,
            rerank_min_score=0.30,
        )
        outcome = service.search("今天天气怎么样")
        self.assertFalse(outcome.answerable)
        self.assertEqual(outcome.hits, [])

    def test_rerank_failure_falls_back_to_gate(self):
        reranker = FixedReranker({}, raise_error=True)
        service = build_service(
            vector_hits=[("a", 0.80)],
            bm25_hits=[],
            payloads={"a": payload("a")},
            reranker=reranker,
        )
        outcome = service.search("蔬菜吃多少")
        self.assertTrue(outcome.answerable)
        self.assertEqual(outcome.degraded["rerank"], "failed")
        self.assertIn("模拟", outcome.degraded["reason"])

    def test_rerank_candidate_cap_limits_input(self):
        vector_hits = [(f"c{index}", 0.90 - index * 0.01) for index in range(8)]
        payloads = {cid: payload(cid, text=f"正文{cid}") for cid, _ in vector_hits}
        reranker = FixedReranker({f"正文{cid}": 0.5 for cid, _ in vector_hits})
        service = build_service(
            vector_hits=vector_hits,
            bm25_hits=[],
            payloads=payloads,
            reranker=reranker,
            rerank_candidates=3,
        )
        service.search("蔬菜")
        self.assertEqual(len(reranker.calls[0]), 3)
        self.assertEqual(service.search("蔬菜").candidates["prepared"], 3)

    def test_rerank_sees_body_text_only(self):
        # 章节标题不进 rerank 输入：实测带标题会压低正确片段的分数。
        reranker = FixedReranker({"正文": 0.9})
        service = build_service(
            vector_hits=[("a", 0.80)],
            bm25_hits=[],
            payloads={"a": payload("a", text="正文")},
            reranker=reranker,
        )
        service.search("蔬菜")
        self.assertEqual(reranker.calls[0], ["正文"])

    def test_absent_reranker_keeps_gate_behaviour(self):
        service = build_service(
            vector_hits=[("a", 0.55)],
            bm25_hits=[],
            payloads={"a": payload("a")},
            reranker=None,
        )
        outcome = service.search("蔬菜")
        self.assertFalse(outcome.answerable)
        self.assertEqual(outcome.degraded["rerank"], "not_configured")


class _StubRerankHandler(BaseHTTPRequestHandler):
    """模拟 TEI：故意打乱返回顺序，验证客户端按 index 回填而不是按数组顺序。"""

    def do_POST(self) -> None:  # noqa: N802 - http.server 的命名约定
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        texts = body.get("texts") or []
        items = [
            {"index": index, "score": 0.1 * (index + 1)}
            for index in range(len(texts))
        ]
        items.reverse()
        encoded = json.dumps(items).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args) -> None:  # 静默，避免测试输出被访问日志淹没
        pass


class HttpRerankerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = HTTPServer(("127.0.0.1", 0), _StubRerankHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_scores_follow_index_not_array_order(self):
        reranker = HttpReranker(self.url, model="stub", timeout_seconds=2.0)
        try:
            result = reranker.score("问题", ["a", "b", "c"])
        finally:
            reranker.close()
        self.assertEqual([round(value, 3) for value in result.scores], [0.1, 0.2, 0.3])

    def test_unreachable_service_raises_rerank_error(self):
        reranker = HttpReranker("http://127.0.0.1:1", timeout_seconds=0.5)
        try:
            with self.assertRaises(RerankError):
                reranker.score("问题", ["a"])
        finally:
            reranker.close()


class FactoryAndLocalTest(unittest.TestCase):
    @staticmethod
    def _settings(**overrides) -> Settings:
        """Settings 有 5 个必填字段，测试里只关心 rerank 相关项。"""
        base = dict(
            deepseek_api_key="",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-chat",
            deepseek_timeout_seconds=1.0,
            deepseek_input_price_per_million=0.0,
            deepseek_output_price_per_million=0.0,
        )
        base.update(overrides)
        return Settings(**base)

    def test_none_provider_returns_none(self):
        self.assertIsNone(build_reranker(self._settings(rerank_provider="none")))

    def test_unknown_provider_raises(self):
        with self.assertRaises(RerankError):
            build_reranker(self._settings(rerank_provider="bogus"))

    def test_missing_model_dir_raises(self):
        with self.assertRaises(RerankError):
            LocalReranker(Path("does-not-exist-model"))


if __name__ == "__main__":
    unittest.main()
