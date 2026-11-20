"""倒数排名融合（RRF）。

只依赖各通道的排名而不依赖分数尺度，因此向量余弦与 BM25 分可以直接融合，
不需要人为校准两套分数。默认 k=60 与常用实践一致。
"""

from __future__ import annotations

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = DEFAULT_RRF_K,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """把多个有序 ID 列表融合为一个有序结果。

    rankings 中每个元素是一个通道的 chunk_id 排名（越靠前越相关）。
    返回 [(chunk_id, rrf_score)]，按分数降序；同分时按首次出现的顺序稳定排序。
    """
    if k <= 0:
        raise ValueError("RRF 的 k 必须为正整数")
    if weights is not None and len(weights) != len(rankings):
        raise ValueError("weights 长度必须与 rankings 一致")

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for channel_index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else float(weights[channel_index])
        if weight <= 0:
            continue
        seen_in_channel: set[str] = set()
        for rank, chunk_id in enumerate(ranking, start=1):
            if not chunk_id or chunk_id in seen_in_channel:
                continue
            # 同一通道内重复出现只按首次排名计分，避免人为放大分数。
            seen_in_channel.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            if chunk_id not in first_seen:
                first_seen[chunk_id] = order
                order += 1

    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
