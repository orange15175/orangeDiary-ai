split=holdout，题量 32（可答 18 / 应拒答 14），bootstrap 2000 次，seed=20261118

| 模式 | 指标 | 点估计 | 95% CI |
|---|---|---|---|
| vector_only | doc_hit_rate | 0.944 | [0.833, 1.000] |
| vector_only | mrr | 0.824 | [0.667, 0.944] |
| vector_only | answerable_recall | 1.000 | [1.000, 1.000] |
| vector_only | false_accept_rate | 0.071 | [0.000, 0.214] |
| vector_only | decision_accuracy | 0.969 | [0.906, 1.000] |
| bm25_only | doc_hit_rate | 0.889 | [0.722, 1.000] |
| bm25_only | mrr | 0.722 | [0.556, 0.861] |
| bm25_only | answerable_recall | 1.000 | [1.000, 1.000] |
| bm25_only | false_accept_rate | 0.357 | [0.143, 0.643] |
| bm25_only | decision_accuracy | 0.844 | [0.719, 0.969] |
| hybrid_rrf | doc_hit_rate | 0.889 | [0.722, 1.000] |
| hybrid_rrf | mrr | 0.861 | [0.694, 1.000] |
| hybrid_rrf | answerable_recall | 1.000 | [1.000, 1.000] |
| hybrid_rrf | false_accept_rate | 0.071 | [0.000, 0.214] |
| hybrid_rrf | decision_accuracy | 0.969 | [0.906, 1.000] |
| hybrid_rerank | doc_hit_rate | 1.000 | [1.000, 1.000] |
| hybrid_rerank | mrr | 0.880 | [0.759, 0.972] |
| hybrid_rerank | answerable_recall | 1.000 | [1.000, 1.000] |
| hybrid_rerank | false_accept_rate | 0.000 | [0.000, 0.000] |
| hybrid_rerank | decision_accuracy | 1.000 | [1.000, 1.000] |
| hybrid_rerank_nogate | doc_hit_rate | 1.000 | [1.000, 1.000] |
| hybrid_rerank_nogate | mrr | 0.880 | [0.759, 0.972] |
| hybrid_rerank_nogate | answerable_recall | 1.000 | [1.000, 1.000] |
| hybrid_rerank_nogate | false_accept_rate | 1.000 | [1.000, 1.000] |
| hybrid_rerank_nogate | decision_accuracy | 0.562 | [0.406, 0.719] |

判读：区间重叠即差异不显著；负例只有 14 题，误答率的分辨率约 7 个百分点。
