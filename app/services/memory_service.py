"""Build bounded chat memory and periodically compact old conversation turns."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from app.clients.deepseek_client import DeepSeekClient
from app.repositories.conversation_repository import Conversation, ConversationMessage, ConversationRepository

logger = logging.getLogger("orange-diary-ai")


@dataclass(frozen=True)
class MemoryContext:
    conversation: Conversation
    messages: tuple[ConversationMessage, ...]


class MemoryService:
    SUMMARY_TRIGGER_MESSAGE_COUNT = 12
    SUMMARY_BATCH_SIZE = 6
    MAX_SUMMARY_LENGTH = 800

    def __init__(self, repository: ConversationRepository, deepseek: DeepSeekClient):
        self.repository = repository
        self.deepseek = deepseek

    async def load_context(self, user_id: int) -> MemoryContext:
        conversation = await asyncio.to_thread(self.repository.get_or_create, user_id)
        conversation, messages = await self._load_validated_messages(conversation)
        return MemoryContext(conversation, tuple(messages))

    async def save_completed_turn(self, context: MemoryContext, user_message: str, assistant_message: str) -> None:
        await asyncio.to_thread(self.repository.append_turn, context.conversation.id, user_message, assistant_message)
        await self._summarize_if_needed(context.conversation.id)

    async def _summarize_if_needed(self, conversation_id: int) -> None:
        # Reload under the latest coverage marker because another request may have just summarized first.
        conversation = await asyncio.to_thread(self._find_conversation, conversation_id)
        if conversation is None:
            return
        conversation, messages = await self._load_validated_messages(conversation)
        if len(messages) < self.SUMMARY_TRIGGER_MESSAGE_COUNT:
            return
        batch = messages[:self.SUMMARY_BATCH_SIZE]
        try:
            result = await self.deepseek.chat([
                {"role": "system", "content": self._summary_instruction()},
                {"role": "user", "content": self._summary_input(conversation.summary, batch)},
            ])
            summary = self._truncate(result.reply.strip())
            if not summary:
                return
            await asyncio.to_thread(
                self.repository.replace_summary,
                conversation.id,
                conversation.summary_covered_sequence,
                summary,
                batch[-1].sequence,
            )
        except Exception:
            # Compaction must never turn a successfully answered chat request into an error.
            logger.exception("AI memory summarization failed, conversationId=%s", conversation_id)

    def _find_conversation(self, conversation_id: int) -> Conversation | None:
        return self.repository.get_by_id(conversation_id)

    async def _load_validated_messages(
            self, conversation: Conversation
    ) -> tuple[Conversation, list[ConversationMessage]]:
        """Recover an impossible coverage marker before it hides still-present short-term messages."""
        max_sequence = await asyncio.to_thread(
            self.repository.get_max_message_sequence, conversation.id
        )
        covered_sequence = conversation.summary_covered_sequence
        if covered_sequence <= max_sequence:
            messages = await asyncio.to_thread(
                self.repository.load_unsummarized_messages, conversation.id, covered_sequence
            )
            return conversation, messages

        logger.warning(
            "AI memory coverage is ahead of stored messages; restoring short-term context "
            "conversationId=%s userId=%s coveredSequence=%s maxSequence=%s",
            conversation.id, conversation.user_id, covered_sequence, max_sequence,
        )
        repaired = await asyncio.to_thread(
            self.repository.repair_invalid_summary_coverage,
            conversation.id,
            covered_sequence,
            max_sequence,
        )
        if repaired:
            conversation = replace(conversation, summary_covered_sequence=0)
        else:
            # A concurrent summary update won the compare-and-set. Reload its current marker once.
            latest = await asyncio.to_thread(self._find_conversation, conversation.id)
            if latest is not None and latest.summary_covered_sequence <= max_sequence:
                conversation = latest
            else:
                # Reading all remaining messages is safer than silently losing context again.
                conversation = replace(conversation, summary_covered_sequence=0)
        messages = await asyncio.to_thread(
            self.repository.load_unsummarized_messages, conversation.id, conversation.summary_covered_sequence
        )
        return conversation, messages

    @classmethod
    def _summary_instruction(cls) -> str:
        return (
            "你是健康助手的对话记忆整理器。只输出一段不超过800个中文字符的摘要。"
            "仅保留用户稳定饮食偏好、长期目标、回答偏好和未完成计划。"
            "删除今日热量、当天饮食、当天运动、临时数值、闲聊和未经确认的健康结论。"
            "新摘要必须覆盖旧摘要，不要解释整理过程。"
        )

    @staticmethod
    def _summary_input(old_summary: str, messages: list[ConversationMessage]) -> str:
        lines = ["【旧摘要】", old_summary or "暂无。", "", "【新增历史消息】"]
        for message in messages:
            role = "用户" if message.role == "user" else "助手"
            lines.append(f"[{role}] {message.content}")
        return "\n".join(lines)

    @classmethod
    def _truncate(cls, summary: str) -> str:
        return summary[:cls.MAX_SUMMARY_LENGTH]
