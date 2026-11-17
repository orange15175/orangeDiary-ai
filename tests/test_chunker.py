"""切块回归测试：标题路径、表格完整性、片段长度与稳定 ID。"""

import unittest

from app.rag.ingestion.chunker import chunk_markdown, split_sections
from app.rag.schemas import DocumentMeta


def meta() -> DocumentMeta:
    return DocumentMeta(
        document_id="who-diet",
        title="健康饮食",
        file="who.md",
        source_url="https://www.who.int/",
    )


MARKDOWN = """# 健康饮食

概述段落，介绍健康饮食的基本原则。

## 控制钠的摄入

成人每天钠摄入不应超过 2000 毫克。

| 人群 | 建议 |
| --- | --- |
| 成人 | 少于 5 克盐 |

## 减少游离糖

游离糖应低于总能量的 10%。
"""


class ChunkerTests(unittest.TestCase):
    def test_heading_path_is_preserved(self):
        sections = split_sections(MARKDOWN)
        paths = [path for path, _ in sections]
        self.assertIn(("健康饮食", "控制钠的摄入"), paths)
        self.assertIn(("健康饮食", "减少游离糖"), paths)

    def test_table_stays_in_one_chunk(self):
        chunks = chunk_markdown(MARKDOWN, meta(), chunk_size=40, overlap=5)
        table_chunks = [chunk for chunk in chunks if "| 人群 |" in chunk.text]
        self.assertEqual(1, len(table_chunks))
        self.assertIn("| 成人 |", table_chunks[0].text)

    def test_table_heading_path_matches_section(self):
        chunks = chunk_markdown(MARKDOWN, meta())
        table_chunk = next(chunk for chunk in chunks if "| 人群 |" in chunk.text)
        self.assertEqual(("健康饮食", "控制钠的摄入"), table_chunk.heading_path)

    def test_chunk_ids_are_stable_and_sequential(self):
        first = chunk_markdown(MARKDOWN, meta())
        second = chunk_markdown(MARKDOWN, meta())
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in second])
        self.assertEqual(["who-diet-000", "who-diet-001"], [c.chunk_id for c in first][:2])

    def test_long_section_is_split_with_overlap(self):
        paragraph = "钠摄入过多会升高血压。" * 40
        markdown = f"# 标题\n\n## 章节\n\n{paragraph}\n"
        chunks = chunk_markdown(markdown, meta(), chunk_size=120, overlap=20)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 200)

    def test_inline_markdown_is_cleaned(self):
        markdown = "# 标题\n\n## 章节\n\n**加粗**与[链接](https://example.com)保留文字。\n"
        chunks = chunk_markdown(markdown, meta())
        self.assertNotIn("**", chunks[0].text)
        self.assertNotIn("https://example.com", chunks[0].text)
        self.assertIn("链接", chunks[0].text)

    def test_numeric_range_keeps_tilde(self):
        """中文文献用 ~ 表示数值区间，不能被当成删除线标记删掉。"""
        markdown = "# 标题\n\n## 章节\n\n建议每天摄入谷类200~300g，烹调油不超过25~30g。\n"
        chunks = chunk_markdown(markdown, meta())
        self.assertIn("200~300g", chunks[0].text)
        self.assertIn("25~30g", chunks[0].text)

    def test_paired_strikethrough_is_removed(self):
        markdown = "# 标题\n\n## 章节\n\n~~已废止的旧建议~~应替换为新版。\n"
        chunks = chunk_markdown(markdown, meta())
        self.assertNotIn("~~", chunks[0].text)
        self.assertIn("已废止的旧建议", chunks[0].text)

    def test_empty_markdown_yields_no_chunks(self):
        self.assertEqual([], chunk_markdown("", meta()))


if __name__ == "__main__":
    unittest.main()
