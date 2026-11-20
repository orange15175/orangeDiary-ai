"""rerank 阈值与候选数调参：一次推理，离线扫阈值。

为什么要这样拆：每次改阈值都重跑 Embedding + cross-encoder 要几分钟，
而分数只要算一次就能反复用。脚本先跑一遍"不过滤"的检索，把每个候选的
rerank 分数落盘，再在同一份数据上扫阈值——结论可复现，也方便事后复核。

运行：
    python scripts/tune_rerank.py --candidates 20 --max-length 384
    python scripts/tune_rerank.py --from-dump knowledge/eval/rerank-scores.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.rerank.local import LocalReranker  # noqa: E402
from app.rag.service import RetrievalService  # noqa: E402
from scripts.evaluate import (  # noqa: E402
    build_row,
    load_all_chunks,
    load_questions,
    score_mode,
    validate_annotations,
)

THRESHOLDS = [
    0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50,
    0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90,
]


def load_dump(path: Path) -> tuple[dict, list[float], list[float]]:
    body = json.loads(path.read_text(encoding="utf-8"))
    return body["questions"], [], []


def run_inference(args) -> tuple[dict, list[float], list[float], dict]:
    """跑一遍不过滤的检索，拿到每个候选的 rerank 分数。"""
    reranker = LocalReranker(args.model_path, max_length=args.max_length, batch_size=args.candidates)
    service = RetrievalService(
        collection=args.collection,
        # 与 evaluate.py 一致：只扫营养域的分域索引。
        bm25_path=ROOT / "knowledge" / "index" / "bm25" / f"nutrition.{args.index_version}.json",
        index_manifest_path=ROOT / "knowledge" / "index" / "manifest.json",
        domain="nutrition",
        top_k=args.candidates,
        # 门槛设 0：让每个候选都带着分数返回，扫阈值时再按需裁剪。
        rerank_min_score=0.0,
        max_per_document=0,
        reranker=reranker,
        rerank_candidates=args.candidates,
        # 扫阈值要每题都真跑模型，不能命中上一次的分数。
        cache_enabled=False,
    )
    collected: dict[str, list[dict]] = {}
    rerank_ms: list[float] = []
    total_ms: list[float] = []
    degraded: dict = {}
    try:
        for question in load_questions():
            outcome = service.search(question["query"], top_k=args.candidates)
            degraded = outcome.degraded
            rerank_ms.append(outcome.timings.get("rerank_ms", 0.0))
            total_ms.append(outcome.timings.get("total_ms", 0.0))
            collected[question["id"]] = [
                {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "text": hit.text,
                    "rerank_score": hit.rerank_score,
                }
                for hit in outcome.hits
            ]
    finally:
        service.close()
    return collected, rerank_ms, total_ms, degraded


def sweep(questions: list[dict], collected: dict, args) -> int:
    print("| 阈值 | 文档命中 | 证据命中 | MRR | 可答召回 | 误答率 | 判定准确率 | off_topic 误答 |")
    print("|---|---|---|---|---|---|---|---|")
    best: tuple[float, dict] | None = None
    details: dict[float, list[dict]] = {}
    for threshold in THRESHOLDS:
        rows = []
        for question in questions:
            hits = [
                item for item in collected[question["id"]]
                if (item["rerank_score"] or 0.0) >= threshold
            ][: args.candidates]
            hits = _diversify_hits(hits, args.top_k, args.max_per_document)
            # build_row 期望 {"payload": {...}} 结构，这里按在线返回结构还原。
            rows.append(
                build_row(
                    question,
                    [
                        {
                            "chunk_id": hit["chunk_id"],
                            "score": hit["rerank_score"] or 0.0,
                            "payload": {
                                "document_id": hit["document_id"],
                                "text": hit["text"],
                            },
                        }
                        for hit in hits
                    ],
                    0.0,
                )
            )
        score = score_mode(f"rerank@{threshold}", rows)
        off_topic = score["refusal_breakdown"].get("off_topic") or {"accepted": 0, "total": 0}
        print(
            f"| {threshold:.2f} | {score['doc_hit_rate']:.2%} | {score['evidence_hit_rate']:.2%} | "
            f"{score['mrr']:.4f} | {score['answerable_recall']:.2%} | "
            f"{score['false_accept_rate']:.2%} | {score['decision_accuracy']:.2%} | "
            f"{off_topic['accepted']}/{off_topic['total']} |"
        )
        details[threshold] = rows
        # 选阈值：判定准确率优先，同分取更高阈值（更少误答）。
        if best is None or (score["decision_accuracy"], threshold) > (
            best[1]["decision_accuracy"],
            best[0],
        ):
            best = (threshold, score)

    if best and args.details:
        print(f"\n--- 阈值 {best[0]:.2f} 的失败明细 ---")
        for row in details[best[0]]:
            bad = (row["answerable"] and not row["evidence_hit"]) or (
                not row["answerable"] and row["accepted"]
            )
            if bad:
                print(
                    f"{row['id']} [{row['refusal_type'] or row['category']}] {row['query']} -> "
                    f"accepted={row['accepted']} top_docs={row['top_docs']}"
                )
    if best:
        print(f"\n建议阈值：{best[0]:.2f}（判定准确率 {best[1]['decision_accuracy']:.2%}）")
    return 0


def _diversify_hits(hits: list[dict], limit: int, max_per_document: int) -> list[dict]:
    """与在线逻辑同一套多样性规则，避免调参时按"不过滤"的高指标自欺。"""
    if max_per_document <= 0:
        return hits[:limit]
    picked: list[dict] = []
    overflow: list[dict] = []
    counts: dict[str, int] = {}
    for hit in hits:
        if len(picked) >= limit:
            break
        used = counts.get(hit["document_id"], 0)
        if used < max_per_document:
            picked.append(hit)
            counts[hit["document_id"]] = used + 1
        else:
            overflow.append(hit)
    for hit in overflow:
        if len(picked) >= limit:
            break
        picked.append(hit)
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="rerank 阈值扫描")
    parser.add_argument("--index-version", default="knowledge-v4")
    parser.add_argument("--collection", default="orange_diary_knowledge_v4")
    parser.add_argument("--candidates", type=int, default=20, help="送进 rerank 的候选数")
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--top-k", type=int, default=3, help="评估截断位置")
    parser.add_argument("--max-per-document", type=int, default=2)
    parser.add_argument(
        "--model-path", default=str(ROOT / "knowledge" / "models" / "bge-reranker-base")
    )
    parser.add_argument("--dump", default=str(ROOT / "knowledge" / "eval" / "rerank-scores.json"))
    parser.add_argument(
        "--from-dump",
        default=None,
        help="直接复用已落盘的分数重扫阈值（改阈值不必重跑模型）",
    )
    parser.add_argument("--details", action="store_true", help="打印最优阈值下的失败明细")
    args = parser.parse_args()

    questions = load_questions()
    for warning in validate_annotations(questions, load_all_chunks()):
        print(f"[标注自检] {warning}", file=sys.stderr)

    if args.from_dump:
        collected, _, _ = load_dump(Path(args.from_dump))
        print(f"\n复用 {args.from_dump}（{len(collected)} 题）\n")
        return sweep(questions, collected, args)

    collected, rerank_ms, total_ms, degraded = run_inference(args)
    print(
        f"\n候选 {args.candidates} / max_length {args.max_length} / {degraded}："
        f"rerank P50 {statistics.median(rerank_ms):.0f}ms，"
        f"端到端 P50 {statistics.median(total_ms):.0f}ms\n"
    )

    dump_path = Path(args.dump)
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": Path(args.model_path).name,
                "candidates": args.candidates,
                "max_length": args.max_length,
                "rerank_p50_ms": round(statistics.median(rerank_ms), 1),
                "questions": collected,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    code = sweep(questions, collected, args)
    print(f"分数明细已写入 {dump_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
