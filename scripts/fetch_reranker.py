"""下载本地 rerank 模型（默认走 ModelScope，HuggingFace 在部分网络下不可达）。

模型体积约 1.1GB，不放进 git（.gitignore 已排除 knowledge/models/）。
脚本按文件逐个下载并校验大小，中断后重跑可续。

运行：
    python scripts/fetch_reranker.py                     # BAAI/bge-reranker-base -> knowledge/models/
    python scripts/fetch_reranker.py --model BAAI/bge-reranker-v2-m3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "BAAI/bge-reranker-base"
FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "model.safetensors",
]


def download(model: str, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    base = f"https://www.modelscope.cn/api/v1/models/{model}/repo"
    with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client:
        for name in FILES:
            path = target / name
            if path.is_file() and path.stat().st_size > 0:
                print(f"跳过已存在 {name}（{path.stat().st_size / 1e6:.1f}MB）")
                continue
            print(f"下载 {name} ...", flush=True)
            with client.stream("GET", base, params={"Revision": "master", "FilePath": name}) as response:
                response.raise_for_status()
                temp = path.with_suffix(path.suffix + ".part")
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes(1 << 20):
                        handle.write(chunk)
                temp.replace(path)
            print(f"  完成 {name}（{path.stat().st_size / 1e6:.1f}MB）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 rerank 模型")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--target", default=None, help="目标目录，默认 knowledge/models/<模型名>")
    args = parser.parse_args()
    target = Path(args.target) if args.target else (
        ROOT / "knowledge" / "models" / args.model.split("/")[-1]
    )
    try:
        return download(args.model, target)
    except httpx.HTTPError as error:
        print(f"下载失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
