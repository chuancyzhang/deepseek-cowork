"""Diagnostics must not own application state or erase in-use/unknown files."""
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from unittest.mock import patch
import unittest

from core import runtime_logs as logs
from core.process_utils import ProcessSingletonLock, build_process_singleton_lock_path


NOW = datetime(2026, 10, 16, 12).astimezone()


class RuntimeLogsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def legacy(self, name, content, age=0):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        stamp = (NOW - timedelta(days=age)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_threads_preserve_full_multiline_events_and_newline_timestamps_in_payload(self):
        def write(number):
            return logs.append_log("daemon", f"event={number}\n[2020-01-01 00:00:00] payload\ntrace={number}", self.root, now=NOW)
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(write, range(120)))
        self.assertEqual(len(set(paths)), 1)
        result = logs.query_logs(self.root, now=NOW)
        self.assertEqual(result.total, 120)
        self.assertFalse(result.issues)
        for record in result.records:
            number = record.text.split("event=", 1)[1].splitlines()[0]
            self.assertIn(f"trace={number}\n", record.text)
            self.assertIn("[2020-01-01 00:00:00] payload", record.text)
            self.assertIn(f"pid={os.getpid()}", record.text)

    def test_processes_use_separate_files_without_lost_records(self):
        code = ("import sys; from core.runtime_logs import append_log; "
                "[append_log('daemon', 'child='+str(n), sys.argv[1]) for n in range(25)]")
        children = [subprocess.Popen([sys.executable, "-c", code, str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(2)]
        for child in children:
            _, error = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
        files, issues = logs.collect_files(self.root)
        self.assertFalse(issues)
        self.assertEqual(len(files), 2)
        self.assertEqual(sum(len(list(logs.iter_records(file))) for file in files), 50)

    def test_day_rollover_and_retention_boundary_only_touch_owned_logs(self):
        paths = {}
        for age in (0, 1, 29, 30, 40):
            paths[age] = Path(logs.append_log("daemon", f"age={age}", self.root, now=NOW - timedelta(days=age)))
        protected = [
            self.legacy("logs/2026-09-01/user-output.log", "private"),
            self.legacy("chat_history/runtime_journal_v2/s/events/run.jsonl", "recovery", 90),
            self.legacy("chat_history/chat_history.sqlite", b"database", 90),
            self.legacy("updates/transactions/t/backup/daemon.log", "backup", 90),
            self.legacy("config.json", "credentials", 90),
            self.legacy("聊天记录-用户导出.log", "user export", 90),
        ]
        before = {path: path.read_bytes() for path in protected}
        result = logs.cleanup_logs(self.root, now=NOW)
        self.assertEqual(result.removed, 2)
        self.assertFalse(result.issues)
        for age, path in paths.items():
            self.assertEqual(path.exists(), age < 30)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_legacy_trim_preserves_multiline_and_original_invalid_bytes(self):
        path = self.legacy("ui_error.log", b"[2026-09-16 10:00:00] expired\nold traceback\n"
                           b"[2026-09-17 00:00:00] boundary\ntrace \xff\n"
                           b"[2026-10-16 11:00:00] current\ncurrent traceback\n")
        result = logs.cleanup_logs(self.root, now=NOW)
        self.assertEqual(result.trimmed, 1)
        content = path.read_bytes()
        self.assertNotIn(b"expired", content)
        self.assertNotIn(b"old traceback", content)
        self.assertIn(b"trace \xff", content)
        self.assertIn(b"current traceback", content)
        parsed = logs.query_logs(self.root, days=30, now=NOW)
        self.assertEqual(parsed.total, 2)
        self.assertIn("\ufffd", parsed.records[0].text)
        self.assertFalse(list(self.root.glob(".log-retention-*.tmp")))

    def test_legacy_time_formats_and_missing_timestamp_are_visible(self):
        self.legacy("theme_debug.log", "2026-10-16 08:00:00 theme_start\n")
        self.legacy("logs/browser_skill_runtime.log", '{"timestamp":"2026-10-16T00:05:00Z","stage":"ready"}\n')
        self.legacy("updates/app_update.log", '{"time":"2026-10-16 09:00:00","phase":"download"}\n')
        unknown = self.legacy("updates/transactions/t/update-launch.log", "unparsed original output\n")
        result = logs.query_logs(self.root, now=NOW)
        self.assertEqual(result.total, 4)
        record = next(item for item in result.records if item.file.path == unknown)
        self.assertTrue(record.inferred_time)
        self.assertIn("使用文件修改时间", record.display_text())
        self.assertIn("unparsed original output", record.display_text())

    def test_unknown_time_ages_by_mtime_and_does_not_reset_on_trim(self):
        recent = self.legacy("ui_error.log", "orphan output\n[2026-09-01 01:00:00] expired\n", age=2)
        old = self.legacy("startup.log", "undated old output", age=35)
        old_mtime = recent.stat().st_mtime_ns
        result = logs.cleanup_logs(self.root, now=NOW)
        self.assertEqual((result.trimmed, result.removed), (1, 1))
        self.assertEqual(recent.read_text(), "orphan output\n")
        self.assertEqual(recent.stat().st_mtime_ns, old_mtime)
        self.assertFalse(old.exists())

    def test_late_native_crash_uses_file_time_instead_of_old_process_start(self):
        self.legacy("native_crash.log", "[2026-08-01 00:00:00] process_start pid=123\n"
                    "Windows fatal exception: access violation\nCurrent thread traceback\n")
        result = logs.query_logs(self.root, now=NOW)
        self.assertEqual(result.total, 1)
        self.assertTrue(result.records[0].inferred_time)
        self.assertIn("Current thread traceback", result.records[0].text)
        logs.cleanup_logs(self.root, now=NOW)
        self.assertIn("access violation", self.root.joinpath("native_crash.log").read_text())

    def test_live_legacy_writer_is_deferred_without_blocking_daily_cleanup(self):
        legacy = self.legacy("daemon.log", "[2026-08-01 00:00:00] keep while running\n")
        expired = Path(logs.append_log("daemon", "expired", self.root, now=NOW - timedelta(days=31)))
        lock = ProcessSingletonLock(build_process_singleton_lock_path(str(self.root), "daemon-23333"))
        self.assertTrue(lock.acquire())
        try:
            result = logs.cleanup_logs(self.root, now=NOW)
            self.assertTrue(legacy.exists())
            self.assertFalse(expired.exists())
            self.assertIn("后台进程仍在运行", "\n".join(result.issues))
        finally:
            lock.release()
        self.assertEqual(logs.cleanup_logs(self.root, now=NOW).removed, 1)
        self.assertFalse(legacy.exists())

    @unittest.skipUnless(os.name == "nt", "Windows inherited handle sharing")
    def test_occupied_raw_log_is_not_deleted_and_is_retried(self):
        path = logs.log_path("browser_service", self.root, now=NOW - timedelta(days=40))
        path.parent.mkdir(parents=True)
        path.write_text("old output")
        stamp = (NOW - timedelta(days=40)).timestamp()
        os.utime(path, (stamp, stamp))
        with path.open("rb"):
            result = logs.cleanup_logs(self.root, now=NOW)
            self.assertTrue(path.exists())
            self.assertTrue(result.issues)
        self.assertEqual(logs.cleanup_logs(self.root, now=NOW).removed, 1)

    def test_changed_legacy_snapshot_is_never_replaced(self):
        path = self.legacy("daemon.log", "[2026-08-01 00:00:00] old\n[2026-10-16 10:00:00] keep\n")
        before = path.read_bytes()
        with patch.object(logs, "_same_snapshot", return_value=False):
            result = logs.cleanup_logs(self.root, now=NOW)
        self.assertTrue(result.issues)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".log-retention-*.tmp")))

    def test_cancelled_legacy_trim_keeps_source_and_removes_temp(self):
        path = self.legacy("daemon.log", "[2026-08-01 00:00:00] old\n[2026-10-16 10:00:00] keep\n")
        before = path.read_bytes()
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 3
        with self.assertRaises(InterruptedError):
            logs.cleanup_logs(self.root, now=NOW, cancelled=cancelled)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".log-retention-*.tmp")))

    def test_search_reads_full_selected_range_not_only_latest_page(self):
        content = "".join(f"[2026-10-16 10:00:00] event={i} {'needle' if i == 0 else ''}\n" for i in range(2100))
        self.legacy("daemon.log", content)
        self.legacy("ui_error.log", "[2026-10-15 10:00:00] yesterday\n[2026-10-01 10:00:00] older\n")
        self.assertEqual(logs.query_logs(self.root, now=NOW).total, 2100)
        self.assertEqual(logs.query_logs(self.root, days=7, now=NOW).total, 2101)
        self.assertEqual(logs.query_logs(self.root, days=30, now=NOW).total, 2102)
        page = logs.query_logs(self.root, source="daemon", now=NOW)
        self.assertEqual(len(page.records), 1000)
        self.assertNotIn("needle", "".join(record.text for record in page.records))
        search = logs.query_logs(self.root, keyword="NEEDLE", now=NOW)
        self.assertEqual(search.total, 1)
        self.assertIn("event=0", search.records[0].text)

    def test_snapshot_excludes_later_append_and_reports_truncation(self):
        path = self.legacy("daemon.log", "[2026-10-16 09:00:00] snapshot\n")
        files, _ = logs.collect_files(self.root)
        with path.open("a") as handle:
            handle.write("[2026-10-16 10:00:00] later\n")
        self.assertEqual(len(list(logs.iter_records(files[0]))), 1)
        path.write_text("cut")
        with self.assertRaisesRegex(OSError, "截短"):
            list(logs.iter_records(files[0]))

    def test_log_failure_is_nonfatal_and_diagnostic_is_visible(self):
        self.root.joinpath("logs").write_text("not a directory")
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(logs.append_log("daemon", "event", self.root), "")
        self.assertIn("日志写入失败", error.getvalue())
        self.assertTrue(logs.maintenance_issues(self.root))

    def test_gateway_error_reads_both_new_and_legacy_raw_logs(self):
        now = logs.local_now()
        self.legacy("im_gateway.log", f"[{now.isoformat()}] gateway failed old error\n")
        logs.append_log("im_gateway", "gateway failed new error", self.root, now=now + timedelta(seconds=1))
        self.assertEqual(logs.latest_gateway_error(self.root), "gateway failed new error")
        raw = logs.log_path("im_gateway_stderr", self.root)
        raw.write_text(f"[{(now + timedelta(seconds=2)).isoformat()}] gateway error=raw failure\n")
        self.assertEqual(logs.latest_gateway_error(self.root), "gateway error=raw failure")

    def test_daily_maintenance_runs_without_page_or_process_restart(self):
        service = logs.LogMaintenance(self.root)
        with patch.object(logs, "cleanup_logs", return_value=logs.MaintenanceResult()) as cleanup:
            service.tick(NOW)
            service.tick(NOW + timedelta(hours=1))
            service.tick(NOW + timedelta(days=1))
        self.assertEqual(cleanup.call_count, 2)

    def test_logger_capture_keeps_levels_and_redacts_display_only(self):
        record = logging.LogRecord("core.test", logging.ERROR, __file__, 1,
                                   'failed api_key="secret-value"', (), None)
        handler = logs._ApplicationLogHandler(self.root)
        handler.emit(record)
        result = logs.query_logs(self.root)
        self.assertIn("secret-value", result.records[0].text)
        self.assertNotIn("secret-value", result.records[0].display_text())

    def test_symlink_logs_are_not_read_or_cleaned(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private.log"
            target.write_text("secret")
            link = self.root / "daemon.log"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            result = logs.cleanup_logs(self.root, now=NOW)
            self.assertTrue(result.issues)
            self.assertEqual(target.read_text(), "secret")
            self.assertEqual(logs.query_logs(self.root, now=NOW).total, 0)

    def test_external_browser_inventory_is_explicit_and_isolated(self):
        local = self.root / "local"
        browser = local / "DeepSeekCowork" / "browser-automation" / "runtime"
        browser.mkdir(parents=True)
        owned = browser / "service-start-0123456789abcdef.log"
        owned.write_text("raw browser output")
        (browser / "other.log").write_text("unrelated")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), \
             patch("core.env_utils.get_app_data_dir", return_value=str(self.root)):
            files, issues = logs.collect_files(self.root)
            self.assertFalse(issues)
            self.assertEqual([file.path for file in files], [owned])
            self.assertEqual(logs.collect_files(self.root / "isolated")[0], [])


if __name__ == "__main__":
    unittest.main()
