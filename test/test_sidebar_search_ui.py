import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from core.chat_storage import ChatStorage
from main import MainWindow


class SidebarSearchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow()
        self.window._startup_hydration_scheduled = True
        self.window._background_services_scheduled = True
        self.window.chat_storage = ChatStorage(str(Path(self.temp.name) / "history.sqlite"))
        with self.window.chat_storage._connect() as conn:
            for index in range(75):
                sid = f"search-{index:03}"
                conn.execute("INSERT INTO conversations(id,title,updated_at,meta) VALUES (?,?,?,?)",
                             (sid, f"历史任务 {index}", index, '{"workspace_source":"chat"}'))
                conn.execute("INSERT INTO messages(id,conversation_id,role,content,position,created_at) VALUES (?,?,?,?,?,?)",
                             ("m-" + sid, sid, "assistant", "截图归档已经完成", 0, index))
        self.projects = patch.object(self.window.config_manager, "get_projects", return_value=[])
        self.projects.start()
        self.window.resize(1000, 720)
        self.window.show()
        self.window.refresh_history_list()
        self.app.processEvents()

    def wait_for(self, condition, timeout=5):
        until = time.monotonic() + timeout
        while not condition() and time.monotonic() < until:
            self.app.processEvents()
            # QTest.qWait can monopolize the GIL between SQLite calls on
            # Windows. Yield like the application's normal Qt event loop.
            time.sleep(0.005)
        self.assertTrue(condition())

    def search(self, text):
        self.window.history_search_input.setFocus()
        self.window.history_search_input.setText(text)
        self.wait_for(lambda: not self.window.history_search_timer.isActive()
                      and self.window.history_search_controller.worker is None)

    def tearDown(self):
        self.projects.stop()
        finished = []
        self.window._shutdown_workers_async(lambda _preserved: finished.append(True))
        self.wait_for(lambda: bool(finished))
        self.window._checkpoint_close_ready = True
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_body_search_pages_results_and_clear_restores_browsing_state(self):
        self.window.unassigned_history_visible_limit = 6
        self.window.refresh_history_list()
        self.assertEqual(len(self.window.history_rows), 6)
        self.search("截图")
        self.assertEqual(len(self.window.history_rows), 50)
        self.assertIsNone(self.window._history_search_loading_label)
        self.assertTrue(self.window.history_search_input.hasFocus())
        more = self.window.history_disclosure_buttons[("search", "")]
        self.assertIn("25", more.text())
        with patch.object(self.window.chat_storage, "search_sidebar_conversations", side_effect=AssertionError("page must reuse result")):
            QTest.mouseClick(more, Qt.LeftButton)
            self.assertEqual(len(self.window.history_rows), 75)
        self.assertNotIn(("search", ""), self.window.history_disclosure_buttons)
        self.window.history_search_input.clear()
        self.assertEqual(len(self.window.history_rows), 6)
        self.assertEqual(self.window.unassigned_history_visible_limit, 6)

    def test_failure_shows_empty_result_keeps_input_and_enter_searches_again(self):
        self.search("截图")
        with patch.object(self.window.chat_storage, "search_sidebar_conversations", side_effect=sqlite3.OperationalError("locked")):
            self.search("归档")
        self.assertEqual(self.window.history_rows, {})
        self.assertEqual(self.window.history_search_input.text(), "归档")
        labels = [label.text() for label in self.window.history_container.findChildren(QLabel)]
        self.assertIn("没有匹配的项目或对话", labels)
        self.assertFalse(hasattr(self.window, "history_search_notice"))
        QTest.keyClick(self.window.history_search_input, Qt.Key_Return)
        self.wait_for(lambda: self.window.history_search_controller.worker is None)
        self.assertEqual(len(self.window.history_rows), 50)

    def test_rapid_input_no_results_and_clear_do_not_restore_old_query(self):
        for query in ("截", "截图", "不存在的搜索内容"):
            self.window.history_search_input.setText(query)
        self.wait_for(lambda: not self.window.history_search_timer.isActive()
                      and self.window.history_search_controller.worker is None)
        self.assertEqual(self.window.history_rows, {})
        self.window.history_search_input.setText("截图")
        self.window.refresh_history_list()
        self.window.history_search_input.clear()
        self.wait_for(lambda: self.window.history_search_controller.worker is None)
        self.assertEqual(len(self.window.history_rows), 3)
        self.assertIsNone(self.window._history_search_result)

    def test_corrupt_search_shows_empty_result_without_touching_messages(self):
        error = sqlite3.DatabaseError("malformed")
        error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT_VTAB
        before = self.window.chat_storage.get_messages("search-000")
        with patch.object(self.window.chat_storage, "search_sidebar_conversations", side_effect=error):
            self.search("截图")
        self.assertEqual(self.window.history_rows, {})
        self.assertEqual(self.window.history_search_input.text(), "截图")
        self.assertEqual(self.window.chat_storage.get_messages("search-000"), before)
