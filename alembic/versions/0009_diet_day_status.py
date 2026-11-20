"""0009：补齐 diet_day_status（当日记录完整度）。

设计 §13.2 列了这个表，0008 建表时漏了。复盘要求"营养日均值标明分母是
'已记录天'还是'完整记录天'，未记录不当 0"，没有这张表就只能猜用户那天是否记全，
所以单独补一个 revision，而不是改动已执行过的 0008。

`completeness` 取值 unknown / partial / complete，语义是**用户自述**，
不是系统推断；`confirmed_at` 为 None 表示用户还没表态。
"""
from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS `diet_day_status` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `status_date` date NOT NULL,
      `completeness` varchar(16) NOT NULL DEFAULT 'unknown',
      `note` varchar(255) NULL,
      `confirmed_at` datetime NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_diet_day_status_user_date` (`user_id`, `status_date`),
      CONSTRAINT `fk_diet_day_status_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def upgrade() -> None:
    for statement in _TABLES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `diet_day_status`")
