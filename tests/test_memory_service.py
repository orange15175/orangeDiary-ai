import unittest
from types import SimpleNamespace

from app.repositories.conversation_repository import Conversation, ConversationMessage
from app.services.memory_service import MemoryContext, MemoryService


class FakeRepository:
    def __init__(self):
        self.conversation = Conversation(1, 7, "用户希望回答简短。", 0)
        self.messages = [
            ConversationMessage(index, "user" if index % 2 else "assistant", f"消息{index}")
            for index in range(1, 13)
        ]
        self.replacements = []

    def get_or_create(self, user_id):
        return self.conversation

    def get_by_id(self, conversation_id):
        return self.conversation if conversation_id == self.conversation.id else None

    def load_unsummarized_messages(self, conversation_id, covered_sequence):
        return [message for message in self.messages if message.sequence > covered_sequence]

    def get_max_message_sequence(self, conversation_id):
        return self.messages[-1].sequence if self.messages else 0

    def repair_invalid_summary_coverage(self, conversation_id, expected_covered_sequence, max_sequence):
        if self.conversation.summary_covered_sequence != expected_covered_sequence:
            return False
        if expected_covered_sequence <= max_sequence:
            return False
        self.conversation = Conversation(1, 7, self.conversation.summary, 0)
        return True

    def append_turn(self, conversation_id, user_message, assistant_message):
        next_sequence = self.messages[-1].sequence + 1 if self.messages else 1
        self.messages.extend([
            ConversationMessage(next_sequence, "user", user_message),
            ConversationMessage(next_sequence + 1, "assistant", assistant_message),
        ])

    def replace_summary(self, conversation_id, expected_covered_sequence, summary, new_covered_sequence):
        self.replacements.append((expected_covered_sequence, summary, new_covered_sequence))
        self.conversation = Conversation(1, 7, summary, new_covered_sequence)
        return True


class FakeDeepSeek:
    def __init__(self):
        self.messages = []

    async def chat(self, messages, json_mode=False):
        self.messages.append(messages)
        return SimpleNamespace(reply="用户偏好简短回答，正在减脂。", prompt_tokens=1, completion_tokens=1, total_tokens=2)


class MemoryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_twelve_unsummarized_messages_compact_the_oldest_six(self):
        repository = FakeRepository()
        deepseek = FakeDeepSeek()
        service = MemoryService(repository, deepseek)

        await service._summarize_if_needed(1)

        self.assertEqual(len(repository.replacements), 1)
        self.assertEqual(repository.replacements[0][0], 0)
        self.assertEqual(repository.replacements[0][2], 6)
        self.assertEqual(repository.conversation.summary_covered_sequence, 6)
        self.assertEqual(
            [message.sequence for message in repository.load_unsummarized_messages(1, 6)],
            [7, 8, 9, 10, 11, 12],
        )

    async def test_short_context_contains_all_unsummarized_messages(self):
        repository = FakeRepository()
        repository.messages = repository.messages[:6]
        service = MemoryService(repository, FakeDeepSeek())

        context = await service.load_context(7)

        self.assertIsInstance(context, MemoryContext)
        self.assertEqual([message.sequence for message in context.messages], [1, 2, 3, 4, 5, 6])

    async def test_invalid_coverage_repairs_to_zero_and_restores_existing_messages(self):
        repository = FakeRepository()
        repository.messages = repository.messages[:4]
        repository.conversation = Conversation(1, 7, "已有长期摘要。", 98)
        service = MemoryService(repository, FakeDeepSeek())

        context = await service.load_context(7)

        self.assertEqual(context.conversation.summary_covered_sequence, 0)
        self.assertEqual(repository.conversation.summary_covered_sequence, 0)
        self.assertEqual([message.sequence for message in context.messages], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
