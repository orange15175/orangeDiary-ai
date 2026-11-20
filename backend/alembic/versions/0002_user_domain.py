"""0002：用户域补齐 `avatar_upload`。

M1 只建了 user/user_profile/user_goal，但 `PUT /api/users/profile` 要校验头像
是否真的由本人上传过，没有这张表就只能跳过校验（等于允许任意 URL）。
存量库由 Java 迁移脚本建过同结构表，所以用 `IF NOT EXISTS`，upgrade 幂等。
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `avatar_upload` (
          `object_key` varchar(255) NOT NULL COMMENT '对象存储key',
          `user_id` bigint NOT NULL COMMENT '上传用户ID',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
          PRIMARY KEY (`object_key`),
          KEY `idx_avatar_cleanup` (`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='头像上传记录'
        """
    )


def downgrade() -> None:
    # 降级不删表：这张表在 M1 之前就存在于存量库，删掉会丢真实上传记录。
    pass
