import unittest
from decimal import Decimal
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
    _record_deepseek_billing_request_start = MainWindow._record_deepseek_billing_request_start

    def __init__(self):
        self.refreshed = []

    def refresh_token_usage_label(self, session_id=None):
        self.refreshed.append(session_id)


class _BillingResultHarness:
    _handle_deepseek_balance_result = MainWindow._handle_deepseek_balance_result
    _safe_finalize_deepseek_billing = MainWindow._safe_finalize_deepseek_billing

    def __init__(self, state):
        self.state = state
        self.refreshed = []
        self.persisted = []

    def get_session(self, session_id):
        return self.state if session_id == self.state.session_id else None

    def refresh_token_usage_label(self, session_id=None):
        self.refreshed.append(session_id)

    def _persist_deepseek_billing_state(self, state):
        self.persisted.append(state.session_id)


class _BillingStartHarness:
    _start_deepseek_billing_run = MainWindow._start_deepseek_billing_run

    def _model_profile_snapshot_for_state(self, _state):
        return {
            "id": "profile-1",
            "model_name": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-key",
        }

    def refresh_token_usage_label(self, _session_id=None):
        return None


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeBalanceWorker:
    created = []

    def __init__(self, session_id, run_id, profile_id, api_key, _parent):
        self.session_id = session_id
        self.run_id = run_id
        self.profile_id = profile_id
        self.api_key = api_key
        self.result_signal = _FakeSignal()
        self.finished = _FakeSignal()
        self.started = False
        self.__class__.created.append(self)

    def start(self):
        self.started = True


class _BillingFinalizeHarness:
    _finalize_deepseek_billing = MainWindow._finalize_deepseek_billing
    _safe_handle_deepseek_balance_result = MainWindow._safe_handle_deepseek_balance_result

    def __init__(self):
        self._deepseek_balance_workers = {}

    def _model_profile_for_state(self, _state, model_id=None):
        return {
            "id": model_id,
            "model_name": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-key",
        }

    def _persist_deepseek_billing_state(self, _state):
        return None

    def refresh_token_usage_label(self, _session_id=None):
        return None


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

    def test_deepseek_billing_appends_to_existing_chip_without_replacing_usage(self):
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cached_input_tokens": 80,
        }
        billing = {
            "run_id": "run-1",
            "profile_id": "profile-1",
            "model": "deepseek-v4-flash",
            "cost_status": "available",
            "costs": {"CNY": "0.0051", "USD": "0.0007"},
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_input_tokens": 80,
                "uncached_input_tokens": 20,
                "request_count": 1,
            },
            "pricing_version": "2026-08-17",
            "pricing_periods": ["off_peak"],
            "balance_status": "succeeded",
            "balance": {
                "is_available": True,
                "fetched_at": 1_700_000_000,
                "balance_infos": [{
                    "currency": "CNY",
                    "total_balance": "23.45",
                    "granted_balance": "3.45",
                    "topped_up_balance": "20",
                }],
            },
        }

        text = format_token_usage_chip_text(usage, None, billing)
        tooltip = format_token_usage_tooltip(usage, billing_snapshot=billing)

        self.assertEqual(text, "120 tokens · 缓存 80 / 80% · 本轮 ¥0.0051 · 余 ¥23.45")
        self.assertIn("本次运行费用（DeepSeek 官方目录价计算）", tooltip)
        self.assertIn("本轮预估：¥0.0051", tooltip)
        self.assertIn("运行结束时余额：¥23.45", tooltip)

    def test_balance_failure_only_changes_billing_detail(self):
        usage = {"total_tokens": 120, "cached_input_tokens": 0}
        billing = {
            "run_id": "run-1",
            "cost_status": "available",
            "costs": {"CNY": "0.00001"},
            "balance_status": "failed",
            "balance_error_category": "timeout",
        }

        text = format_token_usage_chip_text(usage, None, billing)
        tooltip = format_token_usage_tooltip(usage, billing_snapshot=billing)

        self.assertEqual(text, "120 tokens · 缓存 0 · 本轮 ＜¥0.0001")
        self.assertIn("余额：查询失败（查询超时）", tooltip)
        self.assertIn("不影响本轮结果", tooltip)

    def test_run_billing_accumulates_each_request_once(self):
        state = SimpleNamespace(
            session_id="session-1",
            current_deepseek_billing={
                "run_id": "run-1",
                "profile_id": "profile-1",
                "model": "deepseek-v4-flash",
                "request_started_at": {},
                "counted_request_ids": set(),
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "uncached_input_tokens": 0,
                    "request_count": 0,
                },
                "cost_status": "available",
                "costs": {"CNY": "0", "USD": "0"},
                "pricing_periods": [],
                "finalized": False,
            },
        )
        started_at = 1_788_000_000.0
        event = {
            "request_id": "request-1",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "timestamp": started_at,
        }
        usage = {
            "request_id": "request-1",
            "profile_id": "profile-1",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "input_tokens": 1000,
            "output_tokens": 100,
            "cached_input_tokens": 800,
            "uncached_input_tokens": 200,
            "cache_metrics_status": "deepseek_prompt_cache",
        }
        with patch("main.log_chat_runtime_debug"):
            MainWindow._record_deepseek_billing_request_start(None, state, event)
            MainWindow._record_deepseek_billing_usage(None, state, usage)
            MainWindow._record_deepseek_billing_usage(None, state, usage)

        billing = state.current_deepseek_billing
        self.assertEqual(billing["usage"]["request_count"], 1)
        self.assertEqual(billing["usage"]["input_tokens"], 1000)
        self.assertEqual(billing["cost_status"], "available")
        self.assertTrue(Decimal(billing["costs"]["CNY"]) > 0)

    def test_billing_run_state_never_keeps_api_key(self):
        state = SimpleNamespace(
            session_id="session-1",
            current_deepseek_billing={},
            last_deepseek_billing={},
        )
        harness = _BillingStartHarness()
        with patch("main.log_chat_runtime_debug"):
            harness._start_deepseek_billing_run(state, "run-1")

        self.assertEqual(state.current_deepseek_billing["run_id"], "run-1")
        self.assertNotIn("api_key", state.current_deepseek_billing)

    def test_finalize_starts_exactly_one_balance_worker(self):
        state = SimpleNamespace(
            session_id="session-1",
            last_deepseek_billing={},
            current_deepseek_billing={
                "run_id": "run-1",
                "profile_id": "profile-1",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
                "usage": {"request_count": 1, "input_tokens": 10, "output_tokens": 2},
                "cost_status": "unavailable",
                "cost_reason_code": "missing_cache_usage",
                "costs": {},
                "pricing_version": "2026-08-17",
                "pricing_periods": [],
                "finalized": False,
            },
        )
        harness = _BillingFinalizeHarness()
        _FakeBalanceWorker.created = []
        with patch("main.DeepSeekBalanceWorker", _FakeBalanceWorker), patch(
            "main.log_chat_runtime_debug"
        ):
            first = harness._finalize_deepseek_billing(state, "run-1")
            second = harness._finalize_deepseek_billing(state, "run-1")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(_FakeBalanceWorker.created), 1)
        self.assertTrue(_FakeBalanceWorker.created[0].started)
        self.assertEqual(_FakeBalanceWorker.created[0].api_key, "secret-key")
        self.assertNotIn("api_key", state.current_deepseek_billing)

    def test_balance_failure_is_runtime_only_and_does_not_persist(self):
        state = SimpleNamespace(
            session_id="session-1",
            last_deepseek_billing={
                "run_id": "run-1",
                "profile_id": "profile-1",
                "cost_status": "available",
                "costs": {"CNY": "0.0051"},
                "balance_status": "querying",
            },
        )
        harness = _BillingResultHarness(state)
        with patch("main.log_chat_runtime_debug"):
            harness._handle_deepseek_balance_result(
                {
                    "ok": False,
                    "session_id": "session-1",
                    "run_id": "run-1",
                    "profile_id": "profile-1",
                    "error_category": "timeout",
                }
            )

        self.assertEqual(state.last_deepseek_billing["balance_status"], "failed")
        self.assertEqual(state.last_deepseek_billing["balance_error_category"], "timeout")
        self.assertEqual(harness.persisted, [])
        self.assertEqual(harness.refreshed, ["session-1"])

    def test_billing_finalize_exception_is_swallowed(self):
        state = SimpleNamespace(
            session_id="session-1",
            current_deepseek_billing={"finalized": False},
        )
        harness = _BillingResultHarness(state)
        harness._finalize_deepseek_billing = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("metadata unavailable")
        )
        with patch("main.log_chat_runtime_debug"):
            result = harness._safe_finalize_deepseek_billing(state, "run-1")

        self.assertFalse(result)
        self.assertTrue(state.current_deepseek_billing["finalized"])


if __name__ == "__main__":
    unittest.main()
