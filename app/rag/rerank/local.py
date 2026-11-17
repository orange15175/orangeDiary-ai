"""本地 cross-encoder rerank（transformers + torch，CPU 可用）。

模型目录必须事先落盘（本仓库脚本 `scripts/fetch_reranker.py` 负责下载），
运行时不联网：`local_files_only=True`，避免检索线程被下载阻塞。

选择 cross-encoder 而不是"再算一次向量余弦"的原因：它把 query 和段落拼成一对
送进同一个编码器，能在 query-passage 之间做交互（哪一段回答了这个问题），
而不是比较两个独立向量的整体相似度。这正是 q19 与代糖题需要的信号。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from app.rag.rerank.base import RerankError, RerankScores, normalize_scores

log = logging.getLogger("orange-diary-ai.rag.rerank")


class LocalReranker:
    """把 bge-reranker 这类单输出分类头当作相关度打分器。"""

    def __init__(
        self,
        model_path: str | Path,
        max_length: int = 512,
        batch_size: int = 8,
        threads: int | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_dir():
            raise RerankError(f"rerank 模型目录不存在: {path}")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:  # pragma: no cover - 依赖缺失时的明确提示
            raise RerankError(
                "未安装 rerank 依赖，请执行 pip install -r requirements-rerank.txt"
            ) from error

        self.name = "local"
        self.model = path.name
        self.max_length = max(32, int(max_length))
        self.batch_size = max(1, int(batch_size))
        self._torch = torch
        if threads:
            torch.set_num_threads(max(1, int(threads)))
        started = time.perf_counter()
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(path), local_files_only=True
            ).eval()
        except Exception as error:  # noqa: BLE001 - 模型损坏/格式不符统一转成 RerankError
            raise RerankError(f"rerank 模型加载失败: {error}") from error
        log.info(
            "rerank 模型加载完成：%s，耗时 %.0fms", self.model, (time.perf_counter() - started) * 1000
        )

    def score(self, query: str, documents: Sequence[str]) -> RerankScores:
        if not documents:
            return RerankScores(scores=(), provider=self.name, model=self.model, latency_ms=0.0)
        started = time.perf_counter()
        torch = self._torch
        values: list[float] = []
        try:
            with torch.inference_mode():
                for start in range(0, len(documents), self.batch_size):
                    batch = list(documents[start : start + self.batch_size])
                    features = self._tokenizer(
                        [query] * len(batch),
                        batch,
                        padding=True,
                        truncation="only_second",
                        max_length=self.max_length,
                        return_tensors="pt",
                    )
                    logits = self._model(**features).logits.view(-1)
                    values.extend(float(value) for value in logits.tolist())
        except Exception as error:  # noqa: BLE001
            raise RerankError(f"rerank 推理失败: {error}") from error
        return RerankScores(
            scores=normalize_scores(values, documents),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def close(self) -> None:
        # 释放模型权重，避免测试反复构建时内存堆积。
        self._model = None
        self._tokenizer = None
