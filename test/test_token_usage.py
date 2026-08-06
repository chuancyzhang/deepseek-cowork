import unittest

from main import (
    format_token_usage_tooltip,
    normalize_last_token_usage,
    token_usage_bucket_key,
)


class TestTokenUsageBuckets(unittest.TestCase):
    def test_protocol_switch_uses_separate_cache_buckets(self):
        responses_key = token_usage_bucket_key(
            {
                "provider": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6",
                "protocol": "responses",
                "profile_id": "profile-a",
            }
        )
        chat_key = token_usage_bucket_key(
            {
                "provider": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6",
                "protocol": "chat_completions",
                "profile_id": "profile-a",
            }
        )

        self.assertNotEqual(responses_key, chat_key)

    def test_last_usage_preserves_provider_schema_diagnostics(self):
        usage = normalize_last_token_usage(
            {
                "input_tokens": 10_000,
                "cached_input_tokens": 8_576,
                "provider": "DeepSeek",
                "model": "deepseek-v4-flash",
                "protocol": "chat_completions",
                "cache_metrics_status": "deepseek_prompt_cache",
                "request_id": "req-1",
            }
        )

        tooltip = format_token_usage_tooltip(usage, usage)

        self.assertIn("最近一轮请求（本轮，不是累计）", tooltip)
        self.assertIn("DeepSeek / deepseek-v4-flash / chat_completions", tooltip)
        self.assertIn("deepseek_prompt_cache", tooltip)
        self.assertIn("req-1", tooltip)


if __name__ == "__main__":
    unittest.main()
