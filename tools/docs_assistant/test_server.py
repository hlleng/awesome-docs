import unittest

from tools.docs_assistant.server import (
    _mcp_tool_result,
    build_context,
    extract_sources,
    retrieval_preview,
)


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


if __name__ == "__main__":
    unittest.main()
