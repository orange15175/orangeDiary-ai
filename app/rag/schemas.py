"""RAG 基础数据结构。

文档、切块与索引元数据集中在这里，避免各处散落字符串键。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# 切块算法或参数变化时必须改版本号，旧索引才能被识别为不兼容。
CHUNKER_VERSION = "md-heading-v2"
# 索引文本的拼装方式（是否并入章节标题）变化时改这里，触发全量重建。
INDEX_TEXT_VERSION = "heading-context-v1"
SCHEMA_VERSION = "1"


def content_hash(text: str) -> str:
    """文本内容哈希，用于判断文档/片段是否变化。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DocumentMeta:
    """来源清单中的一条文档元数据。"""

    document_id: str
    title: str
    file: str
    publisher: str = ""
    source_url: str = ""
    published_at: str = ""
    scope: str = ""
    version: str = "v1"
    needs_review: bool = False


@dataclass(frozen=True)
class Chunk:
    """一个可入库的片段，附带可追溯的元数据。"""

    chunk_id: str
    document_id: str
    version: str
    chunk_index: int
    title: str
    heading_path: tuple[str, ...]
    source_url: str
    text: str
    content_hash: str

    @property
    def heading_text(self) -> str:
        """把标题路径拼成可读字符串，用于展示与日志。"""
        return " > ".join(self.heading_path)

    @property
    def index_text(self) -> str:
        """送入 Embedding 与 BM25 的文本。

        章节标题携带主题信息（例如「谷薯类的选择」），只索引正文会让
        "主食吃多少克"这类按小节提问的问题落到相邻段落上。
        """
        if not self.heading_path:
            return self.text
        return f"{self.heading_text}\n{self.text}"

    def payload(self) -> dict:
        """Qdrant payload；字段与检索接口返回结构保持一致。"""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "version": self.version,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "heading_path": list(self.heading_path),
            "source_url": self.source_url,
            "text": self.text,
            "content_hash": self.content_hash,
            "chunker_version": CHUNKER_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
