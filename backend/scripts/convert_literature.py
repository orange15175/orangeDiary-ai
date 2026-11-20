"""把 docx / pdf 文献转换为 Markdown，保留标题层级与表格。

转换是一次性的受控操作；结果落入 knowledge/sources/，由人工复核后再入库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

# Word 样式名到 Markdown 标题级别的映射；中文样式名一并兼容。
_HEADING_STYLES = {
    "Title": 1,
    "Subtitle": 2,
    "Heading 1": 1, "Heading 2": 2, "Heading 3": 3, "Heading 4": 4,
    "标题 1": 1, "标题 2": 2, "标题 3": 3, "标题 4": 4,
    "标题": 1,
}
_LIST_STYLES = {"List Bullet", "List Number", "List Paragraph", "列表段落", "项目符号"}


def iter_blocks(document: Document):
    """按文档顺序遍历段落与表格，避免表格位置错乱。"""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def clean_cell(text: str) -> str:
    """表格单元格内不能有换行与竖线。"""
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def table_to_markdown(table: Table) -> str:
    """把 Word 表格转为 Markdown 表格；第一行作为表头。"""
    rows = []
    for row in table.rows:
        cells = [clean_cell(cell.text) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(normalized[0]) + " |"
    divider = "| " + " | ".join(["---"] * width) + " |"
    body = ["| " + " | ".join(row) + " |" for row in normalized[1:]]
    return "\n".join([header, divider, *body])


def _looks_like_heading(paragraph: Paragraph) -> bool:
    """原文常用“加粗短段落”代替标题样式，按此特征还原二级标题。"""
    text = paragraph.text.strip()
    if not text or len(text) > 30:
        return False
    if text[-1] in "。，、；：！？,.;:!?":
        return False
    # 加粗的列表项和引导句不是标题：带分号的碎片、以冒号结尾的引导语都排除。
    if "；" in text or ";" in text:
        return False
    runs = [run for run in paragraph.runs if run.text.strip()]
    if not runs:
        return False
    return all(run.bold for run in runs)


def docx_to_markdown(path: Path) -> str:
    document = Document(str(path))
    # 文档标题取自文件名，正文只负责章节层级。
    lines: list[str] = [f"# {path.stem}", ""]
    for block in iter_blocks(document):
        if isinstance(block, Table):
            markdown_table = table_to_markdown(block)
            if markdown_table:
                lines.extend(["", markdown_table, ""])
            continue
        text = block.text.strip()
        if not text:
            continue
        if text == path.stem:
            continue
        style = (block.style.name or "").strip() if block.style is not None else ""
        if style in _HEADING_STYLES:
            level = _HEADING_STYLES[style]
            lines.extend(["", f"{'#' * level} {text}", ""])
        elif style in _LIST_STYLES:
            lines.append(f"- {text}")
        elif _looks_like_heading(block):
            lines.extend(["", f"## {text}", ""])
        else:
            lines.extend([text, ""])
    return "\n".join(lines).strip() + "\n"


def pdf_to_markdown(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = [f"# {path.stem}", ""]
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(f"## 第 {index} 页\n\n{text}")
    return "\n\n".join(parts).strip() + "\n"


def convert(source_dir: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"converted": [], "failed": []}
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".docx":
                markdown = docx_to_markdown(path)
            elif suffix == ".pdf":
                markdown = pdf_to_markdown(path)
            else:
                continue
            if len(markdown.strip()) < 200:
                raise ValueError(f"解析结果过短({len(markdown)} 字符)，可能是扫描件或提取失败")
            target = output_dir / f"{path.stem}.md"
            target.write_text(markdown, encoding="utf-8")
            report["converted"].append({"source": path.name, "output": target.name, "chars": len(markdown)})
        except Exception as error:  # noqa: BLE001 单个文件失败不能中断整批转换
            report["failed"].append({"source": path.name, "error": str(error)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="docx/pdf 转 Markdown")
    parser.add_argument("--source", required=True, help="原始文献目录")
    parser.add_argument("--output", required=True, help="Markdown 输出目录")
    args = parser.parse_args()
    report = convert(Path(args.source), Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
