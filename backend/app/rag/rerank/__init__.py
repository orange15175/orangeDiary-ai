"""Rerank 子系统：把 query 与候选片段做交互打分。

对外只暴露 `Reranker` 协议与 `build_reranker`；具体实现按依赖重量分文件，
这样不装 torch 的部署也能正常跑"无 rerank"模式。
"""

from app.rag.rerank.base import RerankError, RerankScores, Reranker, normalize_scores
from app.rag.rerank.factory import build_reranker

__all__ = [
    "RerankError",
    "RerankScores",
    "Reranker",
    "build_reranker",
    "normalize_scores",
]
