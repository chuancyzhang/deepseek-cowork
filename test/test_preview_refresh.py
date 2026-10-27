import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStackedWidget, QTextEdit

from core.preview_versions import read_preview_version
from ui.file_refresh import refresh_file, schedule_file_refresh
from ui.preview_refresh import controller


class PreviewVersionTest(unittest.TestCase):
    def test_content_version_ignores_timestamp_but_detects_same_size_same_timestamp_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            path.write_bytes(b"first")
            first = read_preview_version(path)
            os.utime(path, (100, 100))
            self.assertEqual(first, read_preview_version(path))
            path.write_bytes(b"other")
            os.utime(path, (100, 100))
            self.assertNotEqual(first, read_preview_version(path))

    def test_html_resources_include_missing_files_and_ignore_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            css = Path(directory) / "style.css"
            path.write_text('<link rel="stylesheet" href="style.css"><script src="code.js"></script>', encoding="utf-8")
            first = read_preview_version(path)
            self.assertEqual(len(first.resources), 2)
            (Path(directory) / "unrelated.css").write_text("body{}")
            self.assertEqual(first, read_preview_version(path))
            css.write_text("body{color:red}")
            second = read_preview_version(path)
            self.assertEqual(first.source, second.source)
            self.assertNotEqual(first, second)
            css.unlink()
            self.assertEqual(first, read_preview_version(path))


class PreviewHost(QMainWindow):
    def __init__(self, path):
        super().__init__()
        self.current_deliverable_path = str(path)
        self.current_session_id = "first"
        self.deliverable_edit_state = "idle"
        self.deliverable_edit_session = None
        self.current_deliverable_stale = False
        self.deliverable_render_fingerprint = None
        self.deliverable_render_loading = False
        self.preview_stack = QStackedWidget(self)
        self.deliverable_text_preview = QTextEdit(self.preview_stack)
        self.preview_stack.addWidget(self.deliverable_text_preview)
        self.session = SimpleNamespace(deliverable_preview_rendered=False)
        self.status = ""

    def _set_deliverable_status(self, text="", **kwargs):
        notice, _ = self.preview_refresh_controller.message()
        self.status = notice or text

    def _watch_deliverable_paths(self):
        pass

    def get_current_session(self):
        return self.session

    def _deliverable_fingerprint(self, path):
        try:
            stat = os.stat(path)
            return path, stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def render_selected_deliverable(self, force=False):
        controller(self).request_render(self.current_deliverable_path, force=force)


class PreviewRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "report.docx"
        self.path.write_bytes(b"first")
        self.host = PreviewHost(self.path)
        self.refresh = controller(self.host)
        self.renders = []
        self.complete = True
        self.render_patch = patch.object(self.refresh, "_load_document", side_effect=self.render)
        self.render_patch.start()
        self.host.render_selected_deliverable()
        self.wait_idle()
        self.assertEqual(len(self.renders), 1)

    def render(self, request):
        self.renders.append(request)
        if self.complete:
            preview = QLabel("rendered " + str(len(self.renders)), self.host.preview_stack)
            self.host.preview_stack.addWidget(preview)
            self.host.document_preview = preview
            self.refresh.document_key = request["key"]
            self.host.preview_stack.setCurrentWidget(preview)
            self.refresh._completed(request)

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), self.host.status)

    def wait_idle(self):
        self.until(lambda: self.host.preview_version_worker is None
                   and not self.refresh.pending and not self.refresh.timer.isActive()
                   and not getattr(self.host, "browser_file_refresh_timer", SimpleNamespace(isActive=lambda: False)).isActive())

    def changed(self):
        refresh_file(self.host)
        self.wait_idle()

    def tearDown(self):
        timer = getattr(self.host, "browser_file_refresh_timer", None)
        if timer:
            timer.stop()
        self.refresh.dispose()
        self.assertTrue(self.refresh.worker.wait(3000))
        self.render_patch.stop()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temporary.cleanup()

    def test_unrelated_events_and_duplicate_directory_events_do_not_render(self):
        old = self.host.preview_stack.currentWidget()
        schedule_file_refresh(self.host, str(self.path.with_name("other.txt")))
        self.assertFalse(hasattr(self.host, "browser_file_refresh_timer"))
        for _ in range(100):
            schedule_file_refresh(self.host, str(self.path.parent))
        self.wait_idle()
        self.assertEqual(len(self.renders), 1)
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertEqual(self.host.status, "")

    def test_actual_change_only_notifies_until_manual_refresh(self):
        old = self.host.preview_stack.currentWidget()
        replacement = self.path.with_suffix(".tmp")
        replacement.write_bytes(b"second")
        os.replace(replacement, self.path)
        self.changed()
        self.assertEqual(self.host.status, "文件已更新")
        self.host._set_deliverable_status("")
        self.host.render_selected_deliverable()
        self.wait_idle()
        self.assertEqual(self.host.status, "文件已更新")
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertEqual(len(self.renders), 1)
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        self.assertEqual(len(self.renders), 2)
        self.assertEqual(self.host.status, "")

    def test_metadata_only_update_and_manual_refresh_reuse_document(self):
        os.utime(self.path, (100, 100))
        self.changed()
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        self.assertEqual(len(self.renders), 1)
        self.assertEqual(self.host.status, "")

    def test_deleted_file_preserves_view_and_can_be_restored(self):
        old = self.host.preview_stack.currentWidget()
        self.path.unlink()
        self.changed()
        self.assertIn("源文件已不可用", self.host.status)
        self.host.render_selected_deliverable()
        self.wait_idle()
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertEqual(len(self.renders), 1)
        self.path.write_bytes(b"restored")
        self.changed()
        self.assertEqual(self.host.status, "文件已更新")
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        self.assertEqual(len(self.renders), 2)

    def test_change_during_refresh_survives_completion_and_repeated_refresh_click(self):
        self.complete = False
        self.path.write_bytes(b"second")
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        attempt = self.renders[-1]
        self.path.write_bytes(b"third")
        self.changed()
        self.host.render_selected_deliverable(force=True)
        self.assertEqual(len(self.renders), 2)
        self.refresh._completed(attempt)
        self.wait_idle()
        self.assertEqual(self.host.status, "文件已更新")
        self.assertEqual(self.refresh._state().loaded, attempt["version"])

    def test_refresh_failure_keeps_previous_view_and_retry_is_explicit(self):
        old = self.host.preview_stack.currentWidget()
        self.complete = False
        self.path.write_bytes(b"second")
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        self.refresh._failed(self.renders[-1], "Office 暂时不可用")
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertIn("当前预览已保留", self.host.status)
        self.host.render_selected_deliverable()
        self.wait_idle()
        self.assertEqual(len(self.renders), 2)
        self.complete = True
        self.host.render_selected_deliverable(force=True)
        self.wait_idle()
        self.assertTrue(self.renders[-1]["rebuild"])
        self.assertEqual(self.host.status, "")

    def test_renderer_start_exception_exposes_retry_without_losing_old_view(self):
        old = self.host.preview_stack.currentWidget()
        self.path.write_bytes(b"second")
        with patch.object(self.refresh, "_load_document", side_effect=RuntimeError("reader unavailable")):
            self.host.render_selected_deliverable(force=True)
            self.wait_idle()
        self.assertFalse(self.host.deliverable_render_loading)
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertIn("无法启动预览", self.host.status)
        self.assertTrue(self.refresh.message()[1])

    def test_old_check_cannot_cross_session_switch_and_return(self):
        entered, release = threading.Event(), threading.Event()
        original = read_preview_version

        def blocked(path):
            entered.set()
            release.wait(3)
            return original(path)

        self.path.write_bytes(b"second")
        with patch("ui.preview_refresh.read_preview_version", side_effect=blocked):
            self.refresh.check(str(self.path))
            self.until(entered.is_set)
            self.host.current_session_id = "other"
            self.refresh.navigate("other", str(self.path))
            self.assertEqual(self.refresh.message(), ("", False))
            self.host.current_session_id = "first"
            self.refresh.navigate("first", str(self.path))
            release.set()
            self.wait_idle()
        self.assertEqual(self.refresh._state().loaded, self.refresh._state().observed)
        self.changed()
        self.assertEqual(self.host.status, "文件已更新")

    def test_shutdown_keeps_worker_owned_until_queued_completion(self):
        from ui.worker_lifecycle import pending_ui_workers
        entered, release = threading.Event(), threading.Event()
        original = read_preview_version

        def blocked(path):
            entered.set()
            release.wait(3)
            return original(path)

        with patch("ui.preview_refresh.read_preview_version", side_effect=blocked):
            self.refresh.check(str(self.path))
            self.until(entered.is_set)
            self.host._checkpoint_shutdown_pending = True
            self.refresh.dispose()
            release.set()
            self.assertTrue(self.refresh.worker.wait(3000))
            self.assertIn(self.refresh.worker, pending_ui_workers(self.host))
            self.app.processEvents()
        self.assertNotIn(self.refresh.worker, pending_ui_workers(self.host))
        self.assertEqual(len(self.renders), 1)

    def test_cancel_during_version_read_never_starts_late_render(self):
        entered, release = threading.Event(), threading.Event()
        original = read_preview_version
        old = self.host.preview_stack.currentWidget()

        def blocked(path):
            entered.set()
            release.wait(3)
            return original(path)

        self.path.write_bytes(b"second")
        with patch("ui.preview_refresh.read_preview_version", side_effect=blocked):
            self.host.render_selected_deliverable(force=True)
            self.until(entered.is_set)
            self.refresh.cancel()
            release.set()
            self.wait_idle()
        self.assertEqual(len(self.renders), 1)
        self.assertIs(self.host.preview_stack.currentWidget(), old)
        self.assertIn("已取消", self.host.status)
        self.assertFalse(self.host.deliverable_render_loading)


if __name__ == "__main__":
    unittest.main()
