"""Real Qt workers: complete exports, responsive filters, and safe page disposal."""
import os
import re
from pathlib import Path
import tempfile
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core import runtime_logs as logs
from ui import log_export as ui


class RuntimeLogUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.panels = []

    def tearDown(self):
        for panel in self.panels:
            if not isValid(panel):
                continue
            panel._pending_query = None
            for worker in (panel.worker, panel._read_worker):
                if worker is not None and isValid(worker):
                    worker.shutdown()
            panel.close()
            panel.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def until(self, condition, timeout=5):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def panel(self):
        panel = ui.LogExportPanel(str(self.root), MagicMock())
        self.panels.append(panel)
        return panel

    def export(self, target=None, **kwargs):
        worker = ui.LogExportWorker(str(self.root), str(target or self.root / "export.txt"), MagicMock(), None, **kwargs)
        worker.run()
        return worker

    def test_full_export_exceeds_two_mib_and_includes_all_owned_sources(self):
        now = logs.local_now()
        logs.append_log("daemon", "FIRST_MARKER " + "完整内容" * 240000 + " LAST_MARKER", self.root)
        logs.append_log("browser_skill_runtime", "BROWSER_READY", self.root)
        logs.append_log("wecom_capability", "WECOM_READY", self.root)
        logs.append_log("ui_error", 'failure api_key="credential-secret"\ntraceback detail', self.root)
        transaction = self.root / "updates" / "transactions" / "t"
        (transaction / "backup").mkdir(parents=True)
        (transaction / "update.log").write_text(f"[{now.isoformat()}] INSTALLER_READY\n")
        (transaction / "backup" / "secret.log").write_text("DO_NOT_EXPORT_BACKUP")
        (self.root / "config.json").write_text("DO_NOT_EXPORT_CONFIG")
        worker = self.export()
        self.assertFalse(worker.error)
        self.assertFalse(worker.warnings)
        text = Path(worker.target).read_text(encoding="utf-8")
        self.assertGreater(Path(worker.target).stat().st_size, 2 * 1024 * 1024)
        for value in ("FIRST_MARKER", "LAST_MARKER", "BROWSER_READY", "WECOM_READY", "INSTALLER_READY", "traceback detail"):
            self.assertIn(value, text)
        for value in ("credential-secret", "DO_NOT_EXPORT_BACKUP", "DO_NOT_EXPORT_CONFIG"):
            self.assertNotIn(value, text)
        self.assertEqual(worker.record_count, 5)

    def test_export_range_and_source_use_same_reader_as_preview(self):
        now = logs.local_now()
        for days, name in ((0, "TODAY"), (6, "WEEK"), (29, "MONTH"), (30, "EXPIRED")):
            logs.append_log("daemon", name, self.root, now=now - timedelta(days=days))
        logs.append_log("ui_error", "OTHER_SOURCE", self.root)
        for days, count in ((1, 1), (7, 2), (30, 3)):
            worker = self.export(days=days, source="daemon")
            self.assertFalse(worker.error)
            self.assertEqual(worker.record_count, count)
            text = Path(worker.target).read_text(encoding="utf-8")
            self.assertNotIn("OTHER_SOURCE", text)
            self.assertNotIn("EXPIRED", text)

    def test_source_disappearing_yields_explicit_partial_export(self):
        logs.append_log("daemon", "preserved", self.root)
        logs.append_log("ui_error", "unavailable", self.root)
        original = ui.iter_records
        def read(file, cancelled):
            if file.source == "ui_error":
                raise OSError("file unavailable")
            yield from original(file, cancelled)
        with patch.object(ui, "iter_records", side_effect=read):
            worker = self.export()
        self.assertFalse(worker.error)
        self.assertEqual(len(worker.warnings), 1)
        text = Path(worker.target).read_text(encoding="utf-8")
        self.assertIn("导出不完整", text)
        self.assertIn("file unavailable", text)
        self.assertIn("preserved", text)
        panel = self.panel()
        panel.worker = worker
        panel.export_finished()
        self.assertIn("部分日志", panel.notice.label.text())
        self.assertNotIn("已导出", panel.notice.label.text())

    def test_invalid_utf8_is_reported_as_partial_without_changing_source(self):
        source = self.root / "daemon.log"
        original = f"[{logs.local_now().isoformat()}] damaged ".encode() + b"\xff\n"
        source.write_bytes(original)
        worker = self.export()
        self.assertFalse(worker.error)
        self.assertTrue(worker.warnings)
        self.assertIn("无法解码", Path(worker.target).read_text(encoding="utf-8"))
        self.assertEqual(source.read_bytes(), original)

    def test_cancel_write_error_and_overwrite_protection_preserve_existing_files(self):
        source = Path(logs.append_log("daemon", "original log", self.root))
        before = source.read_bytes()
        worker = self.export(source)
        self.assertTrue(worker.error)
        self.assertEqual(source.read_bytes(), before)
        # A hard link to a source outside logs/ must also be rejected.
        alias = self.root / "alias.txt"
        os.link(source, alias)
        self.assertTrue(self.export(alias).error)
        self.assertEqual(source.read_bytes(), before)
        target = self.root / "export.txt"
        target.write_text("existing export")
        worker = ui.LogExportWorker(str(self.root), str(target), MagicMock(), None)
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        self.assertIn("取消", worker.error)
        self.assertEqual(target.read_text(), "existing export")
        failed = self.export(self.root / "missing" / "export.txt")
        self.assertTrue(failed.error)
        self.assertEqual(source.read_bytes(), before)

    def test_destination_write_failure_cancels_atomic_save_not_partial_success(self):
        logs.append_log("daemon", "original", self.root)
        target = self.root / "export.txt"
        target.write_text("existing")
        with patch.object(ui, "QSaveFile") as save:
            output = save.return_value
            output.open.return_value = True
            output.write.return_value = -1
            output.errorString.return_value = "disk full"
            worker = self.export(target)
        self.assertEqual(worker.error, "disk full")
        output.cancelWriting.assert_called_once()
        output.commit.assert_not_called()
        self.assertEqual(target.read_text(), "existing")

    def test_empty_range_is_explicit_and_does_not_replace_an_existing_export(self):
        target = self.root / "export.txt"
        target.write_text("keep")
        worker = self.export(target)
        self.assertIn("暂无日志", worker.error)
        self.assertEqual(target.read_text(), "keep")

    def test_preview_pagination_and_search_do_not_limit_export(self):
        file = self.root / "daemon.log"
        now = logs.local_now().isoformat()
        file.write_text("".join(f"[{now}] event={n} {'needle' if n == 0 else ''}\n" for n in range(1005)), encoding="utf-8")
        panel = self.panel()
        panel.refresh_logs()
        self.until(lambda: panel._read_worker is None)
        self.assertTrue(panel.more_button.isEnabled())
        self.assertNotIn("needle", panel.viewer.toPlainText())
        panel.load_more()
        self.until(lambda: panel._read_worker is None)
        self.assertIn("needle", panel.viewer.toPlainText())
        self.assertIn("已显示更早 5 条", panel.summary.text())
        self.assertFalse(panel.more_button.isEnabled())
        panel.keyword.setText("needle")
        panel.refresh_logs()
        self.until(lambda: panel._read_worker is None)
        self.assertIn("共匹配 1 条", panel.summary.text())
        with patch.object(ui, "QFileDialog") as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.selectedFiles.return_value = [str(self.root / "from-panel.txt")]
            panel.export_logs()
        self.until(lambda: panel.worker is None)
        self.assertIn("1005 条记录", panel.notice.label.text())

    def test_preview_size_limit_still_allows_every_older_record_without_duplicates(self):
        now = logs.local_now().isoformat()
        (self.root / "daemon.log").write_text(
            "".join(f"[{now}] event={n} " + "x" * 20000 + "\n" for n in range(80)), encoding="utf-8")
        panel = self.panel()
        panel.refresh_logs()
        seen = []
        for _page in range(10):
            self.until(lambda: panel._read_worker is None)
            current = [int(value) for value in re.findall(r"event=(\d+) ", panel.viewer.toPlainText())]
            self.assertTrue(current)
            self.assertFalse(set(current).intersection(seen))
            seen.extend(current)
            self.assertIn(f" {len(current)} 条", panel.summary.text())
            if not panel.more_button.isEnabled():
                break
            panel.load_more()
        self.assertEqual(sorted(seen), list(range(80)))
        panel.refresh_logs()
        self.until(lambda: panel._read_worker is None)
        self.assertIn("event=79 ", panel.viewer.toPlainText())

    def test_stale_completion_never_overwrites_latest_filter_and_ui_stays_responsive(self):
        logs.append_log("daemon", "DAEMON_RESULT", self.root)
        logs.append_log("ui_error", "ERROR_RESULT", self.root)
        started, release = threading.Event(), threading.Event()
        original = ui.query_logs
        def read(root, **options):
            if options["source"] == "":
                started.set()
                release.wait(3)
                options["cancelled"] = lambda: False  # Simulate a completion already in flight.
            return original(root, **options)
        panel = self.panel()
        with patch.object(ui, "query_logs", side_effect=read):
            panel.refresh_logs()
            self.until(started.is_set)
            panel.set_source("ui_error")
            self.assertEqual(panel.source_combo.currentData(), "ui_error")
            self.assertEqual(panel.viewer.toPlainText(), "")
            release.set()
            self.until(lambda: panel._read_worker is None and panel._pending_query is None)
        self.assertIn("ERROR_RESULT", panel.viewer.toPlainText())
        self.assertNotIn("DAEMON_RESULT", panel.viewer.toPlainText())

    def test_disposed_page_does_not_destroy_running_worker(self):
        started, release = threading.Event(), threading.Event()
        def read(*args, **kwargs):
            started.set()
            release.wait(3)
            return logs.QueryResult()
        panel = self.panel()
        with patch.object(ui, "query_logs", side_effect=read):
            panel.refresh_logs()
            self.until(started.is_set)
            worker = panel._read_worker
            self.assertIs(worker.parent(), self.app)
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertFalse(isValid(panel))
            self.assertTrue(worker.isRunning())
            release.set()
            self.assertTrue(worker.wait(3000))
            self.app.processEvents()

    def test_read_failure_and_long_preview_have_clear_feedback(self):
        panel = self.panel()
        with patch.object(ui, "query_logs", side_effect=OSError("permission denied")):
            panel.refresh_logs()
            self.until(lambda: panel._read_worker is None)
        self.assertIn("读取失败", panel.summary.text())
        self.assertIn("permission denied", panel.read_notice.label.text())
        logs.append_log("daemon", "x" * (ui.RECORD_PREVIEW_CHAR_LIMIT + 100), self.root)
        panel.refresh_logs()
        self.until(lambda: panel._read_worker is None)
        self.assertIn("预览已截短", panel.read_notice.label.text())
        self.assertIn("导出包含完整", panel.viewer.toPlainText())

    def test_shutdown_does_not_start_queued_read_or_export(self):
        panel = self.panel()
        previous = getattr(self.app, "main_window", None)
        self.app.main_window = SimpleNamespace(_checkpoint_shutdown_pending=True)
        try:
            panel.refresh_logs()
            with patch.object(ui, "QFileDialog") as dialog:
                panel.export_logs()
                dialog.assert_not_called()
            self.assertIsNone(panel._read_worker)
        finally:
            self.app.main_window = previous

    def test_native_crash_rollover_keeps_qt_handler_and_rebinds_descriptor(self):
        import main
        first = logs.log_path("native_crash", self.root)
        second = first.parent.parent / "2099-01-01" / first.name
        with patch.object(main, "_NATIVE_CRASH_LOG_HANDLE", None), patch.object(main, "_QT_MESSAGE_HANDLER", None), \
             patch.object(main, "runtime_log_path", side_effect=[first, second, second]), \
             patch.object(main, "get_app_data_dir", return_value=str(self.root)), \
             patch("main.faulthandler.enable") as enable, patch("PySide6.QtCore.qInstallMessageHandler") as qt:
            main.install_native_crash_logging()
            old = main._NATIVE_CRASH_LOG_HANDLE
            main.install_native_crash_logging()
            try:
                self.assertTrue(old.closed)
                self.assertEqual(main._NATIVE_CRASH_LOG_HANDLE.name, str(second))
                main.install_native_crash_logging()
                self.assertEqual(enable.call_count, 2)
                qt.assert_called_once()
            finally:
                main._NATIVE_CRASH_LOG_HANDLE.close()
        self.assertIn("process_continue", second.read_text(encoding="utf-8"))

    def test_settings_entry_is_read_only_and_gateway_link_uses_same_page(self):
        import main
        from core.config_manager import ConfigManager
        with patch("core.config_manager.get_app_data_dir", return_value=str(self.root)), \
             patch("core.config_manager.get_base_dir", return_value=str(self.root)), \
             patch("main.get_app_data_dir", return_value=str(self.root)):
            config = ConfigManager()
            settings = main.SettingsDialog(config, initial_page_label="运行日志")
            try:
                self.assertEqual(settings.nav_list.currentItem().text(), "运行日志")
                self.assertTrue(settings.settings_action_bar.isHidden())
                settings.runtime_logs_panel.keyword.setText("query")
                settings.runtime_logs_panel.range_combo.setCurrentIndex(1)
                self.assertFalse(settings._settings_dirty)
                settings.select_initial_page("消息连接")
                settings._open_im_gateway_log()
                self.assertEqual(settings.nav_list.currentItem().text(), "运行日志")
                self.assertEqual(settings.runtime_logs_panel.source_combo.currentData(), "im_gateway")
                self.until(lambda: settings.runtime_logs_panel._read_worker is None)
            finally:
                settings._allow_close_without_prompt = True
                settings.close()
                settings.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
