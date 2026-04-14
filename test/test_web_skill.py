import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

spec = importlib.util.spec_from_file_location(
    "impl", os.path.join(os.path.dirname(__file__), "../skills/web-search/impl.py")
)
impl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impl)


class TestWebSkill(unittest.TestCase):
    def setUp(self):
        impl._SEARCH_CACHE.clear()
        impl._ARTICLE_CACHE.clear()

    def test_search_web_rejects_empty_query(self):
        payload = json.loads(impl.search_web(""))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "query_required")

    def test_search_web_rejects_conflicting_domain_filters(self):
        payload = json.loads(
            impl.search_web(
                "openai",
                allowed_domains=["openai.com"],
                blocked_domains=["example.com"],
            )
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "allowed_and_blocked_domains_conflict")

    @patch.object(impl, "_search_bing")
    @patch.object(impl, "_search_duckduckgo")
    def test_search_web_falls_back_to_bing(self, mock_duckduckgo, mock_bing):
        mock_duckduckgo.side_effect = RuntimeError("ddg down")
        mock_bing.return_value = [
            {
                "title": "OpenAI",
                "url": "https://openai.com/research",
                "snippet": "Research updates",
                "domain": "openai.com",
                "provider": "bing",
            }
        ]

        payload = json.loads(impl.search_web("openai", max_results=5))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_used"], "bing")
        self.assertEqual(payload["fallback_chain"], ["duckduckgo", "bing"])
        self.assertEqual(payload["results"][0]["provider"], "bing")

    @patch.object(impl, "_search_duckduckgo")
    def test_search_web_dedupes_urls_and_limits_same_domain(self, mock_duckduckgo):
        mock_duckduckgo.return_value = [
            {
                "title": "A1",
                "url": "https://example.com/a",
                "snippet": "one",
                "domain": "example.com",
                "provider": "duckduckgo",
            },
            {
                "title": "A1",
                "url": "https://example.com/a",
                "snippet": "dup",
                "domain": "example.com",
                "provider": "duckduckgo",
            },
            {
                "title": "A2",
                "url": "https://example.com/b",
                "snippet": "two",
                "domain": "example.com",
                "provider": "duckduckgo",
            },
            {
                "title": "A3",
                "url": "https://example.com/c",
                "snippet": "three",
                "domain": "example.com",
                "provider": "duckduckgo",
            },
            {
                "title": "B1",
                "url": "https://other.com/x",
                "snippet": "other",
                "domain": "other.com",
                "provider": "duckduckgo",
            },
        ]

        payload = json.loads(impl.search_web("query", max_results=5))
        urls = [item["url"] for item in payload["results"]]
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls.count("https://example.com/a"), 1)
        self.assertNotIn("https://example.com/c", urls)

    @patch.object(impl, "_request_text")
    def test_read_web_article_uses_first_successful_proxy(self, mock_request_text):
        mock_request_text.return_value = ("# Title\n\nThis is a markdown body with enough useful content to pass validation." * 3, "https://markdown.new/test")
        payload = json.loads(impl.read_web_article("example.com/page"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["extractor_used"], "markdown.new")
        self.assertIn("Title", payload["title"])
        self.assertNotIn("<html", payload["content"].lower())

    @patch.object(impl, "_extract_with_scrapling")
    @patch.object(impl, "_request_text")
    def test_read_web_article_falls_back_to_scrapling(self, mock_request_text, mock_scrapling):
        mock_request_text.return_value = ("<html><body>blocked</body></html>", "https://proxy")
        mock_scrapling.return_value = "Useful extracted article text " * 12
        payload = json.loads(impl.read_web_article("https://example.com/page"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["extractor_used"], "scrapling")

    @patch.object(impl, "_extract_with_scrapling")
    @patch.object(impl, "_request_text")
    def test_read_web_article_returns_structured_error_when_all_extractors_fail(self, mock_request_text, mock_scrapling):
        mock_request_text.side_effect = RuntimeError("proxy failure")
        mock_scrapling.side_effect = RuntimeError("scrapling failure")
        payload = json.loads(impl.read_web_article("https://example.com/page"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "scrapling failure")
        self.assertTrue(payload["warnings"])

    @patch.object(impl, "_request_text")
    def test_read_web_article_respects_max_chars(self, mock_request_text):
        mock_request_text.return_value = ("# Title\n\n" + ("content " * 500), "https://markdown.new/test")
        payload = json.loads(impl.read_web_article("https://example.com/page", max_chars=100))
        self.assertTrue(payload["ok"])
        self.assertLessEqual(len(payload["content"]), 100)

    @patch.object(impl, "read_web_article")
    def test_read_article_alias_calls_read_web_article(self, mock_read_web_article):
        mock_read_web_article.return_value = json.dumps({"ok": True})
        payload = json.loads(impl.read_article("https://example.com/page", max_chars=50))
        self.assertTrue(payload["ok"])
        mock_read_web_article.assert_called_once_with("https://example.com/page", max_chars=50)


if __name__ == "__main__":
    unittest.main()
