"""BM25 关键词索引（中文分词）。

单进程小知识库用持久化 JSON 索引即可；加载一次复用，不每请求重建。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import jieba

# 只去掉空白与常见标点，保留数字、单位、英文术语和中文词。
_NOISE_RE = re.compile(r"[\s\u3000，。、；：？！“”‘’（）()《》【】\[\]<>·—…,.;:?!\"'/\\|~`@#$%^&*+=_]+")

# 单位同义词归一：文献写"200~300g"，用户问"多少克"，不归一就对不上。
_UNIT_ALIASES = {
    "克": "g",
    "毫克": "mg",
    "千克": "kg",
    "公斤": "kg",
    "毫升": "ml",
    "升": "l",
    "千卡": "kcal",
    "大卡": "kcal",
}
# 数字紧跟单位的写法（300g、500ml），拆出数字与单位，让"克"这类量词也能命中。
_NUMBER_UNIT_RE = re.compile(r"^(\d+(?:\.\d+)?)(g|mg|kg|ml|l|kcal)$")


def _expand_token(token: str) -> list[str]:
    """把单位同义词与"数字+单位"展开成统一 token。"""
    if token in _UNIT_ALIASES:
        return [_UNIT_ALIASES[token]]
    matched = _NUMBER_UNIT_RE.match(token)
    if matched:
        number, unit = matched.groups()
        return [token, number, _UNIT_ALIASES.get(unit, unit)]
    return [token]


def tokenize(text: str) -> list[str]:
    """中文分词 + 英文小写归一化；数字与单位保留并做同义词归一。"""
    cleaned = _NOISE_RE.sub(" ", text.lower())
    tokens: list[str] = []
    for token in jieba.lcut(cleaned):
        token = token.strip()
        if not token or token.isspace():
            continue
        tokens.extend(_expand_token(token))
    return tokens


class BM25Index:
    """Okapi BM25；分数只用于关键词通道排序，不作为相关概率。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunk_ids: list[str] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_freq: Counter = Counter()
        self.doc_len: list[int] = []
        self.avgdl: float = 0.0

    def build(self, items: list[tuple[str, str]]) -> None:
        """items 为 (chunk_id, text)。"""
        self.chunk_ids = [chunk_id for chunk_id, _ in items]
        self.doc_tokens = [tokenize(text) for _, text in items]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.doc_freq = Counter()
        for tokens in self.doc_tokens:
            self.doc_freq.update(set(tokens))
        total = sum(self.doc_len)
        self.avgdl = (total / len(self.doc_len)) if self.doc_len else 0.0

    def _idf(self, term: str) -> float:
        n = len(self.doc_tokens)
        df = self.doc_freq.get(term, 0)
        if df <= 0 or n <= 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """返回 [(chunk_id, score)]，按分数降序。"""
        terms = tokenize(query)
        if not terms or not self.doc_tokens:
            return []
        scores: list[float] = [0.0] * len(self.doc_tokens)
        for term in set(terms):
            idf = self._idf(term)
            if idf <= 0:
                continue
            for index, tokens in enumerate(self.doc_tokens):
                tf = tokens.count(term)
                if not tf:
                    continue
                length = self.doc_len[index] or 1
                denominator = tf + self.k1 * (1 - self.b + self.b * length / (self.avgdl or 1))
                scores[index] += idf * tf * (self.k1 + 1) / denominator
        ranked = sorted(
            ((chunk_id, score) for chunk_id, score in zip(self.chunk_ids, scores) if score > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:top_k]

    def to_dict(self) -> dict:
        """只持久化分词结果，统计量在加载时重建，避免文件随统计项膨胀。"""
        return {
            "k1": self.k1,
            "b": self.b,
            "chunks": [{"chunk_id": chunk_id, "tokens": tokens}
                       for chunk_id, tokens in zip(self.chunk_ids, self.doc_tokens)],
        }

    @classmethod
    def from_dict(cls, body: dict) -> "BM25Index":
        index = cls(k1=float(body.get("k1", 1.5)), b=float(body.get("b", 0.75)))
        chunks = body.get("chunks") or []
        index.chunk_ids = [str(item.get("chunk_id")) for item in chunks]
        index.doc_tokens = [list(item.get("tokens") or []) for item in chunks]
        index.doc_len = [len(tokens) for tokens in index.doc_tokens]
        index.doc_freq = Counter()
        for tokens in index.doc_tokens:
            index.doc_freq.update(set(tokens))
        total = sum(index.doc_len)
        index.avgdl = (total / len(index.doc_len)) if index.doc_len else 0.0
        return index

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
