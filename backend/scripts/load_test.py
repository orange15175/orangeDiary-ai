"""受控压测：对检索接口按并发档位打请求，输出 P50/P95/P99、吞吐与错误分布。

设计取舍：
1. 只打 `/internal/rag/v1/search`。端到端 `/api/ai/chat` 会真调生成模型（按量计费、
   有供应商限流），压测要的是本机检索链路的容量，不是供应商吞吐；
2. 冷 / 热分开测：冷缓存必须由**关掉缓存的实例**来跑（`RAG_CACHE_ENABLED=false`），
   而不是靠换查询词——换词会改变召回结果，测出来的不再是同一个负载；
3. 记录 `cache` 字段，命中缓存的请求耗时会拉低均值，必须能从数据里区分出来；
4. 客户端与被测服务同机运行，客户端自己也吃 CPU，因此结果是保守下限，报告里要写清楚。

用法：
    python scripts/load_test.py --concurrency 1 5 10 20 --requests 30 --label cold
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --label warm
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

QUERIES = [
    "减重期间主食应该吃多少克",
    "每天盐摄入上限是多少",
    "用代糖能帮助减肥吗",
    "每周至少运动多少分钟",
    "血糖高的人主食怎么搭配",
    "反式脂肪每天最多吃多少",
    "蔬菜水果每天吃多少",
    "肥胖的人蛋白质供能比多少",
]


def load_token() -> str:
    token = os.getenv("AI_SERVICE_TOKEN", "").strip()
    if token:
        return token
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("AI_SERVICE_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("缺少 AI_SERVICE_TOKEN，请在环境变量或 .env 中提供")


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def run_level(
    client: httpx.Client, base_url: str, token: str, concurrency: int, requests: int
) -> dict:
    """一个并发档位：固定总请求数，按并发数分批发完。"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    caches: dict[str, int] = {}
    started = time.perf_counter()

    def one(index: int) -> None:
        query = QUERIES[index % len(QUERIES)]
        begin = time.perf_counter()
        try:
            response = client.post(
                f"{base_url}/internal/rag/v1/search",
                headers=headers,
                json={"query": query, "top_k": 3},
            )
            elapsed = (time.perf_counter() - begin) * 1000
            key = str(response.status_code)
            statuses[key] = statuses.get(key, 0) + 1
            if response.status_code == 200:
                body = response.json()
                cache_state = body.get("cache") or "none"
                caches[cache_state] = caches.get(cache_state, 0) + 1
                latencies.append(elapsed)
            else:
                # 失败请求也计入延迟，否则 P95 会把超时那批悄悄排除掉。
                latencies.append(elapsed)
        except Exception as error:  # noqa: BLE001 - 压测要如实记录任何失败
            elapsed = (time.perf_counter() - begin) * 1000
            latencies.append(elapsed)
            key = type(error).__name__
            statuses[key] = statuses.get(key, 0) + 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(requests)))
    wall = time.perf_counter() - started

    return {
        "concurrency": concurrency,
        "requests": requests,
        "wall_seconds": round(wall, 2),
        "throughput_rps": round(requests / wall, 2) if wall > 0 else 0.0,
        "latency_p50_ms": round(percentile(latencies, 0.50), 1),
        "latency_p95_ms": round(percentile(latencies, 0.95), 1),
        "latency_p99_ms": round(percentile(latencies, 0.99), 1),
        "latency_max_ms": round(max(latencies), 1) if latencies else 0.0,
        "error_rate": round(
            1 - statuses.get("200", 0) / requests, 4
        ) if requests else 0.0,
        "statuses": statuses,
        "cache": caches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检索接口受控压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 5, 10, 20])
    parser.add_argument("--requests", type=int, default=30, help="每个并发档位的总请求数")
    parser.add_argument("--label", default="default", help="标记冷/热缓存，写入结果文件")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    token = load_token()
    results: list[dict] = []
    with httpx.Client(timeout=httpx.Timeout(args.timeout)) as client:
        # 先单请求探活：服务没起时不要浪费四轮超时。
        probe = client.post(
            f"{args.base_url}/internal/rag/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": QUERIES[0], "top_k": 3},
        )
        if probe.status_code != 200:
            print(f"探活失败 http={probe.status_code} {probe.text[:200]}", file=sys.stderr)
            return 1
        print(f"探活 OK，延迟约 {probe.elapsed.total_seconds() * 1000:.0f}ms，开始压测（{args.label}）\n")

        for concurrency in args.concurrency:
            level = run_level(client, args.base_url, token, concurrency, args.requests)
            results.append(level)
            print(
                f"并发 {concurrency:>2} | RPS {level['throughput_rps']:>5} | "
                f"P50 {level['latency_p50_ms']:>7}ms | P95 {level['latency_p95_ms']:>7}ms | "
                f"错误率 {level['error_rate']:.1%} | 状态 {level['statuses']}"
            )

    output = Path(args.output) if args.output else (
        ROOT / "knowledge" / "eval" / f"loadtest-{date.today().isoformat()}-{args.label}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "label": args.label,
                "base_url": args.base_url,
                "requests_per_level": args.requests,
                "levels": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {output}")

    print("\n| 并发 | RPS | P50 (ms) | P95 (ms) | P99 (ms) | Max (ms) | 错误率 | 缓存分布 |")
    print("|---|---|---|---|---|---|---|---|")
    for level in results:
        print(
            f"| {level['concurrency']} | {level['throughput_rps']} | {level['latency_p50_ms']} | "
            f"{level['latency_p95_ms']} | {level['latency_p99_ms']} | {level['latency_max_ms']} | "
            f"{level['error_rate']:.1%} | {level['cache']} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
