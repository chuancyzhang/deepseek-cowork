import unittest

from core.memory_update import estimate_tokens
from core.token_speed import TokenSpeedTracker


class TestTokenSpeedTracker(unittest.TestCase):
    def test_accumulated_estimate_matches_existing_token_rule(self):
        tracker = TokenSpeedTracker()
        tracker.begin("request-1", 10.0)

        tracker.record_text("a", 10.0)
        tracker.record_text("b", 10.2)
        tracker.record_text("cd 你好", 10.4)

        snapshot = tracker.snapshot(11.0)
        self.assertEqual(snapshot["current_tokens"], estimate_tokens("abcd 你好"))
        self.assertEqual(snapshot["current_tokens"], 3)

    def test_live_rate_uses_three_second_window_and_decays_to_zero(self):
        tracker = TokenSpeedTracker(window_seconds=3.0)
        tracker.begin("request-1", 0.0)
        tracker.record_text("你" * 6, 1.0)

        self.assertAlmostEqual(tracker.snapshot(2.0)["current_rate"], 6.0)
        self.assertAlmostEqual(tracker.snapshot(4.0)["current_rate"], 2.0)
        self.assertAlmostEqual(tracker.snapshot(4.1)["current_rate"], 0.0)

    def test_finish_keeps_completed_average_but_tool_only_does_not_overwrite_it(self):
        tracker = TokenSpeedTracker()
        tracker.begin("request-1", 0.0)
        tracker.record_text("你" * 12, 1.0)
        tracker.finish("request-1", 4.0)
        completed = tracker.snapshot(4.0)
        self.assertAlmostEqual(completed["last_rate"], 4.0)
        self.assertEqual(completed["last_tokens"], 12)
        self.assertEqual(completed["last_duration"], 3.0)

        tracker.begin("request-2", 5.0)
        tracker.finish("request-2", 8.0)
        tool_only = tracker.snapshot(8.0)
        self.assertAlmostEqual(tool_only["last_rate"], 4.0)
        self.assertEqual(tool_only["last_request_id"], "request-1")

    def test_retry_discards_failed_attempt_samples_and_preserves_last_average(self):
        tracker = TokenSpeedTracker()
        tracker.begin("completed", 0.0)
        tracker.record_text("你" * 4, 1.0)
        tracker.finish("completed", 3.0)

        tracker.begin("retrying", 4.0)
        tracker.record_text("你" * 20, 5.0)
        tracker.retry("retrying", 6.0)
        reset = tracker.snapshot(6.0)
        self.assertIsNone(reset["current_rate"])
        self.assertEqual(reset["current_tokens"], 0)
        self.assertAlmostEqual(reset["last_rate"], 2.0)

        tracker.record_text("你" * 6, 7.0)
        tracker.finish("retrying", 9.0)
        self.assertAlmostEqual(tracker.snapshot(9.0)["last_rate"], 3.0)

    def test_error_and_cancel_do_not_replace_last_completed_average(self):
        tracker = TokenSpeedTracker()
        tracker.begin("completed", 0.0)
        tracker.record_text("你" * 8, 1.0)
        tracker.finish("completed", 3.0)

        tracker.begin("failed", 4.0)
        tracker.record_text("你" * 100, 5.0)
        tracker.finish("failed", 6.0, status="error")
        self.assertAlmostEqual(tracker.snapshot(6.0)["last_rate"], 4.0)

        tracker.begin("stopped", 7.0)
        tracker.record_text("你" * 50, 8.0)
        tracker.cancel(9.0)
        self.assertAlmostEqual(tracker.snapshot(9.0)["last_rate"], 4.0)

    def test_request_mismatch_is_explicit(self):
        tracker = TokenSpeedTracker()
        tracker.begin("request-1", 0.0)
        with self.assertRaisesRegex(RuntimeError, "request_id mismatch"):
            tracker.finish("request-2", 1.0)

    def test_overlapping_provider_requests_are_rejected(self):
        tracker = TokenSpeedTracker()
        tracker.begin("request-1", 0.0)
        with self.assertRaisesRegex(RuntimeError, "before the active request finished"):
            tracker.begin("request-2", 1.0)

    def test_session_trackers_are_isolated_and_clear_drops_runtime_history(self):
        first = TokenSpeedTracker()
        second = TokenSpeedTracker()
        first.begin("first", 0.0)
        second.begin("second", 0.0)
        first.record_text("你" * 8, 1.0)
        second.record_text("你" * 2, 1.0)
        first.finish("first", 3.0)
        second.finish("second", 3.0)

        self.assertAlmostEqual(first.snapshot(3.0)["last_rate"], 4.0)
        self.assertAlmostEqual(second.snapshot(3.0)["last_rate"], 1.0)

        first.clear()
        self.assertIsNone(first.snapshot(4.0)["last_rate"])
        self.assertAlmostEqual(second.snapshot(4.0)["last_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
