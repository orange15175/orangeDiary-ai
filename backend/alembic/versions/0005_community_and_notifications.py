"""0005：社区、站内消息、登录足迹、管理操作日志。

`post` / `post_comment` 的审核字段（moderation_reason / moderated_by / moderated_at）
在源脚本里是 ALTER 加的，这里直接写进建表语句——新建库一次到位，存量库因为
`IF NOT EXISTS` 不受影响，字段已存在也不会报错（表存在就整句跳过）。
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# `CREATE TABLE IF NOT EXISTS` 对已存在的表整句跳过，**不会补列**。
# 存量库里这三列通常已由 moderation_migration.sql 加过，但"跑过 post 没跑 moderation"
# 的库会缺；这里显式检查并补齐，避免 Python 侧写入 moderation_reason 时报 Unknown column。
_MODERATION_COLUMNS = {
    "post": [
        ("moderation_reason", "varchar(500) NULL"),
        ("moderated_by", "bigint NULL"),
        ("moderated_at", "datetime NULL"),
    ],
    "post_comment": [
        ("moderation_reason", "varchar(500) NULL"),
        ("moderated_by", "bigint NULL"),
        ("moderated_at", "datetime NULL"),
    ],
}


def _ensure_moderation_columns() -> None:
    bind = op.get_bind()
    for table, columns in _MODERATION_COLUMNS.items():
        for name, ddl in columns:
            exists = bind.exec_driver_sql(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (table, name),
            ).scalar()
            if not exists:
                bind.exec_driver_sql(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `user_daily_checkin` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `checkin_date` date NOT NULL COMMENT '签到日期',
          `checked_at` datetime NOT NULL COMMENT '首次签到时间',
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_user_daily_checkin_user_date` (`user_id`, `checkin_date`),
          CONSTRAINT `fk_user_daily_checkin_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户每日登录足迹'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `post` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `user_id` bigint NOT NULL,
          `post_type` varchar(30) NOT NULL,
          `content` varchar(2000) NOT NULL,
          `visibility` varchar(20) NOT NULL DEFAULT 'public',
          `status` varchar(20) NOT NULL DEFAULT 'published',
          `published_at` datetime NULL,
          `moderation_reason` varchar(500) NULL,
          `moderated_by` bigint NULL,
          `moderated_at` datetime NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          KEY `idx_post_feed` (`status`, `visibility`, `published_at`),
          KEY `idx_post_user` (`user_id`, `created_at`),
          CONSTRAINT `fk_post_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `post_image` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `post_id` bigint NOT NULL,
          `object_key` varchar(500) NOT NULL,
          `original_name` varchar(255) NULL,
          `content_type` varchar(100) NOT NULL,
          `sort_order` int NOT NULL DEFAULT 0,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          KEY `idx_post_image_post` (`post_id`),
          UNIQUE KEY `uk_post_image_object` (`object_key`),
          CONSTRAINT `fk_post_image_post_id` FOREIGN KEY (`post_id`) REFERENCES `post` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `post_like` (
          `post_id` bigint NOT NULL,
          `user_id` bigint NOT NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`post_id`, `user_id`),
          KEY `idx_post_like_user` (`user_id`, `created_at`),
          CONSTRAINT `fk_post_like_post_id` FOREIGN KEY (`post_id`) REFERENCES `post` (`id`) ON DELETE CASCADE,
          CONSTRAINT `fk_post_like_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `post_comment` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `post_id` bigint NOT NULL,
          `user_id` bigint NOT NULL,
          `content` varchar(500) NOT NULL,
          `status` varchar(20) NOT NULL DEFAULT 'active',
          `moderation_reason` varchar(500) NULL,
          `moderated_by` bigint NULL,
          `moderated_at` datetime NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          KEY `idx_post_comment_page` (`post_id`, `status`, `created_at`, `id`),
          KEY `idx_post_comment_user` (`user_id`, `created_at`),
          CONSTRAINT `fk_post_comment_post_id` FOREIGN KEY (`post_id`) REFERENCES `post` (`id`) ON DELETE CASCADE,
          CONSTRAINT `fk_post_comment_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `community_report` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `reporter_id` bigint NOT NULL,
          `target_type` varchar(20) NOT NULL,
          `target_id` bigint NOT NULL,
          `post_id` bigint NOT NULL,
          `target_author_id` bigint NOT NULL,
          `reason` varchar(40) NOT NULL,
          `details` varchar(500) NULL,
          `target_snapshot` text NOT NULL,
          `status` varchar(20) NOT NULL DEFAULT 'pending',
          `processed_by` bigint NULL,
          `processed_at` datetime NULL,
          `resolution` varchar(30) NULL,
          `resolution_reason` varchar(500) NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_reporter_target` (`reporter_id`, `target_type`, `target_id`),
          KEY `idx_report_queue` (`status`, `created_at`, `id`),
          KEY `idx_report_target` (`target_type`, `target_id`, `created_at`),
          KEY `idx_report_author` (`target_author_id`, `created_at`),
          CONSTRAINT `fk_report_reporter` FOREIGN KEY (`reporter_id`) REFERENCES `user` (`id`),
          CONSTRAINT `fk_report_post` FOREIGN KEY (`post_id`) REFERENCES `post` (`id`),
          CONSTRAINT `fk_report_author` FOREIGN KEY (`target_author_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `notification` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `recipient_id` bigint NOT NULL,
          `type` varchar(20) NOT NULL DEFAULT 'COMMENT',
          `actor_id` bigint NULL,
          `post_id` bigint NULL,
          `comment_id` bigint NULL,
          `title` varchar(100) NULL,
          `content` text NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `read_at` datetime NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_notification_comment` (`comment_id`),
          KEY `idx_notification_inbox` (`recipient_id`, `created_at`, `id`),
          KEY `idx_notification_type` (`recipient_id`, `type`, `created_at`, `id`),
          KEY `idx_notification_unread` (`recipient_id`, `read_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `admin_action_log` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `admin_id` bigint NOT NULL,
          `admin_name` varchar(100) NULL,
          `action_type` varchar(30) NOT NULL,
          `target_type` varchar(20) NOT NULL,
          `target_id` bigint NOT NULL,
          `post_id` bigint NOT NULL,
          `author_id` bigint NOT NULL,
          `reason` varchar(500) NOT NULL,
          `before_status` varchar(20) NOT NULL,
          `after_status` varchar(20) NOT NULL,
          `snapshot` text NOT NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          KEY `idx_log_time` (`created_at`, `id`),
          KEY `idx_log_admin` (`admin_id`, `created_at`),
          KEY `idx_log_target` (`target_id`, `created_at`),
          KEY `idx_log_action` (`action_type`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    _ensure_moderation_columns()


def downgrade() -> None:
    # 只删本次新建的表；community_report 有外键指向 post，删表顺序要倒着来。
    for table in (
        "admin_action_log",
        "notification",
        "community_report",
        "post_comment",
        "post_like",
        "post_image",
        "post",
        "user_daily_checkin",
    ):
        op.execute(f"DROP TABLE IF EXISTS `{table}`")
