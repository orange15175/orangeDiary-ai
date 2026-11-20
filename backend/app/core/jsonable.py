"""把业务对象转成可写入 JSON 列的普通类型。

SQLAlchemy 的 JSON 列用标准 `json.dumps`，不认识 `Decimal` 与 `date`。
这里统一转换，避免每个服务各写一份：

- Decimal -> float（展示用；需要精确金额时在业务层用 Decimal 计算，落 JSON 只作快照）
- date/datetime -> ISO8601 字符串
- dict/list/tuple 递归
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return str(value)
