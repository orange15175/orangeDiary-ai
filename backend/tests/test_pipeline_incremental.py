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
        self.deleted: list[str] = []

    def ensure_collection(self, name: str, dimension: int) -> None:
        pass

    def delete_document(self, name: str, document_id: str) -> None:
        self.deleted.append(document_id)

    def upsert(self, name: str, points: list[dict], batch_size: int = 64) -> int:
        self.upserted.extend(points)
        return len(points)

    def count(self, name: str) -> int:
        return len(self.upserted)


def load_status(index_dir: Path) -> dict:
    return json.loads((index_dir / "build_state.json").read_text(encoding="utf-8"))["last_run"]


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

    def test_document_removed_from_manifest_is_pruned(self):
        """源文档从清单里删掉后，必须在索引里同步消失，否则旧知识会一直被召回。"""
        self.build()
        self.store.deleted.clear()
        self.manifest.write_text(
            json.dumps({"version": "test-v2", "documents": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.build()
        self.assertEqual(1, result.documents_removed)
        self.assertIn("doc", self.store.deleted)
        self.assertFalse((self.index_dir / "chunks" / "doc.json").exists())

    def test_run_status_is_recorded(self):
        """失败与成功都要留下状态，排障时不必猜这次构建到底有没有跑完。"""
        result = self.build()
        self.assertEqual("succeeded", result.index_version and load_status(self.index_dir)["status"])

        # 制造失败：清单指向不存在的文件会让该文档解析失败，但不应中断整次运行；
        # 这里改用"源目录整体不可读"更彻底，直接删掉一个已登记文档目录下的文件。
        self.manifest.write_text(
            json.dumps({
                "version": "test-v3",
                "documents": [{"document_id": "missing", "title": "缺失", "file": "nope.md", "version": "v1"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.build()
        status = load_status(self.index_dir)
        self.assertEqual("succeeded", status["status"])
        self.assertTrue(status["errors"], "解析失败的文档必须出现在 errors 里")

    def test_content_change_triggers_rebuild(self):
        self.build()
        (self.sources / "doc.md").write_text(DOCUMENT + "\n新增一段说明。\n", encoding="utf-8")
        second = self.build()
        self.assertEqual(1, second.documents_processed)


if __name__ == "__main__":
    unittest.main()


NUTRITION_DOC = "# 膳食指南\n\n## 食盐\n\n成人每天食盐不超过 5 克。\n"
COOKING_DOC = "# 番茄炒蛋\n\n## 食材\n\n番茄 2 个、鸡蛋 3 个。\n\n## 做法\n\n先炒蛋再炒番茄。\n"


class DomainIndexTests(unittest.TestCase):
    """入库必须同时产出分域 BM25，否则域过滤只剩向量一半能用。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sources = root / "sources"
        (self.sources / "cooking").mkdir(parents=True)
        (self.sources / "diet.md").write_text(NUTRITION_DOC, encoding="utf-8")
        (self.sources / "cooking" / "recipe.md").write_text(COOKING_DOC, encoding="utf-8")
        self.manifest = root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "version": "test-v1",
                    "documents": [
                        {"document_id": "diet", "title": "膳食指南", "file": "diet.md",
                         "domain": "nutrition"},
                        {"document_id": "recipe-fanqie", "title": "番茄炒蛋",
                         "file": "cooking/recipe.md", "domain": "cooking",
                         "recipe_key": "fanqiechaodan", "chunk_strategy": "recipe"},
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

    def build(self):
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
        return pipeline.run(index_version="test-v1", rebuild=True)

    def test_per_domain_bm25_files_are_written(self):
        self.build()
        bm25_dir = self.index_dir / "bm25"
        self.assertTrue((bm25_dir / "test-v1.json").is_file(), "合并索引要保留给运维接口")
        self.assertTrue((bm25_dir / "nutrition.test-v1.json").is_file())
        self.assertTrue((bm25_dir / "cooking.test-v1.json").is_file())

    def test_payload_carries_knowledge_domain(self):
        self.build()
        by_domain = {}
        for point in self.store.upserted:
            by_domain.setdefault(point["payload"]["knowledge_domain"], []).append(point)
        self.assertEqual({"nutrition", "cooking"}, set(by_domain))
        cooking = by_domain["cooking"][0]["payload"]
        self.assertEqual("fanqiechaodan", cooking["recipe_key"])

    def test_manifest_records_domain_counts(self):
        self.build()
        manifest = json.loads((self.index_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["domains"]["nutrition"])
        self.assertEqual(1, manifest["domains"]["cooking"])

    def test_unknown_domain_is_rejected(self):
        self.manifest.write_text(
            json.dumps(
                {"version": "t", "documents": [
                    {"document_id": "x", "title": "x", "file": "diet.md", "domain": "cookng"}
                ]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        from app.rag.ingestion.manifest import ManifestError
        with self.assertRaises(ManifestError):
            self.build()

    def test_schema_version_change_triggers_rebuild(self):
        """新增 knowledge_domain 属于 payload 结构变更，旧点必须重建而不是跳过。"""
        self.build()
        original = pipeline_module.SCHEMA_VERSION
        try:
            pipeline_module.SCHEMA_VERSION = original + "-next"
            result = self.build_without_rebuild()
        finally:
            pipeline_module.SCHEMA_VERSION = original
        self.assertEqual(2, result.documents_processed)

    def build_without_rebuild(self):
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
        return pipeline.run(index_version="test-v1", rebuild=False)
