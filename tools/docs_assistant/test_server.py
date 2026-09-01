import tempfile
import unittest
from dataclasses import replace

from tools.docs_assistant.server import (
    AssistantService,
    Config,
    _mcp_tool_result,
    build_context,
    extract_sources,
    retrieval_preview,
)
from tools.docs_assistant.memory import ConversationAccessError, ConversationStore


class DocsAssistantTests(unittest.TestCase):
    def test_reads_structured_mcp_result(self):
        payload = {
            "result": {
                "structuredContent": {
                    "result": {"results": [{"document": "guide"}]}
                }
            }
        }
        self.assertEqual(_mcp_tool_result(payload)["results"][0]["document"], "guide")

    def test_extracts_and_deduplicates_sources(self):
        results = [
            {
                "document": "guide",
                "page": 2,
                "score": 0.9,
                "citation_url": "https://example.com/guide#page=2",
                "content": "Useful content",
            },
            {
                "document": "guide",
                "page": 2,
                "score": 0.8,
                "content": "Duplicate",
            },
        ]
        sources = extract_sources(results, 0.05)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["title"], "guide · 第 2 页")

    def test_context_and_preview_include_citations(self):
        sources = [
            {
                "title": "guide",
                "url": "https://example.com/guide",
                "excerpt": "Install the SDK.",
            }
        ]
        self.assertIn("[1] guide", build_context(sources, 1_000))
        self.assertIn("[1] Install the SDK.", retrieval_preview(sources))

    def test_sqlite_memory_round_trip_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(
                f"{directory}/assistant.sqlite3",
                max_messages=3,
            )
            conversation_id = "550e8400-e29b-41d4-a716-446655440000"
            client_id = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
            store.ensure(conversation_id, client_id)
            for index in range(5):
                store.append(
                    conversation_id,
                    client_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"message-{index}",
                )

            messages = store.load(conversation_id, client_id)
            self.assertEqual([item["content"] for item in messages], [
                "message-2",
                "message-3",
                "message-4",
            ])

    def test_service_uses_persisted_history_and_isolates_clients(self):
        class StubService(AssistantService):
            def search(self, query):
                return []

            def generate(self, question, sources, history, page):
                previous = ",".join(item["content"] for item in history)
                return f"previous={previous}; current={question}"

        with tempfile.TemporaryDirectory() as directory:
            base_config = Config.from_env()
            config = replace(
                base_config,
                mcp_url="https://example.invalid/mcp",
                llm_url="",
                llm_model="",
                db_path=f"{directory}/assistant.sqlite3",
                memory_max_messages=12,
            )
            store = ConversationStore(config.db_path, max_messages=12)
            service = StubService(config, store)
            conversation_id = "550e8400-e29b-41d4-a716-446655440000"
            client_id = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"

            service.ask(
                {
                    "message": "first",
                    "conversation_id": conversation_id,
                    "client_id": client_id,
                }
            )
            second = service.ask(
                {
                    "message": "second",
                    "conversation_id": conversation_id,
                    "client_id": client_id,
                }
            )
            self.assertIn("first", second["answer"])
            self.assertIn("second", second["answer"])
            with self.assertRaises(ConversationAccessError):
                service.ask(
                    {
                        "message": "not yours",
                        "conversation_id": conversation_id,
                        "client_id": "6ba7b811-9dad-41d1-80b4-00c04fd430c9",
                    }
                )


if __name__ == "__main__":
    unittest.main()
