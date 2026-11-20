"""0006：AI 会话记忆与问答追踪。

三张表都来自既有的 Java 脚本（`ai_memory_migration.sql` / `ai_trace_migration.sql`），
结构对齐、`IF NOT EXISTS` 保护存量库。这一步先只建表，`/api/ai/chat` 的编排与
`/api/admin/ai/*` 的查询都会落在这三张表上。
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `ai_conversation` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `user_id` bigint NOT NULL,
          `conversation_id` varchar(64) NOT NULL,
          `summary` text NULL,
          `summary_covered_sequence` int NOT NULL DEFAULT 0,
          `last_message_at` datetime NULL,
          `status` varchar(20) NOT NULL DEFAULT 'active',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_ai_conversation_user_conversation` (`user_id`, `conversation_id`),
          CONSTRAINT `fk_ai_conversation_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
            ON DELETE RESTRICT ON UPDATE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `ai_conversation_message` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `conversation_id` bigint NOT NULL,
          `message_sequence` int NOT NULL,
          `role` varchar(20) NOT NULL,
          `content` text NOT NULL,
          `token_count` int NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_ai_message_conversation_sequence` (`conversation_id`, `message_sequence`),
          KEY `idx_ai_message_conversation_sequence` (`conversation_id`, `message_sequence`),
          CONSTRAINT `fk_ai_message_conversation` FOREIGN KEY (`conversation_id`)
            REFERENCES `ai_conversation` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `ai_chat_trace` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `trace_id` varchar(64) NOT NULL,
          `user_id` bigint NULL,
          `conversation_id` varchar(64) NULL,
          `intent` varchar(64) NULL,
          `question_excerpt` varchar(64) NULL,
          `question_hash` varchar(64) NULL,
          `source_count` int NOT NULL DEFAULT 0,
          `retrieval_ms` int NULL,
          `rerank_state` varchar(64) NULL,
          `degraded` varchar(128) NULL,
          `total_ms` int NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          KEY `idx_ai_chat_trace_trace` (`trace_id`),
          KEY `idx_ai_chat_trace_created` (`created_at`),
          KEY `idx_ai_chat_trace_user` (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    # message 有外键指向 conversation，删表顺序要倒着来。
    for table in ("ai_chat_trace", "ai_conversation_message", "ai_conversation"):
        op.execute(f"DROP TABLE IF EXISTS `{table}`")
