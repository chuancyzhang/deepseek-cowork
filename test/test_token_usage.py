import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import (
    MainWindow,
    format_token_usage_chip_text,
    format_token_usage_tooltip,
    normalize_last_token_usage,
    token_usage_bucket_key,
)
from core.token_speed import TokenSpeedTracker


class _FakeTimer:
    def __init__(self):
        self.active = False

    def isActive(self):
        return self.active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class _TokenSpeedHarness:
    _fail_token_speed_monitor = MainWindow._fail_token_speed_monitor
    _start_token_speed_monitor = MainWindow._start_token_speed_monitor
    _finish_token_speed_monitor = MainWindow._finish_token_speed_monitor
    _record_token_speed_delta = MainWindow._record_token_speed_delta

    def __init__(self):
        self.refreshed = []

    def refresh_token_usage_label(self, session_id=None):
        self.refreshed.append(session_id)


class TestTokenUsageBuckets(unittest.TestCase):
    def test_speed_is_appended_without_replacing_cache_summary(self):
        usage = {
            "input_tokens": 409_000,
            "output_tokens": 400,
            "total_tokens": 409_400,
            "cached_input_tokens": 391_700,
        }

        text = format_token_usage_chip_text(
            usage,
            {"active": True, "current_rate": 48.2},
        )

        self.assertEqual(
            text,
            "409.4K tokens · 缓存 391.7K / 96% · 速度 48.2 tok/s",
        )

    def test_no_speed_snapshot_keeps_existing_chip_text(self):
        usage = {"total_tokens": 100, "cached_input_tokens": 0}
        self.assertEqual(
            format_token_usage_chip_text(usage),
            "100 tokens · 缓存 0",
        )

    def test_idle_speed_and_speed_details_use_runtime_snapshot(self):
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cached_input_tokens": 80,
        }
        speed = {
            "active": False,
            "last_rate": 12.5,
            "last_tokens": 50,
            "last_duration": 4.0,
        }

        self.assertEqual(
            format_token_usage_chip_text(usage, speed),
            "120 tokens · 缓存 80 / 80% · 最近 12.5 tok/s",
        )
        tooltip = format_token_usage_tooltip(usage, speed_snapshot=speed)
        self.assertIn("缓存输入：80 (80.0%)", tooltip)
        self.assertIn("最近完成请求均速：12.5 tok/s", tooltip)
        self.assertIn("统计思考与正文，不含 Tool 参数", tooltip)

    def test_waiting_and_stalled_speed_have_explicit_chip_states(self):
        usage = {"total_tokens": 120, "cached_input_tokens": 0}
        self.assertEqual(
            format_token_usage_chip_text(
                usage,
                {"active": True, "current_rate": None},
            ),
            "120 tokens · 缓存 0 · 速度 --",
        )
        self.assertEqual(
            format_token_usage_chip_text(
                usage,
                {"active": True, "current_rate": 0.0},
            ),
            "120 tokens · 缓存 0 · 速度 0.0 tok/s",
        )

    def test_main_window_monitor_helpers_drive_timer_and_runtime_state(self):
        harness = _TokenSpeedHarness()
        state = SimpleNamespace(
            session_id="session-1",
            token_speed_tracker=TokenSpeedTracker(),
            token_speed_timer=_FakeTimer(),
        )
        with patch("main.log_chat_runtime_debug"), patch(
            "main.time.monotonic",
            side_effect=[0.0, 1.0, 2.0, 4.0],
        ):
            harness._start_token_speed_monitor(
                state,
                {"type": "provider_request_start", "request_id": "request-1"},
            )
            harness._record_token_speed_delta(state, "你" * 8, "thinking")
            harness._record_token_speed_delta(state, "你" * 4, "content")
            harness._finish_token_speed_monitor(
                state,
                {
                    "type": "provider_request_finish",
                    "request_id": "request-1",
                    "status": "completed",
                },
            )

        snapshot = state.token_speed_tracker.snapshot(4.0)
        self.assertFalse(state.token_speed_timer.isActive())
        self.assertAlmostEqual(snapshot["last_rate"], 4.0)
        self.assertEqual(snapshot["last_tokens"], 12)
        self.assertEqual(harness.refreshed, ["session-1", "session-1", "session-1"])

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
