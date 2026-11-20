"""来源清单与构建状态。

manifest.json 记录人工审核过的文档元数据；build_state.json 记录上次构建的内容哈希，
用于“未变化文档跳过解析与 Embedding”的增量判断。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.rag.schemas import DOMAIN_NUTRITION, KNOWN_DOMAINS, DocumentMeta


class ManifestError(ValueError):
    """来源清单格式错误。"""


def load_manifest(path: Path) -> list[DocumentMeta]:
    """读取 manifest.json 并做最小校验。"""
    if not path.is_file():
        raise ManifestError(f"来源清单不存在: {path}")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"来源清单不是合法 JSON: {error}") from error
    documents = body.get("documents")
    if not isinstance(documents, list):
        raise ManifestError("来源清单缺少 documents 数组")
    result: list[DocumentMeta] = []
    seen: set[str] = set()
    for item in documents:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id") or "").strip()
        title = str(item.get("title") or "").strip()
        file_name = str(item.get("file") or "").strip()
        if not document_id or not title or not file_name:
            raise ManifestError(f"文档缺少 document_id/title/file: {item}")
        if document_id in seen:
            raise ManifestError(f"document_id 重复: {document_id}")
        seen.add(document_id)
        # 域名写错会同时从两个域的索引里消失（既不进 nutrition 也不进 cooking），
        # 检索时只表现为"没命中"，很难排查，所以这里直接拒绝。
        domain = str(item.get("domain") or DOMAIN_NUTRITION).strip()
        if domain not in KNOWN_DOMAINS:
            raise ManifestError(
                f"未知 knowledge_domain: {domain}（允许：{', '.join(KNOWN_DOMAINS)}）"
            )
        strategy = str(item.get("chunk_strategy") or "heading").strip()
        if strategy not in {"heading", "recipe"}:
            raise ManifestError(f"未知 chunk_strategy: {strategy}（允许：heading, recipe）")
        result.append(
            DocumentMeta(
                document_id=document_id,
                title=title,
                file=file_name,
                publisher=str(item.get("publisher") or ""),
                source_url=str(item.get("source_url") or ""),
                published_at=str(item.get("published_at") or ""),
                scope=str(item.get("scope") or ""),
                version=str(item.get("version") or "v1"),
                needs_review=bool(item.get("needs_review", False)),
                domain=domain,
                recipe_key=str(item.get("recipe_key") or "").strip(),
                chunk_strategy=strategy,
                license=str(item.get("license") or "").strip(),
            )
        )
    return result


def load_build_state(path: Path) -> dict:
    """读取上次构建状态；文件不存在时返回空状态。"""
    if not path.is_file():
        return {"documents": {}}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"documents": {}}
    if not isinstance(body, dict) or not isinstance(body.get("documents"), dict):
        return {"documents": {}}
    return body


def save_build_state(path: Path, state: dict) -> None:
    """原子写入构建状态，避免中断留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
