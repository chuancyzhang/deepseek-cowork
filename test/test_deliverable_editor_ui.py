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
            with patch("main.get_resource_dir", return_value=directory):
                with self.assertRaises(DeliverableEditError) as raised:
                    self.window._editor_asset_path("docx")

        self.assertEqual(raised.exception.code, "editor_assets_missing")

    def test_python_preview_edit_focus_and_layout_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "script.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("print('ready')\n")
            self.window.context_drawer_expanded = False
            self.window.context_drawer_user_width = 420
            self.window.context_drawer_width_before_expand = 0
            self.window.file_navigator_visible = True
            self.window.file_navigator_pinned = True
            self.window.file_workbench.set_navigator_state(visible=True, pinned=True)

            self.window.select_deliverable(source, render_html=True)
            self.assertIn("print('ready')", self.window.deliverable_text_preview.toPlainText())
            self.assertFalse(self.window.deliverable_mode_btn.isHidden())

            self.window.begin_deliverable_edit()
            self._wait_until(lambda: self.window.deliverable_edit_state == "ready")

            self.assertTrue(self.window.file_workbench.editor_focus)
            self.assertTrue(self.window.context_drawer_expanded)
            self.assertFalse(self.window.file_workbench.navigator.isHidden())
            self.assertTrue(self.window.file_navigator_visible)
            self.assertTrue(self.window.file_navigator_pinned)
            with patch.object(self.window.config_manager, "set") as persist_mock:
                self.window.persist_context_drawer_width()
            persist_mock.assert_not_called()

            with patch.object(self.window, "add_system_toast"), patch.object(
                self.window, "_show_deliverable_conflict_options"
            ), patch("main.QMessageBox.critical"):
                self.window._handle_deliverable_save_finished(
                    {
                        "ok": False,
                        "code": "disk_error",
                        "message": "保存失败",
                    }
                )
            self.assertEqual(self.window.deliverable_edit_state, "failed")
            self.assertTrue(self.window.file_workbench.editor_focus)

            self.window._release_deliverable_edit_session(show_preview=False)

            self.assertFalse(self.window.file_workbench.editor_focus)
            self.assertFalse(self.window.context_drawer_expanded)
            self.assertEqual(self.window.context_drawer_user_width, 420)
            self.assertTrue(self.window.file_navigator_visible)
            self.assertTrue(self.window.file_navigator_pinned)

    def test_text_selection_and_close_do_not_leave_previous_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "data.json")
            unsupported = os.path.join(directory, "archive.unknown")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write('{"ready": true}')
            with open(unsupported, "wb") as handle:
                handle.write(b"unsupported")
            self.window.preview_stack.setCurrentWidget(self.window.preview_image)

            self.window.select_deliverable(source, render_html=True)

            self.assertIs(
                self.window.preview_stack.currentWidget(),
                self.window.deliverable_text_preview,
            )
            self.assertIn('"ready": true', self.window.deliverable_text_preview.toPlainText())

            self.window.preview_stack.setCurrentWidget(self.window.preview_image)
            self.window.select_deliverable(unsupported, render_html=True)
            self.assertIs(
                self.window.preview_stack.currentWidget(),
                self.window.deliverable_text_preview,
            )
            self.assertIn(
                "当前格式暂不支持内嵌渲染",
                self.window.deliverable_text_preview.toPlainText(),
            )
            self.assertNotIn(
                '"ready": true', self.window.deliverable_text_preview.toPlainText()
            )

            self.assertTrue(self.window.close_current_file())
            self.assertEqual(self.window.current_preview_path, source)
            self.assertIn('"ready": true', self.window.deliverable_text_preview.toPlainText())

            self.assertTrue(self.window.close_current_file())
            self.assertIs(self.window.preview_stack.currentWidget(), self.window.preview_text)
            self.assertEqual(self.window.preview_text.toPlainText(), "")
            self.assertEqual(self.window.current_preview_path, "")

    def test_late_html_load_cannot_replace_selected_text_preview(self):
        class _LoadedUrl:
            def __init__(self, path):
                self.path = path

            def toLocalFile(self):
                return self.path

        class _LateHtmlView:
            def __init__(self, path):
                self.path = path

            def url(self):
                return _LoadedUrl(self.path)

        with tempfile.TemporaryDirectory() as directory:
            html_path = os.path.join(directory, "old.html")
            json_path = os.path.join(directory, "current.json")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<p>old</p>")
            with open(json_path, "w", encoding="utf-8") as handle:
                handle.write('{"current": true}')

            self.window.select_deliverable(json_path, render_html=True)
            expected_status = self.window.deliverable_status_label.text()
            previous_view = self.window.deliverable_web_view
            self.window.deliverable_web_view = _LateHtmlView(html_path)
            try:
                self.window.deliverable_render_loading = True
                self.window.handle_deliverable_render_finished(True)
            finally:
                self.window.deliverable_web_view = previous_view

            self.assertIs(
                self.window.preview_stack.currentWidget(),
                self.window.deliverable_text_preview,
            )
            self.assertIn('"current": true', self.window.deliverable_text_preview.toPlainText())
            self.assertEqual(self.window.deliverable_status_label.text(), expected_status)

    def test_web_editor_zoom_is_reset_per_mode(self):
        self.window.deliverable_edit_session = type(
            "_Session",
            (),
            {
                "path": r"D:\workspace\data.xlsx",
                "metadata": {"ui_session_id": "session"},
                "descriptor": type("_Descriptor", (), {"label": "XLSX"})(),
            },
        )()
        self.window.deliverable_editor_bridge = type(
            "_Bridge", (), {"reset": lambda _self, _session_id="": None}
        )()
        page = type("_Page", (), {"runJavaScript": lambda _self, _script: None})()
        view = type(
            "_View",
            (),
            {
                "factors": [],
                "setZoomFactor": lambda self, value: self.factors.append(value),
                "setUrl": lambda _self, _url: None,
                "page": lambda _self: page,
            },
        )()
        self.window.deliverable_editor_web_view = view
        self.window.deliverable_editor_ready_mode = ""
        with patch.object(self.window, "_editor_asset_path", return_value=__file__):
            self.window._load_deliverable_web_editor({"kind": "sheet"})
            self.window._load_deliverable_web_editor({"kind": "html"})

        self.assertEqual(view.factors, [1.10, 1.00])

    def test_web_editor_theme_includes_readability_tokens(self):
        scripts = []
        page = type(
            "_Page",
            (),
            {"runJavaScript": lambda _self, script: scripts.append(script)},
        )()
        view = type("_View", (), {"page": lambda _self: page})()
        self.window.deliverable_editor_web_view = view

        self.window.refresh_deliverable_editor_theme()

        self.assertEqual(len(scripts), 1)
        self.assertIn('"font-size": "14px"', scripts[0])
        self.assertIn('"control-height": "32px"', scripts[0])
        self.assertIn('"toolbar-height": "44px"', scripts[0])

    def test_docx_editor_uses_compact_mode_button(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "report.docx")
            with open(source, "wb") as handle:
                handle.write(b"placeholder")
            self.window.current_deliverable_path = source

            self.window._set_deliverable_controls_enabled(source)

            self.assertFalse(self.window.deliverable_mode_btn.isHidden())
            self.assertTrue(self.window.deliverable_mode_btn.isEnabled())
            self.assertTrue(self.window.deliverable_read_only_label.isHidden())


if __name__ == "__main__":
    unittest.main()
