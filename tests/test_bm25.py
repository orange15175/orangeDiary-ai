"""BM25 中文索引测试：分词保留单位、排序合理、持久化可往返。"""

import tempfile
import unittest
from pathlib import Path

from app.rag.retrieval.bm25 import BM25Index, tokenize

CHUNKS = [
    ("doc-000", "成人每天食盐摄入量不应超过 5 克，钠摄入不超过 2000 毫克。"),
    ("doc-001", "游离糖摄入应低于总能量的 10%，最好控制在 5% 以内。"),
    ("doc-002", "每周至少进行 150 分钟中等强度身体活动。"),
]


class TokenizeTests(unittest.TestCase):
    def test_numbers_and_units_are_kept(self):
        tokens = tokenize("每天食盐不超过 5 克，钠 2000 毫克")
        self.assertIn("5", tokens)
        self.assertIn("2000", tokens)
        # 单位统一成符号写法，避免"克"与文献里的"g"对不上。
        self.assertIn("g", tokens)
        self.assertIn("mg", tokens)

    def test_attached_unit_is_split(self):
        """文献写 300g，分词要同时给出整体、数字与单位。"""
        tokens = tokenize("谷类 200~300g")
        self.assertIn("300g", tokens)
        self.assertIn("300", tokens)
        self.assertIn("g", tokens)

    def test_quantity_words_match_symbols(self):
        """用户问"多少克"要能命中写"300g"的片段。"""
        query = set(tokenize("主食吃多少克"))
        document = set(tokenize("每日总摄入量 150~300g"))
        self.assertTrue(query & document, f"无共同 token: {query} vs {document}")

    def test_punctuation_is_removed(self):
        tokens = tokenize("食盐，摄入。")
        self.assertNotIn("，", tokens)
        self.assertNotIn("。", tokens)


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index()
        self.index.build(CHUNKS)

    def test_relevant_chunk_ranks_first(self):
        ranked = self.index.search("每天吃多少盐", top_k=3)
        self.assertTrue(ranked)
        self.assertEqual("doc-000", ranked[0][0])

    def test_sugar_query_hits_sugar_chunk(self):
        ranked = self.index.search("游离糖摄入上限", top_k=3)
        self.assertEqual("doc-001", ranked[0][0])

    def test_empty_query_returns_nothing(self):
        self.assertEqual([], self.index.search("", top_k=3))

    def test_unrelated_terms_score_zero(self):
        self.assertEqual([], self.index.search("宇宙飞船燃料", top_k=3))

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bm25.json"
            self.index.save(path)
            loaded = BM25Index.load(path)
        self.assertEqual(self.index.chunk_ids, loaded.chunk_ids)
        ranked = loaded.search("每天吃多少盐", top_k=1)
        self.assertEqual("doc-000", ranked[0][0])


if __name__ == "__main__":
    unittest.main()
