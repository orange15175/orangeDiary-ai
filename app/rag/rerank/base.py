"""Rerank 抽象：把"召回候选"变成"按 query 与片段的交互相关度排序"。

为什么单独一层：RRF 只看排名、向量只看余弦、BM25 只看词面，三者都无法判断
"这段话是否真的回答了这个问题"。M4 实测里两个已知缺陷都卡在这一步：
- q19「减重主食吃多少克」命中了文档但排不进 Top-3（词面叫法不同）；
- 「用代糖能减肥吗」向量 0.598 / BM25 6.7，与负例分数区间重叠被误拒。

接口约定：
- `score()` 返回与入参等长的分数序列，只保证**同一批内可比较**（0~1 概率或 logit 皆可，
  但同一 provider 必须稳定）；
- 任何失败抛 `RerankError`，由调用方降级，不允许返回 0 分装作"无关"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class RerankError(RuntimeError):
    """rerank 不可用或调用失败；调用方必须降级并标注 degraded。"""


@dataclass(frozen=True)
class RerankScores:
    """一批候选的相关度分数。"""

    scores: tuple[float, ...]
    provider: str
    model: str
    latency_ms: float

    def top_index(self) -> int:
        return max(range(len(self.scores)), key=lambda index: self.scores[index]) if self.scores else -1


class Reranker(Protocol):
    """所有 rerank 实现的最小契约。"""

    name: str
    model: str

    def score(self, query: str, documents: Sequence[str]) -> RerankScores: ...

    def close(self) -> None: ...


def normalize_scores(raw: Sequence[float], documents: Sequence[str]) -> tuple[float, ...]:
    """把任意实数分数压到 0~1，便于跨 provider 比较与设阈值。

    已经是 0~1 的（sigmoid 后的概率）原样返回；出现负数或 >1 的按 logistic 压一次。
    这样即使换用输出 logit 的模型，阈值语义仍然是"相关概率"。
    """
    if len(raw) != len(documents):
        raise RerankError(f"rerank 返回分数数量不符：期望 {len(documents)}，实际 {len(raw)}")
    values = [float(value) for value in raw]
    if all(0.0 <= value <= 1.0 for value in values):
        return tuple(values)
    return tuple(1.0 / (1.0 + pow(2.718281828459045, -value)) for value in values)
