"""增量入库测试：内容未变要跳过，切块/索引策略变更必须触发重建。

这两条是相反方向的要求，必须一起测：
- 只按内容哈希判断"没变"会漏掉算法升级，导致新旧片段混在同一个集合；
- 只按版本判断又会每次全量重建，增量就没有意义。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.rag import pipeline as pipeline_module
from app.rag.pipeline import IngestPipeline


class FakeEmbedder:
    model = "fake-embed"

    def dimension(self) -> int:
        return 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 8 for _ in texts]


class FakeStore:
    def __init__(self) -> None:
        self.upserted: list[dict] = []

    def ensure_collection(self, name: str, dimension: int) -> None:
        pass

    def delete_document(self, name: str, document_id: str) -> None:
        pass

    def upsert(self, name: str, points: list[dict], batch_size: int = 64) -> int:
        self.upserted.extend(points)
        return len(points)

    def count(self, name: str) -> int:
        return len(self.upserted)


DOCUMENT = "# 测试文档\n\n## 钠\n\n成人每天食盐不超过5克。\n"


class IncrementalIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sources = root / "sources"
        self.sources.mkdir()
        (self.sources / "doc.md").write_text(DOCUMENT, encoding="utf-8")
        self.manifest = root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "version": "test-v1",
                    "documents": [
                        {"document_id": "doc", "title": "测试文档", "file": "doc.md", "version": "v1"}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.index_dir = root / "index"
        self.store = FakeStore()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, rebuild: bool = False):
        pipeline = IngestPipeline(
            sources_dir=self.sources,
            manifest_path=self.manifest,
            index_dir=self.index_dir,
            embedder=FakeEmbedder(),
            store=self.store,
            collection="test_collection",
            chunk_size=200,
            overlap=20,
        )
        return pipeline.run(index_version="test-v1", rebuild=rebuild)

    def test_second_run_skips_unchanged_document(self):
        first = self.build()
        self.assertEqual(1, first.documents_processed)
        second = self.build()
        self.assertEqual(1, second.documents_skipped)
        self.assertEqual(0, second.chunks_written)

    def test_index_text_version_change_triggers_rebuild(self):
        self.build()
        original = pipeline_module.INDEX_TEXT_VERSION
        try:
            pipeline_module.INDEX_TEXT_VERSION = original + "-next"
            second = self.build()
        finally:
            pipeline_module.INDEX_TEXT_VERSION = original
        self.assertEqual(1, second.documents_processed, "索引文本策略变化后应重建，而不是跳过")
        self.assertEqual(0, second.documents_skipped)

    def test_chunker_version_change_triggers_rebuild(self):
        self.build()
        original = pipeline_module.CHUNKER_VERSION
        try:
            pipeline_module.CHUNKER_VERSION = original + "-next"
            second = self.build()
        finally:
            pipeline_module.CHUNKER_VERSION = original
        self.assertEqual(1, second.documents_processed, "切块算法升级后应重建")

    def test_content_change_triggers_rebuild(self):
        self.build()
        (self.sources / "doc.md").write_text(DOCUMENT + "\n新增一段说明。\n", encoding="utf-8")
        second = self.build()
        self.assertEqual(1, second.documents_processed)


if __name__ == "__main__":
    unittest.main()
