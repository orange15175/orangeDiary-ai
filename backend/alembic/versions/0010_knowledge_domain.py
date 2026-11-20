"""0010：给 AI 来源快照与追踪表补 knowledge_domain。

索引里现在同时有营养文献（nutrition）与菜谱（cooking）两个域。回答引用了哪一份
资料、这条追踪走的是哪个域，事后必须能区分：

- 出现"做菜问题引了膳食指南"这类串域问题时，没有这个字段只能靠标题猜；
- 索引换版重建后，旧回答的来源快照仍要能说明它当时属于哪个域。

`CREATE TABLE IF NOT EXISTS` 对已存在的表整句跳过、不会补列，所以这里单独
检查 information_schema 后再 ADD COLUMN；重复执行是安全的。
"""
from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("ai_answer_source", "knowledge_domain"),
    ("ai_chat_trace", "knowledge_domain"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in _COLUMNS:
        exists = conn.exec_driver_sql(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
            """,
            (table, column),
        ).scalar()
        if exists:
            continue
        op.execute(
            f"ALTER TABLE `{table}` ADD COLUMN `{column}` varchar(32) NULL COMMENT '知识域：nutrition/cooking'"
        )


def downgrade() -> None:
    # 只回滚本 revision 新增的列；列里有数据时删除会丢历史，因此生产上建议前滚。
    conn = op.get_bind()
    for table, column in _COLUMNS:
        exists = conn.exec_driver_sql(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
            """,
            (table, column),
        ).scalar()
        if exists:
            op.execute(f"ALTER TABLE `{table}` DROP COLUMN `{column}`")
