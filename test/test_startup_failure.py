"""Startup failures must leave the loading state and offer visible diagnostics."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import main


class StartupFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="startup-failure-"))
        self.enterContext(patch("main.get_app_data_dir", return_value=self.temp))
        self.logs = self.enterContext(patch("main.append_background_process_log"))
        self.app = Mock(main_window=None)
        self.startup = main.show_startup_loading_window(self.qt_app)
        self.dialogs = []
        self.enterContext(patch.object(main.ProductMessageDialog, "exec", self.capture_dialog))

    def capture_dialog(self):
        # Find the real dialog constructed by the startup path, without opening
        # a nested event loop or dismissing a user's application window.
        dialog = next(widget for widget in self.qt_app.topLevelWidgets()
                      if isinstance(widget, main.ProductMessageDialog) and widget.windowTitle() == "启动失败")
        self.assertFalse(self.startup.isVisible())
        self.assertFalse(self.startup.indicator._running)
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.dialogs.append((text, dialog.findChild(QPushButton).text()))
        dialog.deleteLater()
        return main.QMessageBox.Close

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def run_startup(self, factory):
        server = Mock()
        # Dispatch the startup callback inside the fault-injection scope; a
        # queued callback must not outlive the mocked constructor on failure.
        with patch("main.MainWindow", factory), patch.object(
            main.QTimer, "singleShot", side_effect=lambda _delay, callback: callback(),
        ):
            holder = main.schedule_main_window_startup(
                self.app, self.startup, server, True, {"requested": True},
            )
        return holder, server

    def test_missing_render_dependency_exits_loading_and_reports_error(self):
        factory = Mock(side_effect=ImportError("could not import module 'PySide6.QtQuick'"))
        holder, _ = self.run_startup(factory)
        self.assertIsNone(holder["window"])
        self.assertIsNone(self.app.main_window)
        self.app.exit.assert_called_once_with(1)
        self.assertEqual(len(self.dialogs), 1)
        self.assertIn("未能启动", self.dialogs[0][0])
        self.assertIn("PySide6.QtQuick", self.dialogs[0][0])
        self.assertIn("诊断日志", self.dialogs[0][0])
        self.assertEqual(self.dialogs[0][1], "关闭应用")
        self.assertTrue(any("stage=main_window_startup_failed" in str(call) for call in self.logs.call_args_list))

    def test_show_failure_hides_partial_window_without_retry(self):
        window = Mock()
        window.showMaximized.side_effect = RuntimeError("show failed")
        factory = Mock(return_value=window)
        holder, _ = self.run_startup(factory)
        factory.assert_called_once()
        window.hide.assert_called_once()
        window.activate_existing_window.assert_not_called()
        self.assertIsNone(holder["window"])
        self.app.exit.assert_called_once_with(1)

    def test_success_closes_loading_and_activates_main_window(self):
        window = Mock()
        holder, server = self.run_startup(Mock(return_value=window))
        self.assertIs(holder["window"], window)
        self.assertIs(self.app.main_window, window)
        window.attach_single_instance_server.assert_called_once_with(server)
        window.showMaximized.assert_called_once()
        window.activate_existing_window.assert_called_once()
        self.assertFalse(self.startup.isVisible())
        self.app.exit.assert_not_called()
        self.assertFalse(self.dialogs)


if __name__ == "__main__":
    unittest.main()
