"""离线入库编排：切块 → Embedding → Qdrant → BM25 索引。

与在线检索完全分离；聊天请求不会触发这里的任何步骤。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.rag.embedding.ollama import OllamaEmbedder
from app.rag.ingestion.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_markdown
from app.rag.ingestion.loader import LoaderError, read_document, strip_front_matter
from app.rag.ingestion.manifest import (
    load_build_state,
    load_manifest,
    save_build_state,
)
from app.rag.retrieval.bm25 import BM25Index
from app.rag.retrieval.vector import QdrantStore
from app.rag.schemas import CHUNKER_VERSION, INDEX_TEXT_VERSION, SCHEMA_VERSION, Chunk, content_hash

log = logging.getLogger("orange-diary-ai.ingest")


def _cached_index_text(item: dict) -> str:
    """缓存片段只存正文与标题路径，这里补回索引文本，保证增量与全量结果一致。"""
    heading = item.get("heading_path") or []
    text = item.get("text") or ""
    return f"{' > '.join(heading)}\n{text}" if heading else text


@dataclass
class IngestResult:
    index_version: str
    collection: str
    documents_total: int = 0
    documents_processed: int = 0
    documents_skipped: int = 0
    documents_removed: int = 0
    chunks_written: int = 0
    points_in_collection: int = 0
    bm25_chunks: int = 0
    errors: list[str] = field(default_factory=list)


class IngestPipeline:
    """一次构建产出一个可发布的索引版本。"""

    def __init__(
        self,
        sources_dir: Path,
        manifest_path: Path,
        index_dir: Path,
        embedder: OllamaEmbedder,
        store: QdrantStore,
        collection: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> None:
        self.sources_dir = sources_dir
        self.manifest_path = manifest_path
        self.index_dir = index_dir
        self.embedder = embedder
        self.store = store
        self.collection = collection
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.state_path = index_dir / "build_state.json"
        self.chunks_dir = index_dir / "chunks"
        self.bm25_dir = index_dir / "bm25"

    def _chunk_cache_path(self, document_id: str) -> Path:
        return self.chunks_dir / f"{document_id}.json"

    def _load_cached_chunks(self, document_id: str) -> list[dict]:
        path = self._chunk_cache_path(document_id)
        if not path.is_file():
            return []
        import json

        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return body.get("chunks") or []

    def _save_cached_chunks(self, document_id: str, chunks: list[Chunk]) -> None:
        import json

        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "document_id": document_id,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "heading_path": list(chunk.heading_path),
                }
                for chunk in chunks
            ],
        }
        self._chunk_cache_path(document_id).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def run(self, index_version: str, rebuild: bool = False) -> IngestResult:
        """带任务状态的入口：running → succeeded / failed，状态写进 build_state。

        状态是给人看排障用的：失败时至少能知道是哪一版、什么错误，而不是只有一份半成品索引。
        """
        self._mark_status(index_version, "running")
        try:
            result = self._run(index_version, rebuild)
        except Exception as error:
            self._mark_status(
                index_version,
                "failed",
                error=f"{type(error).__name__}: {error}"[:500],
            )
            raise
        self._mark_status(
            index_version,
            "succeeded",
            detail={
                "documents_total": result.documents_total,
                "documents_processed": result.documents_processed,
                "documents_skipped": result.documents_skipped,
                "documents_removed": result.documents_removed,
                "chunks_written": result.chunks_written,
                "errors": result.errors[:10],
            },
        )
        return result

    def _mark_status(self, index_version: str, status: str, error: str | None = None,
                     detail: dict | None = None) -> None:
        state = load_build_state(self.state_path)
        entry = {
            "status": status,
            "index_version": index_version,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            entry["error"] = error
        if detail:
            entry.update(detail)
        state["last_run"] = entry
        save_build_state(self.state_path, state)

    def _run(self, index_version: str, rebuild: bool = False) -> IngestResult:
        documents = load_manifest(self.manifest_path)
        result = IngestResult(index_version=index_version, collection=self.collection, documents_total=len(documents))
        state = load_build_state(self.state_path)
        dimension = self.embedder.dimension()
        # 集合维度与当前 Embedding 不一致时直接失败，不允许混用不兼容向量。
        self.store.ensure_collection(self.collection, dimension)
        log.info("Embedding 维度实测 %s，集合 %s 就绪", dimension, self.collection)

        all_chunks: list[tuple[str, str]] = []
        # 每个域单独一份 BM25：候选阶段就要按域限定，混搜后再过滤会把相关菜谱挤掉。
        chunks_by_domain: dict[str, list[tuple[str, str]]] = {}
        for meta in documents:
            path = self.sources_dir / meta.file
            try:
                raw = read_document(path)
            except LoaderError as error:
                result.errors.append(f"{meta.document_id}: {error}")
                log.error("文档解析失败 %s: %s", meta.document_id, error)
                continue
            text = strip_front_matter(raw)
            current_hash = content_hash(text)
            recorded = state.get("documents", {}).get(meta.document_id) or {}
            unchanged = (
                not rebuild
                and recorded.get("content_hash") == current_hash
                and recorded.get("version") == meta.version
                # 切块或索引文本策略变化后，旧片段必须重建，不能沿用缓存。
                and recorded.get("chunker_version") == CHUNKER_VERSION
                and recorded.get("index_text_version") == INDEX_TEXT_VERSION
                # 新增 knowledge_domain 属于 payload 结构变更：旧点没有这个字段，
                # 继续跳过就会留下"过滤永远不命中"的索引。
                and recorded.get("schema_version") == SCHEMA_VERSION
            )
            if unchanged:
                cached = self._load_cached_chunks(meta.document_id)
                if cached:
                    result.documents_skipped += 1
                    items = [(item["chunk_id"], _cached_index_text(item)) for item in cached]
                    all_chunks.extend(items)
                    chunks_by_domain.setdefault(meta.domain, []).extend(items)
                    log.info("跳过未变化文档 %s（%s 片段）", meta.document_id, len(cached))
                    continue

            chunks = chunk_markdown(text, meta, chunk_size=self.chunk_size, overlap=self.overlap)
            if not chunks:
                result.errors.append(f"{meta.document_id}: 切块结果为空，未写入索引")
                log.error("切块结果为空 %s", meta.document_id)
                continue

            vectors = self.embedder.embed([chunk.index_text for chunk in chunks])
            points = [
                # Qdrant 只接受整数或 UUID 作为点 ID，chunk_id 保留在 payload 中。
                {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)), "vector": vector, "payload": chunk.payload()}
                for chunk, vector in zip(chunks, vectors)
            ]
            # 旧片段先删后写：文档更新后不会和新片段同时被召回。
            self.store.delete_document(self.collection, meta.document_id)
            written = self.store.upsert(self.collection, points)
            self._save_cached_chunks(meta.document_id, chunks)
            state.setdefault("documents", {})[meta.document_id] = {
                "content_hash": current_hash,
                "version": meta.version,
                "domain": meta.domain,
                "chunk_count": len(chunks),
                "embedding_model": self.embedder.model,
                "chunker_version": CHUNKER_VERSION,
                "index_text_version": INDEX_TEXT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
            result.documents_processed += 1
            result.chunks_written += written
            items = [(chunk.chunk_id, chunk.index_text) for chunk in chunks]
            all_chunks.extend(items)
            chunks_by_domain.setdefault(meta.domain, []).extend(items)
            log.info("已入库 %s（%s）：%s 片段", meta.document_id, meta.domain, written)

        # 源文件被删除（或从清单里移除）后，旧片段必须清掉，否则它会一直出现在检索结果里。
        current_ids = {meta.document_id for meta in documents}
        stale_ids = [doc_id for doc_id in state.get("documents", {}) if doc_id not in current_ids]
        for doc_id in stale_ids:
            self.store.delete_document(self.collection, doc_id)
            cached = self._chunk_cache_path(doc_id)
            if cached.is_file():
                cached.unlink()
            state["documents"].pop(doc_id, None)
            result.documents_removed += 1
            log.info("已清理不在清单中的文档 %s", doc_id)

        bm25 = BM25Index()
        bm25.build(all_chunks)
        bm25_path = self.bm25_dir / f"{index_version}.json"
        bm25.save(bm25_path)
        # 分域索引文件名必须与 `runtime.service_for` 的约定一致：`<domain>.<version>.json`。
        # 合并文件保留，供不指定域的运维接口与运行态统计使用。
        for domain, items in chunks_by_domain.items():
            domain_bm25 = BM25Index()
            domain_bm25.build(items)
            domain_bm25.save(self.bm25_dir / f"{domain}.{index_version}.json")
            log.info("已生成 %s 域 BM25：%s 片段", domain, len(items))
        result.bm25_chunks = len(all_chunks)
        result.points_in_collection = self.store.count(self.collection)

        save_build_state(self.state_path, state)
        self._write_index_manifest(index_version, documents, result, dimension)
        return result

    def _write_index_manifest(self, index_version: str, documents, result: IngestResult, dimension: int) -> None:
        """发布清单：记录本次索引的模型、维度、切块版本与文档哈希。"""
        import json

        state = load_build_state(self.state_path)
        payload = {
            "index_version": index_version,
            "collection": self.collection,
            "embedding_model": self.embedder.model,
            "embedding_dimension": dimension,
            "chunker_version": CHUNKER_VERSION,
            "index_text_version": INDEX_TEXT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "bm25_index": f"bm25/{index_version}.json",
            "documents": [
                {
                    "document_id": meta.document_id,
                    "title": meta.title,
                    "version": meta.version,
                    "domain": meta.domain,
                    "recipe_key": meta.recipe_key,
                    "source_url": meta.source_url,
                    "needs_review": meta.needs_review,
                    "content_hash": (state.get("documents", {}).get(meta.document_id) or {}).get("content_hash"),
                    "chunk_count": (state.get("documents", {}).get(meta.document_id) or {}).get("chunk_count"),
                }
                for meta in documents
            ],
            # 每域片段数写进发布清单：检索前就能判断某个域是否已建索引。
            "domains": {
                domain: sum(
                    (state.get("documents", {}).get(meta.document_id) or {}).get("chunk_count") or 0
                    for meta in documents
                    if meta.domain == domain
                )
                for domain in sorted({meta.domain for meta in documents})
            },
            "errors": result.errors,
        }
        path = self.index_dir / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
