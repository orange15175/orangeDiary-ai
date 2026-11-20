"""Embedding 适配器：调用本地 Ollama 的 bge-m3。

维度不信任配置，首次调用实测；批接口不可用时按小批量退让，不做无限并发。
"""

from __future__ import annotations

import httpx


class EmbeddingError(RuntimeError):
    """Embedding 失败；调用方必须显式报告，不能静默写入空向量。"""


class OllamaEmbedder:
    """最小的 Ollama Embedding 客户端，复用连接并限制批量。"""

    def __init__(
        self,
        model: str = "bge-m3",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 120.0,
        batch_size: int = 8,
        keep_alive: str = "30m",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.batch_size = max(1, batch_size)
        # Ollama 默认空闲 5 分钟就卸载模型，下次请求要重新加载（bge-m3 实测 5~6s）。
        # 在线检索的单次预算只有 2s 量级，这里显式保活，避免"隔一会儿问第一句就超时"。
        self.keep_alive = keep_alive
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_seconds))
        self._dimension: int | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaEmbedder":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            payload: dict = {"model": self.model, "input": texts}
            if self.keep_alive:
                payload["keep_alive"] = self.keep_alive
            response = self._client.post(f"{self.base_url}/api/embed", json=payload)
        except httpx.HTTPError as error:
            raise EmbeddingError(f"Ollama 请求失败: {type(error).__name__}") from error
        if response.status_code != 200:
            raise EmbeddingError(f"Ollama 返回 {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise EmbeddingError("Ollama 返回非 JSON") from error
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingError("Ollama 返回的向量数量与输入不一致")
        return embeddings

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，返回与输入等长的向量列表。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(batch))
        dimension = len(vectors[0])
        if self._dimension is None:
            # 实测维度写入实例，后续批次必须一致，防止混用不兼容向量。
            self._dimension = dimension
        elif dimension != self._dimension:
            raise EmbeddingError(f"向量维度不一致: {dimension} != {self._dimension}")
        return vectors

    def dimension(self) -> int:
        """探测并缓存模型输出维度。"""
        if self._dimension is None:
            self.embed(["维度探测"])
        return self._dimension or 0
