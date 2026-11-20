"""SQLAlchemy 声明基类。

统一 `__table_args__` 里显式写表名，不靠类名蛇形转换：存量表名有 `user`、`post`
这类与 SQL 关键字或框架约定冲突的写法，隐式规则一旦猜错就是线上写错表。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
