"""Qdrant REST 客户端。

封装入库所需的最小能力（确认集合、按文档删除旧片段、批量写入、统计），
以及在线检索需要的向量查询。
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("orange-diary-ai.vector")


class VectorStoreError(RuntimeError):
    """向量库不可用或返回异常。"""


class QdrantStore:
    def __init__(self, base_url: str = "http://localhost:6333", timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_seconds))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QdrantStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def collection_info(self, name: str) -> dict | None:
        """返回集合信息；不存在返回 None，其他错误必须抛出。"""
        response = self._client.get(f"{self.base_url}/collections/{name}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise VectorStoreError(f"读取集合失败: {response.status_code}")
        return response.json().get("result") or {}

    def ensure_collection(self, name: str, dimension: int) -> None:
        """不存在才创建；已存在时校验维度，避免写入不兼容向量。"""
        info = self.collection_info(name)
        if info is None:
            response = self._client.put(
                f"{self.base_url}/collections/{name}",
                json={
                    "vectors": {"size": dimension, "distance": "Cosine"},
                    "on_disk_payload": True,
                },
            )
            if response.status_code not in (200, 201):
                raise VectorStoreError(f"创建集合失败: {response.status_code} {response.text[:200]}")
            return
        params = info.get("config", {}).get("params", {})
        size = params.get("vectors", {}).get("size")
        if isinstance(size, int) and size != dimension:
            raise VectorStoreError(
                f"集合 {name} 维度为 {size}，与 Embedding 维度 {dimension} 不一致，需重建索引"
            )

    def delete_document(self, name: str, document_id: str) -> None:
        """文档更新前清理旧片段，避免新旧知识同时被召回。"""
        response = self._client.post(
            f"{self.base_url}/collections/{name}/points/delete?wait=true",
            json={"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        )
        if response.status_code != 200:
            raise VectorStoreError(f"删除旧片段失败: {response.status_code}")

    def upsert(self, name: str, points: list[dict], batch_size: int = 64) -> int:
        """按批写入；返回写入点数。"""
        total = 0
        for start in range(0, len(points), batch_size):
            batch = points[start : start + batch_size]
            response = self._client.put(
                f"{self.base_url}/collections/{name}/points?wait=true",
                json={"points": batch},
            )
            if response.status_code not in (200, 201):
                raise VectorStoreError(f"写入失败: {response.status_code} {response.text[:200]}")
            total += len(batch)
        return total

    def count(self, name: str) -> int:
        response = self._client.post(f"{self.base_url}/collections/{name}/points/count", json={"exact": True})
        if response.status_code != 200:
            raise VectorStoreError(f"统计失败: {response.status_code}")
        return int(response.json().get("result", {}).get("count", 0))

    def _post(self, url: str, body: dict):
        """读请求带一次连接级重试。

        Qdrant 在容器资源抖动时会直接断开连接（RemoteProtocolError），一次重试能盖住；
        但 **ReadTimeout 不重试**——超时说明下游已经慢，再试只会把延迟翻倍，
        并且必然顶破 Java 侧 6s 的检索预算。
        """
        try:
            return self._client.post(url, json=body)
        except httpx.ReadTimeout:
            raise
        except httpx.TransportError as first:
            log.warning("Qdrant 连接中断，重试一次：%s", first)
            try:
                return self._client.post(url, json=body)
            except httpx.TransportError as second:
                raise VectorStoreError(f"Qdrant 连接失败（已重试一次）: {second}") from first

    def search(self, name: str, vector: list[float], limit: int = 20, domain: str | None = None) -> list[dict]:
        """向量检索；返回 [{chunk_id, score, payload}]，按分数降序。

        不设 score_threshold：阈值判断交给上层证据过滤，避免这里悄悄吞掉候选。
        `domain` 非空时在 Qdrant 侧过滤 `knowledge_domain`，**在候选阶段就限定**——
        先混搜再过滤会让相关菜谱在 Top-K 阶段就被营养文献挤掉。
        """
        body: dict = {"vector": vector, "limit": max(1, limit), "with_payload": True}
        if domain:
            body["filter"] = {
                "must": [{"key": "knowledge_domain", "match": {"value": domain}}]
            }
        response = self._post(f"{self.base_url}/collections/{name}/points/search", body)
        if response.status_code == 404:
            raise VectorStoreError(f"集合不存在: {name}")
        if response.status_code != 200:
            raise VectorStoreError(f"检索失败: {response.status_code} {response.text[:200]}")
        hits: list[dict] = []
        for item in response.json().get("result") or []:
            payload = item.get("payload") or {}
            hits.append(
                {
                    "chunk_id": str(payload.get("chunk_id") or ""),
                    "score": float(item.get("score") or 0.0),
                    "payload": payload,
                }
            )
        return [hit for hit in hits if hit["chunk_id"]]

    def fetch_by_chunk_ids(self, name: str, chunk_ids: list[str], batch_size: int = 32) -> dict[str, dict]:
        """按业务 chunk_id 批量补取 payload。

        BM25 召回的片段可能不在向量 Top-N 内，融合后需要补正文；
        点的存储 ID 是 uuid5，真实 chunk_id 在 payload 里，因此用 payload 过滤。
        """
        wanted = [cid for cid in dict.fromkeys(chunk_ids) if cid]
        found: dict[str, dict] = {}
        for start in range(0, len(wanted), batch_size):
            batch = wanted[start : start + batch_size]
            response = self._post(
                f"{self.base_url}/collections/{name}/points/scroll",
                {
                    "filter": {"should": [{"key": "chunk_id", "match": {"value": cid}} for cid in batch]},
                    "limit": len(batch),
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            if response.status_code != 200:
                raise VectorStoreError(f"补取片段失败: {response.status_code} {response.text[:200]}")
            for point in (response.json().get("result") or {}).get("points") or []:
                payload = point.get("payload") or {}
                chunk_id = str(payload.get("chunk_id") or "")
                if chunk_id:
                    found[chunk_id] = payload
        return found


