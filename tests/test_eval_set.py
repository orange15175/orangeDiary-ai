"""评估集自检测试：防止题目与索引漂移后仍被当成"通过"。"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "knowledge" / "eval" / "questions.json"
MANIFEST = ROOT / "knowledge" / "manifest.json"
CHUNKS_DIR = ROOT / "knowledge" / "index" / "chunks"


class EvalSetTests(unittest.TestCase):
    def setUp(self):
        self.questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
        self.doc_ids = {
            item["document_id"]
            for item in json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
        }

    def test_question_ids_are_unique(self):
        ids = [item["id"] for item in self.questions]
        self.assertEqual(len(ids), len(set(ids)))

    def test_has_answerable_and_refusal_questions(self):
        answerable = [item for item in self.questions if item["answerable"]]
        refusal = [item for item in self.questions if not item["answerable"]]
        self.assertGreaterEqual(len(answerable), 20)
        self.assertGreaterEqual(len(refusal), 3)

    def test_answerable_questions_point_to_known_documents(self):
        for item in self.questions:
            if not item["answerable"]:
                continue
            self.assertTrue(item["expected_doc_ids"], f"{item['id']} 缺少期望文档")
            unknown = set(item["expected_doc_ids"]) - self.doc_ids
            self.assertFalse(unknown, f"{item['id']} 引用了不存在的文档: {unknown}")
            self.assertTrue(item["evidence_keywords"], f"{item['id']} 缺少证据词")

    def test_refusal_questions_have_no_expectation(self):
        for item in self.questions:
            if item["answerable"]:
                continue
            self.assertEqual([], item["expected_doc_ids"])
            self.assertEqual([], item["evidence_keywords"])
            self.assertIn(
                item.get("refusal_type"),
                {"off_topic", "professional_advice"},
                f"{item['id']} 缺少合法的 refusal_type",
            )

    def test_evidence_keywords_exist_in_built_index(self):
        """索引存在时才校验（索引产物不入库），避免新克隆环境误报。"""
        if not CHUNKS_DIR.is_dir():
            self.skipTest("索引未构建，跳过证据词校验")
        texts: dict[str, list[str]] = {}
        for path in CHUNKS_DIR.glob("*.json"):
            body = json.loads(path.read_text(encoding="utf-8"))
            texts.setdefault(body["document_id"], []).extend(c["text"] for c in body["chunks"])
        for item in self.questions:
            if not item["answerable"]:
                continue
            corpus = " ".join(
                text
                for doc_id in item["expected_doc_ids"]
                for text in texts.get(doc_id, [])
            )
            found = any(keyword in corpus for keyword in item["evidence_keywords"])
            self.assertTrue(found, f"{item['id']} 的证据词在期望文档中均未出现")


if __name__ == "__main__":
    unittest.main()
