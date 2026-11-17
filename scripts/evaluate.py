"""召回评估：向量单通道 / BM25 单通道 / RRF 混合三模式对比。

用途：M3 验收。用同一份问题集、同一份索引，分别跑三种召回策略，比较
文档命中率、证据命中率、MRR 与拒答正确率，避免"看起来能答"的自评。

运行：
    python scripts/evaluate.py                     # 默认 k=3，结果写入 knowledge/eval/
    python scripts/evaluate.py --top-k 5 --quiet    # 只看汇总表

判定口径（写进报告，避免事后解释）：
- 文档命中：Top-K 里出现任一 expected_doc_ids；
- 证据命中：Top-K 里有一条同时来自期望文档且含 evidence_keywords 之一；
- MRR：首个命中期望文档的排名的倒数，未命中记 0；
- 拒答：不可答题在阈值过滤后仍有结果即算"误答"。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.embedding.ollama import OllamaEmbedder  # noqa: E402
from app.rag.retrieval.bm25 import BM25Index  # noqa: E402
from app.rag.retrieval.vector import QdrantStore  # noqa: E402
from app.rag.service import RetrievalService  # noqa: E402

DEFAULT_COLLECTION = "orange_diary_knowledge_v2"
DEFAULT_INDEX_VERSION = "knowledge-v2"
QUESTIONS_PATH = ROOT / "knowledge" / "eval" / "questions.json"
CHUNKS_DIR = ROOT / "knowledge" / "index" / "chunks"
# 与服务端判定保持一致：向量门槛 0.60；关键词单独成答的门槛更高（12.0）。
# bm25_only 作为对照基线仍用 6.0，代表关键词通道自身的排序能力。
VECTOR_MIN_SCORE = 0.60
BM25_STRONG_SCORE = 12.0
BM25_ONLY_GATE = 6.0


@dataclass(frozen=True)
class EvalConfig:
    """评估对象：索引版本与对应集合，用于新旧索引对比。"""

    index_version: str = DEFAULT_INDEX_VERSION
    collection: str = DEFAULT_COLLECTION

    @property
    def bm25_path(self) -> Path:
        return ROOT / "knowledge" / "index" / "bm25" / f"{self.index_version}.json"


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]


def load_all_chunks() -> dict[str, dict]:
    """读取全部片段，用于校验评估集标注是否成立（关键词确实存在于期望文档里）。"""
    chunks: dict[str, dict] = {}
    for path in CHUNKS_DIR.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        document_id = body["document_id"]
        for chunk in body["chunks"]:
            # document_id 在文件层级，补进片段字典方便按文档过滤。
            chunks[chunk["chunk_id"]] = {**chunk, "document_id": document_id}
    return chunks


def validate_annotations(questions: list[dict], chunks: dict[str, dict]) -> list[str]:
    """标注自检：期望文档不存在、或关键词在该文档里根本找不到，都说明题目写错了。"""
    warnings: list[str] = []
    known_docs = {chunk["document_id"] for chunk in chunks.values()}
    for question in questions:
        if not question["answerable"]:
            continue
        for doc_id in question["expected_doc_ids"]:
            if doc_id not in known_docs:
                warnings.append(f"{question['id']}: 期望文档 {doc_id} 不在索引里")
        texts = [
            chunk["text"]
            for chunk in chunks.values()
            if chunk["document_id"] in question["expected_doc_ids"]
        ]
        if question["evidence_keywords"] and not any(
            keyword in text for keyword in question["evidence_keywords"] for text in texts
        ):
            warnings.append(f"{question['id']}: 证据词在期望文档中均未出现，题目标注可能不成立")
    return warnings


def rank_of_first_doc(doc_ids: list[str], expected: set[str]) -> int | None:
    for index, doc_id in enumerate(doc_ids, start=1):
        if doc_id in expected:
            return index
    return None


def score_mode(name: str, rows: list[dict]) -> dict:
    answerable_rows = [row for row in rows if row["answerable"]]
    unanswerable_rows = [row for row in rows if not row["answerable"]]
    count = len(answerable_rows)
    doc_hits = sum(1 for row in rows if row["answerable"] and row["doc_hit"])
    evidence_hits = sum(1 for row in rows if row["answerable"] and row["evidence_hit"])
    reciprocal = [1.0 / row["doc_rank"] for row in rows if row["answerable"] and row["doc_rank"]]
    latencies = [row["latency_ms"] for row in rows]

    accepted_answerable = sum(1 for row in answerable_rows if row["accepted"])
    accepted_unanswerable = sum(1 for row in unanswerable_rows if row["accepted"])
    total = len(rows)
    correct = accepted_answerable + (len(unanswerable_rows) - accepted_unanswerable)

    # 负例分两类看：完全无关的话题检索层就该拒绝；用药/诊断类问题检索层
    # 往往能命中同主题资料，拒绝责任在意图层与提示词层，不能混在一个数里看。
    breakdown: dict[str, dict] = {}
    for row in unanswerable_rows:
        key = row.get("refusal_type") or "off_topic"
        bucket = breakdown.setdefault(key, {"total": 0, "accepted": 0})
        bucket["total"] += 1
        if row["accepted"]:
            bucket["accepted"] += 1
    for key, bucket in breakdown.items():
        bucket["false_accept_rate"] = round(bucket["accepted"] / bucket["total"], 4) if bucket["total"] else 0.0

    return {
        "mode": name,
        "doc_hit_rate": round(doc_hits / count, 4) if count else 0.0,
        "evidence_hit_rate": round(evidence_hits / count, 4) if count else 0.0,
        "mrr": round(sum(reciprocal) / count, 4) if count else 0.0,
        "answerable_recall": round(accepted_answerable / count, 4) if count else 0.0,
        "false_accept_rate": round(accepted_unanswerable / len(unanswerable_rows), 4)
        if unanswerable_rows
        else 0.0,
        "decision_accuracy": round(correct / total, 4) if total else 0.0,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "latency_max_ms": round(max(latencies), 1) if latencies else 0.0,
        "refusal_breakdown": breakdown,
    }


def run_vector_only(
    question: dict,
    embedder: OllamaEmbedder,
    store: QdrantStore,
    top_k: int,
    config: EvalConfig,
) -> dict:
    started = time.perf_counter()
    vector = embedder.embed([question["query"]])[0]
    hits = store.search(config.collection, vector, limit=top_k * 4)
    accepted_hits = [hit for hit in hits if hit["score"] >= VECTOR_MIN_SCORE][:top_k]
    latency = (time.perf_counter() - started) * 1000
    return build_row(question, accepted_hits, latency)


def run_bm25_only(
    question: dict,
    bm25: BM25Index,
    store: QdrantStore,
    top_k: int,
    config: EvalConfig,
) -> dict:
    started = time.perf_counter()
    hits = bm25.search(question["query"], top_k=top_k * 4)
    accepted = [(chunk_id, score) for chunk_id, score in hits if score >= BM25_ONLY_GATE][:top_k]
    payloads = store.fetch_by_chunk_ids(config.collection, [chunk_id for chunk_id, _ in accepted])
    latency = (time.perf_counter() - started) * 1000
    rows = [
        {"chunk_id": chunk_id, "score": score, "payload": payloads.get(chunk_id) or {}}
        for chunk_id, score in accepted
    ]
    return build_row(question, rows, latency)


def run_hybrid(service: RetrievalService, question: dict, top_k: int) -> dict:
    started = time.perf_counter()
    outcome = service.search(question["query"], top_k=top_k)
    latency = (time.perf_counter() - started) * 1000
    rows = [{"chunk_id": hit.chunk_id, "score": hit.score, "payload": {
        "document_id": hit.document_id, "text": hit.text
    }} for hit in outcome.hits]
    row = build_row(question, rows, latency)
    row["accepted"] = outcome.answerable
    return row


def build_row(question: dict, hits: list[dict], latency_ms: float) -> dict:
    expected = set(question["expected_doc_ids"])
    doc_ids = [str(hit["payload"].get("document_id") or "") for hit in hits]
    doc_rank = rank_of_first_doc(doc_ids, expected) if expected else None
    evidence_hit = False
    for hit in hits:
        payload = hit["payload"]
        if str(payload.get("document_id") or "") not in expected:
            continue
        text = str(payload.get("text") or "")
        if not question["evidence_keywords"] or any(
            keyword in text for keyword in question["evidence_keywords"]
        ):
            evidence_hit = True
            break
    return {
        "id": question["id"],
        "query": question["query"],
        "category": question["category"],
        "answerable": question["answerable"],
        "refusal_type": question.get("refusal_type", ""),
        "accepted": bool(hits),
        "doc_hit": doc_rank is not None,
        "doc_rank": doc_rank,
        "evidence_hit": evidence_hit,
        "top1_doc": doc_ids[0] if doc_ids else "",
        "top_docs": doc_ids,
        "latency_ms": round(latency_ms, 1),
    }


def print_table(scores: list[dict]) -> None:
    headers = [
        ("模式", "mode"),
        ("文档命中@k", "doc_hit_rate"),
        ("证据命中@k", "evidence_hit_rate"),
        ("MRR", "mrr"),
        ("可答召回", "answerable_recall"),
        ("误答率", "false_accept_rate"),
        ("判定准确率", "decision_accuracy"),
        ("P50 (ms)", "latency_p50_ms"),
        ("Max (ms)", "latency_max_ms"),
    ]
    print("| " + " | ".join(title for title, _ in headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for score in scores:
        print("| " + " | ".join(str(score[key]) for _, key in headers) + " |")


def print_refusal_breakdown(scores: list[dict]) -> None:
    """负例按类型拆开看：无关话题与专业建议问题的可拒答性不同。"""
    print("\n负例误答拆分（误答数/该类型总数）：")
    print("| 模式 | 完全无关 (off_topic) | 需专业判断 (professional_advice) |")
    print("|---|---|---|")
    for score in scores:
        cells = []
        for key in ("off_topic", "professional_advice"):
            bucket = score["refusal_breakdown"].get(key)
            cells.append(f"{bucket['accepted']}/{bucket['total']}" if bucket else "-")
        print(f"| {score['mode']} | {cells[0]} | {cells[1]} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="召回评估（向量 / BM25 / RRF 混合）")
    parser.add_argument("--top-k", type=int, default=3, help="评估截断位置，默认 3")
    parser.add_argument("--quiet", action="store_true", help="只打印汇总表")
    parser.add_argument("--output", default=None, help="结果 JSON 路径")
    parser.add_argument("--index-version", default=DEFAULT_INDEX_VERSION, help="索引版本，如 knowledge-v3")
    parser.add_argument("--collection", default=None, help="Qdrant 集合名，默认按索引版本推导")
    parser.add_argument("--rerank", action="store_true", help="额外跑一遍 rerank 模式（需要本地模型）")
    parser.add_argument(
        "--rerank-model-path",
        default=str(ROOT / "knowledge" / "models" / "bge-reranker-base"),
    )
    parser.add_argument("--rerank-min-score", type=float, default=0.75)
    parser.add_argument("--rerank-candidates", type=int, default=12)
    parser.add_argument("--rerank-max-length", type=int, default=320)
    args = parser.parse_args()
    config = EvalConfig(
        index_version=args.index_version,
        collection=args.collection or f"orange_diary_{args.index_version.replace('knowledge-', 'knowledge_')}",
    )

    questions = load_questions()
    chunks = load_all_chunks()
    warnings = validate_annotations(questions, chunks)
    for warning in warnings:
        print(f"[标注自检] {warning}", file=sys.stderr)

    bm25 = BM25Index.load(config.bm25_path)
    embedder = OllamaEmbedder()
    store = QdrantStore()
    service = RetrievalService(
        collection=config.collection,
        bm25_path=config.bm25_path,
        index_manifest_path=ROOT / "knowledge" / "index" / "manifest.json",
        min_score=VECTOR_MIN_SCORE,
        bm25_strong_score=BM25_STRONG_SCORE,
        # 评估必须关缓存：命中缓存会跳过检索与 rerank，P50 会被算成 0.2ms。
        cache_enabled=False,
    )

    modes = {"vector_only": [], "bm25_only": [], "hybrid_rrf": []}
    service_rerank = None
    if args.rerank:
        # rerank 模式独立建一个服务实例：两者只差在排序与证据门槛，索引完全共用。
        from app.rag.rerank.local import LocalReranker

        service_rerank = RetrievalService(
            collection=config.collection,
            bm25_path=config.bm25_path,
            index_manifest_path=ROOT / "knowledge" / "index" / "manifest.json",
            min_score=VECTOR_MIN_SCORE,
            bm25_strong_score=BM25_STRONG_SCORE,
            reranker=LocalReranker(args.rerank_model_path, max_length=args.rerank_max_length),
            rerank_candidates=args.rerank_candidates,
            rerank_min_score=args.rerank_min_score,
            cache_enabled=False,
        )
        modes["hybrid_rerank"] = []
    try:
        for question in questions:
            modes["vector_only"].append(run_vector_only(question, embedder, store, args.top_k, config))
            modes["bm25_only"].append(run_bm25_only(question, bm25, store, args.top_k, config))
            modes["hybrid_rrf"].append(run_hybrid(service, question, args.top_k))
            if service_rerank is not None:
                modes["hybrid_rerank"].append(run_hybrid(service_rerank, question, args.top_k))
    finally:
        service.close()
        if service_rerank is not None:
            service_rerank.close()

    scores = [score_mode(name, rows) for name, rows in modes.items()]
    print(f"\n评估集：{len(questions)} 题（可答 {sum(1 for q in questions if q['answerable'])} / "
          f"不可答 {sum(1 for q in questions if not q['answerable'])}），K={args.top_k}，"
          f"索引 {config.index_version}（{config.collection}）\n")
    print_table(scores)
    print_refusal_breakdown(scores)

    if not args.quiet:
        print("\n--- 混合模式逐题结果 ---")
        for row in modes["hybrid_rrf"]:
            flag = "OK " if (row["doc_hit"] if row["answerable"] else not row["accepted"]) else "MISS"
            print(
                f"{flag} {row['id']} [{row['category']}] {row['query']} -> "
                f"doc={row['top1_doc'] or '(none)'} rank={row['doc_rank']} accepted={row['accepted']}"
            )

        print("\n--- 混合模式失败明细 ---")
        failed = [
            row
            for row in modes["hybrid_rrf"]
            if (row["answerable"] and not row["doc_hit"]) or (not row["answerable"] and row["accepted"])
        ]
        if not failed:
            print("无")
        for row in failed:
            print(f"{row['id']} {row['query']} | top_docs={row['top_docs']} | accepted={row['accepted']}")

    output = Path(args.output) if args.output else (
        ROOT / "knowledge" / "eval" / f"results-{date.today().isoformat()}-{config.index_version}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "index_version": config.index_version,
                "collection": config.collection,
                "top_k": args.top_k,
                "thresholds": {"vector_min_score": VECTOR_MIN_SCORE, "bm25_strong_score": BM25_STRONG_SCORE, "bm25_only_gate": BM25_ONLY_GATE},
                "rerank": {
                    "enabled": bool(args.rerank),
                    "model": Path(args.rerank_model_path).name if args.rerank else None,
                    "candidates": args.rerank_candidates,
                    "max_length": args.rerank_max_length,
                    "min_score": args.rerank_min_score,
                },
                "annotation_warnings": warnings,
                "scores": scores,
                "details": modes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
