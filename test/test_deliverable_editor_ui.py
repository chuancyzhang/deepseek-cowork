import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.deliverable_editing import backup_paths
from core.deliverable_editing import DeliverableEditError
from main import MainWindow


class DeliverableEditorUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        if getattr(self.window, "deliverable_edit_state", "idle") != "idle":
            self.window._release_deliverable_edit_session(show_preview=False)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout_ms=10000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(20)
        self.fail("condition was not reached before timeout")

    def test_text_editor_saves_atomically_and_keeps_one_previous_version(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "notes.txt")
            with open(source, "w", encoding="utf-8", newline="") as handle:
                handle.write("第一版\r\n")
            with patch(
                "core.deliverable_editing.get_app_data_dir",
                return_value=directory,
            ):
                self.window._apply_workspace_to_ui(
                    directory,
                    refresh_sidebar=False,
                    remember_workspace=False,
                    persist_default=False,
                )
                self.window.show_file_workspace_detail_view()
                self.window.select_deliverable(source, render_html=False)
                self.window.begin_deliverable_edit()
                self._wait_until(
                    lambda: self.window.deliverable_edit_state == "ready"
                )

                self.assertIs(
                    self.window.preview_stack.currentWidget(),
                    self.window.deliverable_text_editor_container,
                )
                self.window.deliverable_text_editor.setPlainText("第二版\r\n")
                self.assertTrue(self.window.deliverable_edit_dirty)
                self.window.save_deliverable_edit()
                self._wait_until(
                    lambda: (
                        self.window.deliverable_save_worker is None
                        and self.window.deliverable_edit_state == "ready"
                        and not self.window.deliverable_edit_dirty
                    )
                )

                with open(source, "r", encoding="utf-8", newline="") as handle:
                    self.assertEqual(handle.read(), "第二版\r\n")
                actual_previous = backup_paths(
                    source,
                    os.path.join(directory, "deliverable_backups"),
                )[0]
                with open(actual_previous, "r", encoding="utf-8", newline="") as handle:
                    self.assertEqual(handle.read(), "第一版\r\n")

    def test_missing_offline_editor_assets_are_reported_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("main.get_base_dir", return_value=directory):
                with self.assertRaises(DeliverableEditError) as raised:
                    self.window._editor_asset_path("docx")

        self.assertEqual(raised.exception.code, "editor_assets_missing")


if __name__ == "__main__":
    unittest.main()
