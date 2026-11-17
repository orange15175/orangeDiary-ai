"""RRF 融合的单元测试。"""

from __future__ import annotations

import unittest

from app.rag.retrieval.fusion import reciprocal_rank_fusion


class ReciprocalRankFusionTest(unittest.TestCase):
    def test_single_channel_keeps_order(self):
        fused = reciprocal_rank_fusion([["a", "b", "c"]])
        self.assertEqual([chunk_id for chunk_id, _ in fused], ["a", "b", "c"])

    def test_two_channel_agreement_ranks_first(self):
        # a 在两个通道都靠前，应排在只有单通道支持的 b 之前。
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c"]])
        self.assertEqual([chunk_id for chunk_id, _ in fused], ["a", "c", "b"])

    def test_hit_in_both_channels_beats_single_top_hit(self):
        # 双通道次优（1/62 + 1/61）应高于单通道最优（1/61）。
        fused = reciprocal_rank_fusion([["x", "shared"], ["y", "shared"]])
        self.assertEqual(fused[0][0], "shared")

    def test_weight_zero_ignores_channel(self):
        fused = reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0, 0.0])
        self.assertEqual([chunk_id for chunk_id, _ in fused], ["a"])

    def test_duplicate_ids_in_one_channel_counted_once(self):
        fused = reciprocal_rank_fusion([["a", "a", "b"]])
        scores = dict(fused)
        # 重复出现的 a 只按首次出现的排名计分。
        self.assertAlmostEqual(scores["a"], 1 / 61)
        self.assertAlmostEqual(scores["b"], 1 / 63)

    def test_empty_input_returns_empty(self):
        self.assertEqual(reciprocal_rank_fusion([]), [])
        self.assertEqual(reciprocal_rank_fusion([[], []]), [])

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([["a"]], k=0)
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])

    def test_blank_ids_are_ignored(self):
        fused = reciprocal_rank_fusion([["", "a"]])
        self.assertEqual([chunk_id for chunk_id, _ in fused], ["a"])


if __name__ == "__main__":
    unittest.main()
