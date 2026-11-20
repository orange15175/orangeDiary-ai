"""Ollama Embedding 客户端的请求载荷测试（不起真实服务）。"""

from __future__ import annotations

import unittest

from app.rag.embedding.ollama import EmbeddingError, OllamaEmbedder


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"embeddings": [[0.1, 0.2]]}

    def json(self) -> dict:
        return self._payload


class CapturingClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.payloads: list[dict] = []

    def post(self, url: str, json: dict) -> FakeResponse:  # noqa: A002 - 与 httpx 签名一致
        self.payloads.append(json)
        return self.response

    def close(self) -> None:
        pass


def embedder_with(client: CapturingClient, **kwargs) -> OllamaEmbedder:
    embedder = OllamaEmbedder(**kwargs)
    embedder._client = client  # noqa: SLF001 - 测试替换网络层
    return embedder


class OllamaEmbedderTest(unittest.TestCase):
    def test_keep_alive_is_sent_by_default(self):
        client = CapturingClient(FakeResponse())
        embedder = embedder_with(client)
        embedder.embed(["减重期间主食吃多少克"])
        self.assertEqual(client.payloads[0]["keep_alive"], "30m")
        self.assertEqual(client.payloads[0]["model"], "bge-m3")

    def test_empty_keep_alive_omits_field(self):
        client = CapturingClient(FakeResponse())
        embedder = embedder_with(client, keep_alive="")
        embedder.embed(["蔬菜"])
        self.assertNotIn("keep_alive", client.payloads[0])

    def test_custom_keep_alive_passes_through(self):
        client = CapturingClient(FakeResponse())
        embedder = embedder_with(client, keep_alive="24h")
        embedder.embed(["蔬菜"])
        self.assertEqual(client.payloads[0]["keep_alive"], "24h")

    def test_dimension_mismatch_raises(self):
        embedder = embedder_with(CapturingClient(FakeResponse(payload={"embeddings": [[0.1, 0.2]]})))
        embedder.embed(["蔬菜"])
        embedder._client = CapturingClient(  # noqa: SLF001
            FakeResponse(payload={"embeddings": [[0.1, 0.2, 0.3]]})
        )
        with self.assertRaises(EmbeddingError):
            embedder.embed(["肉类"])

    def test_non_200_raises(self):
        embedder = embedder_with(CapturingClient(FakeResponse(status_code=500)))
        with self.assertRaises(EmbeddingError):
            embedder.embed(["蔬菜"])


if __name__ == "__main__":
    unittest.main()
