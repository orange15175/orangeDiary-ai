"""知识文档加载：读取 Markdown / TXT，剥离前置元数据。"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件上限，防止异常大文件拖垮构建


class LoaderError(ValueError):
    """文档无法解析时抛出，调用方必须报告而不是静默入库空文档。"""


def read_document(path: Path) -> str:
    """读取文本并做基本校验；空文件直接报错。"""
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise LoaderError(f"不支持的文件类型: {path.suffix}")
    if not path.is_file():
        raise LoaderError(f"文件不存在: {path}")
    size = path.stat().st_size
    if size == 0:
        raise LoaderError(f"空文件: {path.name}")
    if size > MAX_FILE_BYTES:
        raise LoaderError(f"文件过大({size} bytes): {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise LoaderError(f"编码不是 UTF-8: {path.name}") from error
    if not text.strip():
        raise LoaderError(f"内容为空: {path.name}")
    return text


def strip_front_matter(text: str) -> str:
    """去掉 `--- ... ---` 前置块；元数据以 manifest.json 为准。"""
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1:]).lstrip("\n")
    return text
