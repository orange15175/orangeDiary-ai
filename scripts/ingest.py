"""受控入库 CLI。

示例：
    python scripts/ingest.py --index-version knowledge-v1
    python scripts/ingest.py --index-version knowledge-v2 --rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.embedding.ollama import EmbeddingError, OllamaEmbedder  # noqa: E402
from app.rag.ingestion.manifest import ManifestError  # noqa: E402
from app.rag.pipeline import IngestPipeline  # noqa: E402
from app.rag.retrieval.vector import QdrantStore, VectorStoreError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建 Orange Diary 健康饮食知识索引")
    parser.add_argument("--index-version", default="knowledge-v1", help="索引版本，写入 manifest 与 BM25 文件名")
    parser.add_argument("--collection", default="orange_diary_knowledge_v1", help="Qdrant 集合名称")
    parser.add_argument("--sources", default=str(PROJECT_ROOT / "knowledge" / "sources"), help="Markdown 文献目录")
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "knowledge" / "manifest.json"), help="来源清单")
    parser.add_argument("--index-dir", default=str(PROJECT_ROOT / "knowledge" / "index"), help="索引输出目录")
    parser.add_argument("--chunk-size", type=int, default=700, help="片段目标长度（字符）")
    parser.add_argument("--overlap", type=int, default=100, help="相邻片段重叠字符数")
    parser.add_argument("--rebuild", action="store_true", help="忽略构建状态，全部重新解析与向量化")
    parser.add_argument("--embedding-model", default="bge-m3", help="Ollama Embedding 模型")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama 地址")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant 地址")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with OllamaEmbedder(model=args.embedding_model, base_url=args.ollama_url) as embedder, \
                QdrantStore(base_url=args.qdrant_url) as store:
            pipeline = IngestPipeline(
                sources_dir=Path(args.sources),
                manifest_path=Path(args.manifest),
                index_dir=Path(args.index_dir),
                embedder=embedder,
                store=store,
                collection=args.collection,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
            )
            result = pipeline.run(args.index_version, rebuild=args.rebuild)
    except (EmbeddingError, VectorStoreError, ManifestError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

    summary = {
        "status": "ok" if not result.errors else "partial",
        "index_version": result.index_version,
        "collection": result.collection,
        "documents_total": result.documents_total,
        "documents_processed": result.documents_processed,
        "documents_skipped": result.documents_skipped,
        "chunks_written": result.chunks_written,
        "bm25_chunks": result.bm25_chunks,
        "points_in_collection": result.points_in_collection,
        "errors": result.errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
