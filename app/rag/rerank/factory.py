"""按配置构建 reranker；未配置时返回 None，检索退回"RRF + 证据门槛"。

三种取值的语义：
- ``none``：不重排（默认）。degraded 标注 ``not_configured``，不假装排过序。
- ``local``：加载本地 cross-encoder 目录（``RERANK_MODEL_PATH``）。
- ``http``：调用外部 TEI 兼容服务（``RERANK_URL``）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.rag.rerank.base import RerankError, Reranker

log = logging.getLogger("orange-diary-ai.rag.rerank")

PROVIDERS = ("none", "local", "http")


def build_reranker(settings: Settings) -> Reranker | None:
    provider = (settings.rerank_provider or "none").strip().lower()
    if provider in {"", "none", "off"}:
        return None
    if provider == "local":
        from app.rag.rerank.local import LocalReranker

        return LocalReranker(
            model_path=Path(settings.rerank_model_path),
            max_length=settings.rerank_max_length,
            batch_size=settings.rerank_batch_size,
            threads=settings.rerank_threads,
        )
    if provider == "http":
        from app.rag.rerank.http import HttpReranker

        return HttpReranker(
            base_url=settings.rerank_url,
            model=settings.rerank_model_name,
            timeout_seconds=settings.rerank_timeout_seconds,
            token=settings.rerank_token,
        )
    raise RerankError(f"未知的 RERANK_PROVIDER={provider}，可选 {PROVIDERS}")
