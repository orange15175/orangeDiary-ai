"""HTTP rerank 客户端（HuggingFace Text Embeddings Inference / TEI 兼容协议）。

约定协议：
    POST {url}/rerank
    {"query": "...", "texts": ["...", "..."], "truncate": true, "raw_scores": false}
    -> [{"index": 0, "score": 0.98}, ...]

选 TEI 作为默认协议的理由：本地起一个 `text-embeddings-inference` 容器即可同时
提供 bge-m3 与 bge-reranker；换成云厂商时只需在网关侧做一次字段映射。
"""

from __future__ import annotations

import time
from typing import Sequence

import httpx

from app.rag.rerank.base import RerankError, RerankScores, normalize_scores


class HttpReranker:
    """把一个外部 rerank 服务包装成 `Reranker`。"""

    def __init__(
        self,
        base_url: str,
        model: str = "",
        timeout_seconds: float = 3.0,
        token: str = "",
        batch_size: int = 16,
    ) -> None:
        url = (base_url or "").rstrip("/")
        if not url:
            raise RerankError("RERANK_URL 为空")
        self.name = "http"
        self.model = model or "remote"
        self.batch_size = max(1, int(batch_size))
        self._endpoint = url if url.endswith("/rerank") else url + "/rerank"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # 检索是同步调用链，rerank 失败要尽快抛出来降级，不能挂在长连接上。
        self._client = httpx.Client(timeout=timeout_seconds, headers=headers)

    def score(self, query: str, documents: Sequence[str]) -> RerankScores:
        if not documents:
            return RerankScores(scores=(), provider=self.name, model=self.model, latency_ms=0.0)
        started = time.perf_counter()
        values: list[float] = []
        try:
            for start in range(0, len(documents), self.batch_size):
                batch = list(documents[start : start + self.batch_size])
                response = self._client.post(
                    self._endpoint,
                    json={"query": query, "texts": batch, "truncate": True, "raw_scores": False},
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, list):
                    raise RerankError("rerank 响应不是数组")
                # 服务端可能按分数重排后返回，必须按 index 回填，不能直接依赖顺序。
                batch_scores = [0.0] * len(batch)
                for item in body:
                    index = int(item.get("index", -1))
                    if 0 <= index < len(batch):
                        batch_scores[index] = float(item.get("score", 0.0))
                values.extend(batch_scores)
        except RerankError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RerankError(f"rerank 服务调用失败: {error}") from error
        return RerankScores(
            scores=normalize_scores(values, documents),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def close(self) -> None:
        self._client.close()
