"""分页工具。

项目里有两种分页外形，来自两套 Java 实现，不能统一：

- **MyBatis-Plus `Page<T>`**：`{records,total,size,current,pages}`。社区、通知、管理端用它，
  前端 `table` 组件直接吃这套字段。
- **手写 VO**：`{items,total,page,pageSize,hasMore}`。食物、运动记录用它。

这里只提供 MP 形状与页码夹取，`items/…` 那套留在各自服务里——它们的
`hasMore` 判定公式不同（`page*size<total` vs `(page-1)*size+len<total`），
合并会悄悄改掉某一处的语义。
"""
from __future__ import annotations

from typing import Any

# 与 Java `safePage` / `safeSize` 一致：页码上限 1e6 防深翻页，页大小上限 20。
MAX_PAGE = 1_000_000


def safe_page(page: int) -> int:
    return max(1, min(MAX_PAGE, page))


def safe_size(size: int, maximum: int = 20) -> int:
    return max(1, min(maximum, size))


def mp_page(records: list[Any], total: int, current: int, limit: int) -> dict[str, Any]:
    return {
        "records": records,
        "total": total,
        "size": limit,
        "current": current,
        # MyBatis-Plus 的 pages 是向上取整；total=0 时为 0，不是 1。
        "pages": (total + limit - 1) // limit if limit else 0,
    }
