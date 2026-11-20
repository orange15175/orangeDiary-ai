"""对评估结果做 bootstrap 置信区间，回答"18 道可答题上的 0.88 到底稳不稳"。

用法：
    python scripts/bootstrap_ci.py --input knowledge/eval/holdout_ablation.json --split holdout

为什么需要它：留出集只有 32 题（18 可答 / 14 应拒答），一道题就值 5.6 个百分点。
只报点估计会让"89% vs 83%"这种差距看起来像真的，其实完全落在抽样波动里。
这里对逐题结果重采样，给出 95% 置信区间——区间重叠就说明差异不显著。

只用标准库（random/statistics），不引入 numpy：脚本要能在任何环境跑起来。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 每个指标只在对应的题目子集上算：可答率不该被负例稀释，误答率也一样。
METRICS = {
    "doc_hit_rate": ("answerable", lambda rows: _mean(rows, lambda row: 1.0 if row["doc_hit"] else 0.0)),
    "mrr": ("answerable", lambda rows: _mean(
        rows, lambda row: 1.0 / row["doc_rank"] if row.get("doc_rank") else 0.0)),
    "answerable_recall": ("answerable", lambda rows: _mean(rows, lambda row: 1.0 if row["accepted"] else 0.0)),
    "false_accept_rate": ("unanswerable", lambda rows: _mean(rows, lambda row: 1.0 if row["accepted"] else 0.0)),
    "decision_accuracy": ("all", lambda rows: _mean(rows, lambda row: 1.0 if _correct(row) else 0.0)),
}


def _mean(rows: list[dict], value) -> float:
    return sum(value(row) for row in rows) / len(rows) if rows else 0.0


def _correct(row: dict) -> bool:
    # 可答题被接受、不可答题被拒绝，才算判断正确
    return bool(row["accepted"]) == bool(row["answerable"])


def _subset(rows: list[dict], kind: str) -> list[dict]:
    if kind == "answerable":
        return [row for row in rows if row["answerable"]]
    if kind == "unanswerable":
        return [row for row in rows if not row["answerable"]]
    return rows


def bootstrap(rows: list[dict], kind: str, metric, iterations: int, rng: random.Random) -> tuple[float, float, float]:
    sample = _subset(rows, kind)
    if not sample:
        return 0.0, 0.0, 0.0
    point = metric(sample)
    estimates = []
    for _ in range(iterations):
        resample = [sample[rng.randrange(len(sample))] for _ in range(len(sample))]
        estimates.append(metric(resample))
    estimates.sort()
    low = estimates[int(0.025 * len(estimates))]
    high = estimates[min(len(estimates) - 1, int(0.975 * len(estimates)))]
    return point, low, high


def main() -> int:
    parser = argparse.ArgumentParser(description="评估结果的 bootstrap 95% 置信区间")
    parser.add_argument("--input", default=str(ROOT / "knowledge" / "eval" / "holdout_ablation.json"))
    parser.add_argument("--split", default="holdout")
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261118, help="固定种子，保证结果可复现")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    details = payload.get("details", {}).get(args.split)
    if not details:
        print(f"结果文件里没有 split={args.split} 的逐题明细：{args.input}")
        return 1

    rng = random.Random(args.seed)
    first_mode = next(iter(details.values()))
    answerable = sum(1 for row in first_mode if row["answerable"])
    unanswerable = len(first_mode) - answerable
    print(f"split={args.split}，题量 {len(first_mode)}（可答 {answerable} / 应拒答 {unanswerable}），"
          f"bootstrap {args.iterations} 次，seed={args.seed}\n")
    print("| 模式 | 指标 | 点估计 | 95% CI |")
    print("|---|---|---|---|")

    ordered = ["vector_only", "bm25_only", "hybrid_rrf", "hybrid_rerank", "hybrid_rerank_nogate"]
    modes = [mode for mode in ordered if mode in details] + [m for m in details if m not in ordered]
    for mode in modes:
        rows = details[mode]
        for metric_name, (kind, metric) in METRICS.items():
            point, low, high = bootstrap(rows, kind, metric, args.iterations, rng)
            print(f"| {mode} | {metric_name} | {point:.3f} | [{low:.3f}, {high:.3f}] |")
    print("\n判读：区间重叠即差异不显著；负例只有 14 题，误答率的分辨率约 7 个百分点。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
