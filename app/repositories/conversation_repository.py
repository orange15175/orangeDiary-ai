"""Persist one user-scoped AI conversation in the existing MySQL tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import md5
from uuid import UUID

import pymysql
from pymysql.cursors import DictCursor

from app.config import Settings


@dataclass(frozen=True)
class Conversation:
    id: int
    user_id: int
    summary: str
    summary_covered_sequence: int


@dataclass(frozen=True)
class ConversationMessage:
    sequence: int
    role: str
    content: str


class ConversationRepository:
    """Uses short-lived connections so each FastAPI worker remains independent."""

    # Match Java AiServiceImpl.defaultConversationId so old Java-path conversations remain readable.
    DEFAULT_CONVERSATION_ID_PREFIX = "orange-diary-ai-default:"

    def __init__(self, settings: Settings):
        """保存数据库连接配置，连接在每次仓储操作中短暂创建。"""
        self.settings = settings

    def get_or_create(self, user_id: int) -> Conversation:
        """读取或原子创建该用户的默认会话。"""
        conversation_id = self._default_conversation_id(user_id)
        with self._connection() as connection, connection.cursor() as cursor:
            # The unique key makes concurrent first requests resolve to the same conversation row.
            cursor.execute(
                """
                INSERT INTO ai_conversation (user_id, conversation_id, summary_covered_sequence, status)
                VALUES (%s, %s, 0, 'active')
                ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, conversation_id),
            )
            connection.commit()
            cursor.execute(
                """
                SELECT id, user_id, COALESCE(summary, '') AS summary,
                       COALESCE(summary_covered_sequence, 0) AS summary_covered_sequence
                FROM ai_conversation WHERE user_id = %s AND conversation_id = %s
                """,
                (user_id, conversation_id),
            )
            row = cursor.fetchone()
        return self._conversation_from_row(row)

    def get_by_id(self, conversation_id: int) -> Conversation | None:
        """按主键读取会话，用于摘要前重新确认覆盖位置。"""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, COALESCE(summary, '') AS summary,
                       COALESCE(summary_covered_sequence, 0) AS summary_covered_sequence
                FROM ai_conversation WHERE id = %s
                """,
                (conversation_id,),
            )
            row = cursor.fetchone()
        return self._conversation_from_row(row) if row else None

    def load_unsummarized_messages(self, conversation_id: int, covered_sequence: int) -> list[ConversationMessage]:
        """读取尚未被长期摘要覆盖的短期消息。"""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT message_sequence, role, content
                FROM ai_conversation_message
                WHERE conversation_id = %s AND message_sequence > %s
                ORDER BY message_sequence ASC
                """,
                (conversation_id, covered_sequence),
            )
            rows = cursor.fetchall()
        return [self._message_from_row(row) for row in rows]

    def get_max_message_sequence(self, conversation_id: int) -> int:
        """Read only the sequence boundary needed to validate summary coverage."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(message_sequence), 0) AS max_sequence "
                "FROM ai_conversation_message WHERE conversation_id = %s",
                (conversation_id,),
            )
            return int(cursor.fetchone()["max_sequence"] or 0)

    def repair_invalid_summary_coverage(
            self, conversation_id: int, expected_covered_sequence: int, max_sequence: int
    ) -> bool:
        """Reset only a provably impossible coverage marker without touching summary or messages."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_conversation
                SET summary_covered_sequence = 0
                WHERE id = %s
                  AND summary_covered_sequence = %s
                  AND summary_covered_sequence > %s
                """,
                (conversation_id, expected_covered_sequence, max_sequence),
            )
            repaired = cursor.rowcount == 1
            connection.commit()
            return repaired

    def append_turn(self, conversation_id: int, user_message: str, assistant_message: str) -> None:
        """以连续序号原子写入一组用户和助手消息。"""
        with self._connection() as connection, connection.cursor() as cursor:
            # Locking the parent row serializes sequence allocation for one conversation.
            cursor.execute("SELECT id FROM ai_conversation WHERE id = %s FOR UPDATE", (conversation_id,))
            cursor.execute(
                "SELECT COALESCE(MAX(message_sequence), 0) AS max_sequence "
                "FROM ai_conversation_message WHERE conversation_id = %s",
                (conversation_id,),
            )
            next_sequence = int(cursor.fetchone()["max_sequence"]) + 1
            cursor.executemany(
                """
                INSERT INTO ai_conversation_message (conversation_id, message_sequence, role, content)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (conversation_id, next_sequence, "user", user_message),
                    (conversation_id, next_sequence + 1, "assistant", assistant_message),
                ],
            )
            cursor.execute(
                "UPDATE ai_conversation SET last_message_at = %s WHERE id = %s",
                (datetime.now(), conversation_id),
            )
            connection.commit()

    def replace_summary(self, conversation_id: int, expected_covered_sequence: int, summary: str, new_covered_sequence: int) -> bool:
        """使用比较更新写入摘要，避免并发任务覆盖较新的摘要。"""
        with self._connection() as connection, connection.cursor() as cursor:
            # A compare-and-set prevents a slower duplicate summarization from moving coverage backward.
            cursor.execute(
                """
                UPDATE ai_conversation
                SET summary = %s, summary_covered_sequence = %s
                WHERE id = %s AND summary_covered_sequence = %s
                """,
                (summary, new_covered_sequence, conversation_id, expected_covered_sequence),
            )
            updated = cursor.rowcount == 1
            connection.commit()
            return updated

    def _connection(self):
        """创建一次 MySQL 连接；连接参数只来自运行时配置。"""
        if not self.settings.mysql_username:
            raise RuntimeError("未配置 MYSQL_USERNAME，无法保存 AI 聊天记忆")
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_username,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=5,
            read_timeout=8,
            write_timeout=8,
        )

    @staticmethod
    def _conversation_from_row(row: dict | None) -> Conversation:
        """把数据库行转换为领域会话对象。"""
        if not row:
            raise RuntimeError("无法创建 AI 会话")
        return Conversation(
            id=int(row["id"]), user_id=int(row["user_id"]), summary=row["summary"] or "",
            summary_covered_sequence=int(row["summary_covered_sequence"] or 0),
        )

    @staticmethod
    def _message_from_row(row: dict) -> ConversationMessage:
        """把数据库行转换为领域消息对象。"""
        return ConversationMessage(int(row["message_sequence"]), row["role"], row["content"])

    def _default_conversation_id(self, user_id: int) -> str:
        """为每个用户生成稳定且兼容 Java 历史记录的默认会话 ID。"""
        digest = md5(f"{self.DEFAULT_CONVERSATION_ID_PREFIX}{user_id}".encode("utf-8")).digest()
        return str(UUID(bytes=digest, version=3))
