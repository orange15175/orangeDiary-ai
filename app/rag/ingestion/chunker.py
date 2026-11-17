"""结构化切块：按标题 / 段落 / 列表 / 表格边界切分。

目标是让每个片段自带标题路径，并且不把整张表或单位解释拆散。
"""

from __future__ import annotations

import re

from app.rag.schemas import Chunk, DocumentMeta, content_hash

DEFAULT_CHUNK_SIZE = 700
DEFAULT_OVERLAP = 100

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_INLINE_MARKS = re.compile(r"[*_`]+")
# 删除线只按成对语法处理：单个 ~ 是中文文献里的数值区间符号（200~300g），不能当标记删掉。
_STRIKE_RE = re.compile(r"~~(.+?)~~")


def clean_inline(text: str) -> str:
    """去掉行内 Markdown 标记，保留链接文字，便于向量化和展示。"""
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _INLINE_MARKS.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sections(markdown: str) -> list[tuple[tuple[str, ...], list[str]]]:
    """按标题把文档切成 (heading_path, [段落块])，表格作为整体保留。"""
    sections: list[tuple[tuple[str, ...], list[str]]] = []
    stack: list[tuple[int, str]] = []
    blocks: list[str] = []
    buffer: list[str] = []
    in_table = False

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer:
            text = clean_inline(" ".join(buffer))
            if text:
                blocks.append(text)
            buffer = []

    def flush_section() -> None:
        nonlocal blocks
        flush_buffer()
        if blocks:
            sections.append((tuple(title for _, title in stack), blocks))
        blocks = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        heading = _HEADING_RE.match(line)
        if heading and not in_table:
            flush_section()
            level = len(heading.group(1))
            title = clean_inline(heading.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue

        is_table_line = line.strip().startswith("|")
        if is_table_line:
            if not in_table:
                flush_buffer()
                in_table = True
            buffer.append(line.strip())
            continue
        if in_table:
            in_table = False
            text = "\n".join(buffer)
            buffer = []
            if text.strip():
                blocks.append(text)

        if not line.strip():
            flush_buffer()
            continue
        buffer.append(_LIST_RE.sub("", line.strip()) if _LIST_RE.match(line) else line.strip())

    flush_buffer()
    if in_table and buffer:
        blocks.append("\n".join(buffer))
    if blocks:
        sections.append((tuple(title for _, title in stack), blocks))
    return sections


_SENTENCE_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


def _split_long_block(block: str, chunk_size: int) -> list[str]:
    """超长段落按句子再切，避免单段落超过目标长度。"""
    if len(block) <= chunk_size:
        return [block]
    sentences = [item.strip() for item in _SENTENCE_RE.findall(block) if item.strip()]
    if not sentences:
        return [block[index : index + chunk_size] for index in range(0, len(block), chunk_size)]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        # 无标点的长串（参考文献、目录）按字符硬切，不能让单片段无限增长。
        if len(sentence) > chunk_size:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                sentence[index : index + chunk_size]
                for index in range(0, len(sentence), chunk_size)
            )
            continue
        if not current:
            current = sentence
        elif len(current) + len(sentence) <= chunk_size:
            current += sentence
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def _pack_blocks(blocks: list[str], chunk_size: int, overlap: int) -> list[str]:
    """把段落块打包成不超过 chunk_size 的片段，相邻片段保留少量重叠。"""
    chunks: list[str] = []
    current = ""
    for raw_block in blocks:
        for block in _split_long_block(raw_block, chunk_size):
            # 表格整块保留：宁可略超长度，也不拆散表头和单位说明。
            if block.startswith("|"):
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(block)
                continue
            if not current:
                current = block
                continue
            if len(current) + len(block) + 1 <= chunk_size:
                current = f"{current}\n{block}"
                continue
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n{block}" if tail and len(block) < chunk_size else block
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


MIN_CHUNK_SIZE = 60


def _merge_short_pieces(pieces: list[str], chunk_size: int) -> list[str]:
    """把过短的片段并入相邻片段，避免“对成人而言：”这类噪声单独成块。"""
    if not pieces:
        return pieces
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < MIN_CHUNK_SIZE and len(merged[-1]) + len(piece) + 1 <= int(chunk_size * 1.2):
            merged[-1] = f"{merged[-1]}\n{piece}"
        else:
            merged.append(piece)
    # 末尾仍过短时向前合并，保证每个片段都有足够上下文。
    if len(merged) > 1 and len(merged[-1]) < MIN_CHUNK_SIZE:
        tail = merged.pop()
        if len(merged[-1]) + len(tail) + 1 <= int(chunk_size * 1.2):
            merged[-1] = f"{merged[-1]}\n{tail}"
        else:
            merged.append(tail)
    return merged


def chunk_markdown(
    markdown: str,
    meta: DocumentMeta,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """生成带稳定 ID 与内容哈希的片段列表。"""
    chunks: list[Chunk] = []
    index = 0
    for heading_path, blocks in split_sections(markdown):
        pieces = _merge_short_pieces(_pack_blocks(blocks, chunk_size, overlap), chunk_size)
        for text in pieces:
            chunks.append(
                Chunk(
                    chunk_id=f"{meta.document_id}-{index:03d}",
                    document_id=meta.document_id,
                    version=meta.version,
                    chunk_index=index,
                    title=meta.title,
                    heading_path=heading_path or (meta.title,),
                    source_url=meta.source_url,
                    text=text,
                    content_hash=content_hash(text),
                )
            )
            index += 1
    return chunks
