import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

spec = importlib.util.spec_from_file_location(
    "web_search_impl",
    os.path.join(os.path.dirname(__file__), "../ai_skills/web-search/impl.py"),
)
impl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impl)


def context(provider="anysearch", anysearch_key="", tavily_key=""):
    values = {
        "SEARCH_PROVIDER": provider,
        "ANYSEARCH_API_KEY": anysearch_key,
        "TAVILY_API_KEY": tavily_key,
    }
    return {
        "skill_config": values,
        "skill_config_env": {key: value for key, value in values.items() if value},
    }


class ConfigStub:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_skill_config(self, skill_name):
        return dict(self.values) if skill_name == "web-search" else {}

    def set_skill_config(self, skill_name, values):
        if skill_name != "web-search":
            raise AssertionError(skill_name)
        self.values = dict(values)


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

    @patch.object(impl, "_call_anysearch")
    def test_anysearch_search_normalizes_results_and_uses_key(self, call_anysearch):
        call_anysearch.return_value = {
            "results": [
                {"title": "OpenAI", "url": "https://openai.com/research", "content": "Research updates"},
                {"title": "Filtered", "url": "https://example.com/no", "content": "No"},
            ]
        }
        payload = json.loads(
            impl.search_web(
                "openai",
                allowed_domains=["openai.com"],
                _context=context(anysearch_key="as_sk_private"),
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_used"], "anysearch")
        self.assertEqual(payload["auth_mode"], "api_key")
        self.assertEqual(payload["sources"], ["https://openai.com/research"])
        args = call_anysearch.call_args.args
        self.assertEqual(args[0], "search")
        self.assertEqual(args[2], "as_sk_private")

    @patch.object(impl, "_call_tavily_search")
    def test_tavily_override_uses_keyless_and_maps_results(self, call_tavily):
        call_tavily.return_value = {
            "results": [
                {
                    "title": "Tavily result",
                    "url": "https://docs.tavily.com/",
                    "content": "Current docs",
                    "score": 0.9,
                }
            ]
        }
        payload = json.loads(
            impl.search_web(
                "tavily docs",
                provider="tavily",
                _context=context(provider="anysearch"),
            )
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_used"], "tavily")
        self.assertEqual(payload["auth_mode"], "keyless")
        self.assertIn("keyless_rate_limits_apply", payload["warnings"])
        self.assertEqual(payload["results"][0]["score"], 0.9)

    @patch.object(impl, "_call_tavily_search")
    @patch.object(impl, "_call_anysearch")
    def test_provider_failure_does_not_fall_back(self, call_anysearch, call_tavily):
        call_anysearch.side_effect = impl.ProviderError("quota_exhausted", "AnySearch quota exhausted.")

        payload = json.loads(impl.search_web("query", _context=context("anysearch")))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["provider_used"], "anysearch")
        self.assertEqual(payload["error"], "quota_exhausted")
        self.assertEqual(payload["retryable_with"], "tavily")
        call_tavily.assert_not_called()

    def test_region_and_language_fail_explicitly(self):
        payload = json.loads(impl.search_web("query", region="cn", language="zh-CN"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "unsupported_parameter")
        self.assertEqual(payload["unsupported_parameters"], ["region", "language"])

    @patch.object(impl, "_call_anysearch")
    def test_search_cache_isolated_by_provider_and_auth_mode(self, call_anysearch):
        call_anysearch.return_value = {
            "results": [{"title": "One", "url": "https://example.com/one", "content": "one"}]
        }
        first = json.loads(impl.search_web("cache", _context=context(anysearch_key="as_sk_one")))
        second = json.loads(impl.search_web("cache", _context=context(anysearch_key="as_sk_two")))
        keyless = json.loads(impl.search_web("cache", _context=context()))

        self.assertTrue(first["ok"])
        self.assertIn("cache_hit", second["warnings"])
        self.assertEqual(keyless["auth_mode"], "keyless")
        self.assertEqual(call_anysearch.call_count, 2)
        cache_text = json.dumps(list(impl._SEARCH_CACHE.keys()))
        self.assertNotIn("as_sk_one", cache_text)
        self.assertNotIn("as_sk_two", cache_text)

    @patch.object(impl, "_call_anysearch")
    def test_read_web_article_uses_anysearch_extract(self, call_anysearch):
        call_anysearch.return_value = "# Title\n\nUseful extracted content."
        payload = json.loads(
            impl.read_web_article("example.com/page", max_chars=30, _context=context())
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_used"], "anysearch")
        self.assertEqual(payload["extractor_used"], "anysearch")
        self.assertLessEqual(len(payload["content"]), 30)
        call_anysearch.assert_called_once_with("extract", {"url": "https://example.com/page"}, "")

    @patch.object(impl, "_call_tavily_extract")
    def test_read_web_article_maps_tavily_extract(self, call_extract):
        call_extract.return_value = {
            "results": [
                {
                    "url": "https://example.com/page",
                    "raw_content": "# Title\n\nTavily markdown.",
                }
            ],
            "failed_results": [],
        }
        payload = json.loads(
            impl.read_web_article(
                "https://example.com/page",
                provider="tavily",
                _context=context("tavily", tavily_key="tvly-private"),
            )
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_used"], "tavily")
        call_extract.assert_called_once_with("https://example.com/page", "tvly-private")

    @patch.object(impl, "_call_anysearch")
    def test_batch_search_and_vertical_discovery(self, call_anysearch):
        call_anysearch.side_effect = [
            {"results": [{"query": "one"}]},
            "| domain | sub_domain |\n| finance | finance.quote |",
        ]
        batch = json.loads(
            impl.batch_search_web(["one", {"query": "two"}], _context=context())
        )
        domains = json.loads(
            impl.get_web_search_sub_domains(domain="finance", _context=context())
        )

        self.assertTrue(batch["ok"])
        self.assertTrue(domains["ok"])
        self.assertIn("finance.quote", domains["sub_domains"])
        self.assertEqual(call_anysearch.call_args_list[0].args[0], "batch_search")
        self.assertEqual(call_anysearch.call_args_list[1].args[0], "get_sub_domains")

    def test_anysearch_only_tools_reject_tavily(self):
        batch = json.loads(
            impl.batch_search_web(["one"], provider="tavily", _context=context("tavily"))
        )
        domains = json.loads(
            impl.get_web_search_sub_domains(
                domain="finance",
                provider="tavily",
                _context=context("tavily"),
            )
        )
        self.assertEqual(batch["error"], "unsupported_provider")
        self.assertEqual(domains["error"], "unsupported_provider")

    @patch.object(impl, "read_web_article")
    def test_read_article_alias_calls_read_web_article(self, read_web_article):
        read_web_article.return_value = json.dumps({"ok": True})
        payload = json.loads(
            impl.read_article(
                "https://example.com/page",
                max_chars=50,
                provider="tavily",
                _context={"x": 1},
            )
        )
        self.assertTrue(payload["ok"])
        read_web_article.assert_called_once_with(
            "https://example.com/page",
            max_chars=50,
            provider="tavily",
            _context={"x": 1},
        )

    def test_registration_requires_explicit_confirmation(self):
        with patch.object(impl.requests, "post") as post:
            payload = json.loads(
                impl.register_anysearch_api_key("person@example.com", user_confirmed=False)
            )
        self.assertEqual(payload["error"], "user_confirmation_required")
        post.assert_not_called()

    @patch.object(impl.requests, "post")
    def test_registration_saves_key_without_returning_or_logging_it(self, post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "code": 0,
            "message": "success",
            "data": {
                "username": "person@example.com",
                "login_url": "https://www.anysearch.com/login",
                "api_key": {
                    "key": "as_sk_super_secret",
                    "key_prefix": "as_sk_super...",
                },
            },
        }
        post.return_value = response
        config = ConfigStub({"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "tvly-kept"})
        publisher = MagicMock()
        registration_context = {
            "skill_manager": MagicMock(config_manager=config),
            "skill_change_publisher": publisher,
            "session_id": "session-1",
        }

        with patch.object(impl.logger, "info") as log_info:
            raw = impl.register_anysearch_api_key(
                "person@example.com",
                user_confirmed=True,
                _context=registration_context,
            )
        payload = json.loads(raw)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertNotIn("as_sk_super_secret", raw)
        self.assertEqual(config.values["ANYSEARCH_API_KEY"], "as_sk_super_secret")
        self.assertEqual(config.values["SEARCH_PROVIDER"], "tavily")
        self.assertEqual(config.values["TAVILY_API_KEY"], "tvly-kept")
        self.assertNotIn("person@example.com", repr(log_info.call_args_list))
        self.assertNotIn("as_sk_super_secret", repr(log_info.call_args_list))
        publisher.assert_called_once()

    @patch.object(impl.requests, "post")
    def test_registration_maps_documented_errors(self, post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "code": -1,
            "message": "Rate limited, retry after 300 seconds.",
        }
        post.return_value = response

        payload = json.loads(
            impl.register_anysearch_api_key(
                "person@example.com",
                user_confirmed=True,
            )
        )
        self.assertEqual(payload["error"], "rate_limited")
        self.assertEqual(payload["retry_after_seconds"], 300)


if __name__ == "__main__":
    unittest.main()
