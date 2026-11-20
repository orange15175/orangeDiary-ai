"""0007：回答来源快照与赞踩反馈。

两张表都对齐现有 Java 脚本的命名风格，并用 `IF NOT EXISTS` 保护存量库：

- `ai_answer_source`：一次回答引用的片段在此留存副本。只存 `source_count`
  会让索引更新后的旧回答无法解释"当初引用了什么"，所以这里存片段正文与标题。
- `ai_answer_feedback`：用户对某条回答的赞/踩。`(message_id, user_id)` 唯一，
  同一用户重复提交只更新，不堆历史行。
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `ai_answer_source` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `message_id` bigint NOT NULL,
          `trace_id` varchar(64) NOT NULL,
          `rank` int NOT NULL,
          `chunk_id` varchar(128) NULL,
          `document_id` varchar(128) NULL,
          `title` varchar(255) NULL,
          `heading_path` text NULL,
          `excerpt` text NULL,
          `source_url` varchar(512) NULL,
          `needs_review` tinyint(1) NOT NULL DEFAULT 0,
          `score` double NULL,
          `index_version` varchar(64) NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_ai_answer_source_message_rank` (`message_id`, `rank`),
          KEY `idx_ai_answer_source_trace` (`trace_id`),
          CONSTRAINT `fk_ai_answer_source_message` FOREIGN KEY (`message_id`)
            REFERENCES `ai_conversation_message` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `ai_answer_feedback` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `message_id` bigint NOT NULL,
          `user_id` bigint NOT NULL,
          `rating` varchar(8) NOT NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_ai_feedback_message_user` (`message_id`, `user_id`),
          KEY `idx_ai_feedback_user_rating` (`user_id`, `rating`),
          CONSTRAINT `fk_ai_feedback_message` FOREIGN KEY (`message_id`)
            REFERENCES `ai_conversation_message` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
          CONSTRAINT `fk_ai_feedback_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
            ON DELETE CASCADE ON UPDATE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    for table in ("ai_answer_feedback", "ai_answer_source"):
        op.execute(f"DROP TABLE IF EXISTS `{table}`")
