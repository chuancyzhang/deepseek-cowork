import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QMenu
from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.request_snapshot import RequestSnapshotStore
from main import MainWindow, SystemToast
from ui.chat_log_export import ConversationExportController, ChatLogExportWorker
from ui.primitives import ProductInlineNotice


class TaskDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_failure_moves_notice_after_latest_reply_and_rebinds_details(self):
        parent = QWidget()
        layout = QVBoxLayout(parent)
        first = QLabel("first failed reply")
        second = QLabel("second failed reply")
        later_input = QLabel("next user input")
        notice = ProductInlineNotice("failed", "error")
        for widget in (first, notice, second, later_input):
            layout.addWidget(widget)
        layout.addStretch()
        state = SimpleNamespace(session_id="A", chat_layout=layout, failure_notice_anchor=first,
                                run_failure_details={"run_id": "r1", "error": "first"})
        window = SimpleNamespace(_show_run_failure_details=MagicMock())
        MainWindow._present_run_failure_notice(window, state, notice, "r1")
        notice.details_button.click()
        self.assertEqual(window._show_run_failure_details.call_args.args[1]["error"], "first")
        state.failure_notice_anchor = second
        state.run_failure_details = {"run_id": "r2", "error": "second"}
        MainWindow._present_run_failure_notice(window, state, notice, "r2")
        self.assertEqual(layout.indexOf(notice), layout.indexOf(second) + 1)
        self.assertLess(layout.indexOf(notice), layout.indexOf(later_input))
        notice.details_button.click()
        self.assertEqual(window._show_run_failure_details.call_count, 2)
        self.assertEqual(window._show_run_failure_details.call_args.args[1]["error"], "second")
        notice.set_details(None)
        self.assertTrue(notice.details_button.isHidden())
        parent.deleteLater()

    def test_historical_export_reads_selected_conversation_without_activation(self):
        with tempfile.TemporaryDirectory() as root:
            storage = ChatStorage(os.path.join(root, "chat.sqlite"))
            storage.upsert_conversation("A", title="对话 A")
            storage.upsert_conversation("B", title="对话 B")
            storage.replace_messages("A", [{"role": "user", "content": "A content"}])
            window = SimpleNamespace(current_session_id="B", get_session=lambda sid: None, chat_storage=storage)
            snapshot = MainWindow._capture_chat_log_snapshot(window, "A")
            self.assertEqual(snapshot["session_id"], "A")
            self.assertEqual(snapshot["messages"][0]["content"], "A content")
            self.assertEqual(window.current_session_id, "B")

    def test_request_export_writes_frozen_snapshot_and_reports_write_failure(self):
        with tempfile.TemporaryDirectory() as root:
            payload = {"session_id": "A", "title": "A", "captured_at": datetime.now(),
                       "request": {"model": "m", "messages": [{"role": "user", "content": "original"}]}}
            target = os.path.join(root, "request.json")
            worker = ChatLogExportWorker(copy.deepcopy(payload), target, MagicMock(), None, "request")
            payload["request"]["model"] = "changed"
            worker.run()
            self.assertEqual(worker.error, "")
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["model"], "m")
            bad = ChatLogExportWorker(payload, os.path.join(root, "missing", "request.json"), MagicMock(), None, "request")
            bad.run()
            self.assertTrue(bad.error)

    def test_dialog_cancel_releases_busy_state_without_writing(self):
        owner = QWidget()
        snapshot = {"session_id": "A", "title": "A", "captured_at": datetime.now(), "request": {}}
        notify = MagicMock()
        controller = ConversationExportController(lambda sid, kind: snapshot, notify, MagicMock(), owner)
        with patch("ui.chat_log_export.QFileDialog") as dialog:
            dialog.return_value.exec.return_value = False
            controller.export("A", "request")
        self.assertFalse(controller.busy("A", "request"))
        self.assertEqual(controller.workers, {})
        notify.assert_not_called()
        owner.deleteLater()

    def test_menu_export_binds_clicked_session_without_switching(self):
        menu = QMenu()
        export = MagicMock()
        copy_record = MagicMock()
        controller = SimpleNamespace(busy=lambda sid, kind: False, export=export, copy=copy_record)
        values = dict(chat_history_dir="unused", sessions={}, current_session_id="B",
            chat_storage=SimpleNamespace(get_conversation_record=lambda sid: {}),
            conversation_exports=controller, begin_session_inline_rename=MagicMock(),
            set_conversation_pinned=MagicMock(), archive_session=MagicMock(), delete_session=MagicMock())
        window = QWidget()
        for key, value in values.items():
            setattr(window, key, value)
        class MenuCapture:
            def __getattr__(self, name):
                return getattr(menu, name)
            def exec(self, _position):
                labels = [action.text() for action in menu.actions()]
                self_case.assertLess(labels.index("归档"), labels.index("下载聊天记录…"))
                self_case.assertLess(labels.index("下载最近一次请求…"), labels.index("删除"))
                next(action for action in menu.actions() if action.text() == "下载聊天记录…").trigger()
                next(action for action in menu.actions() if action.text() == "复制聊天记录").trigger()
                self_case.assertFalse(next(action for action in menu.actions() if action.text() == "复制最近一次请求").isEnabled())
        self_case = self
        with patch("main.create_styled_menu", return_value=MenuCapture()):
            MainWindow.show_session_menu(window, "A", None, position=0)
        export.assert_called_once_with("A", "chat")
        copy_record.assert_called_once_with("A", "chat")
        self.assertEqual(window.current_session_id, "B")
        menu.deleteLater()
        window.deleteLater()

    def test_clipboard_matches_download_snapshot_for_chat_and_request(self):
        owner = QWidget()
        selected = ["A"]
        snapshot = {"session_id": "A", "title": "对话 A", "captured_at": datetime.now().astimezone(),
                    "running": False, "messages": [{"role": "user", "content": "用户消息"},
                    {"role": "assistant", "content": "回答" * 12000, "reasoning_content": "思考过程"}],
                    "timeline": [], "turn_id": "", "live_tools": {},
                    "request": {"session_id": "A", "parameters": {"messages": ["长内容" * 12000]}}}
        capture = MagicMock(side_effect=lambda sid, kind: copy.deepcopy(snapshot))
        notify = MagicMock()
        controller = ConversationExportController(capture, notify, MagicMock(), owner)
        with tempfile.TemporaryDirectory() as root:
            for kind in ("chat", "request"):
                with self.subTest(kind=kind):
                    selected[0] = "A"
                    controller.copy(selected[0], kind)
                    worker = controller.workers[("A", kind)]
                    selected[0] = "B"
                    self.assertTrue(worker.wait(5000))
                    self.app.processEvents()
                    self.assertFalse(controller.busy("A", kind))
                    self.assertEqual(notify.call_args.args[1], "success")
                    target = os.path.join(root, kind + ".txt")
                    download = ChatLogExportWorker(snapshot, target, MagicMock(), owner, kind)
                    download.run()
                    self.assertFalse(download.error)
                    with open(target, encoding="utf-8") as handle:
                        self.assertEqual(self.app.clipboard().text(), handle.read())
                    self.assertEqual(capture.call_args.args, ("A", kind))
        self.app.clipboard().clear()
        owner.deleteLater()

    def test_success_toast_actions_do_not_open_until_clicked(self):
        open_file, reveal = MagicMock(), MagicMock()
        toast = SystemToast("已保存", "success", actions=(("打开文件", open_file), ("打开所在文件夹", reveal)))
        open_file.assert_not_called()
        toast.action_buttons[0].click()
        open_file.assert_called_once()
        reveal.assert_not_called()
        toast.action_buttons[1].click()
        reveal.assert_called_once()
        toast.deleteLater()

    def test_provider_errors_have_specific_user_reason(self):
        self.assertEqual(MainWindow._run_failure_presentation("401 invalid_model", False, 0, 0)[0], "invalid_model")
        self.assertIn("系统消息位于开头", MainWindow._run_failure_presentation("400 System message must be at the beginning.", False, 0, 0)[1])

    def test_full_panel_keeps_prompt_tabs_and_complete_details_without_extra_sections(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("core.config_manager.get_app_data_dir", return_value=root), \
                 patch("core.config_manager.get_base_dir", return_value=root), \
                 patch.object(MainWindow, "start_background_services", new=lambda self: None):
                window = MainWindow(ConfigManager())
                try:
                    self.assertEqual([button.text() for button in window.observability_segment_buttons],
                                     ["执行上下文", "调用记录", "技术详情"])
                    self.assertFalse(hasattr(window, "chat_log_export_btn"))
                    self.assertFalse(hasattr(window, "td_raw_details"))
                    self.assertFalse(hasattr(window, "td_exported_files"))
                    window.show_tool_details("test-tool", '{"query":"行情"}', '{"ok":false,"error":"domain required"}')
                    self.assertEqual(json.loads(window.td_result_edit.toPlainText())["ok"], False)
                    payload = {"content": "正文" * 10000}
                    window.show_tool_details("long-tool", '{"query":"行情"}', payload)
                    window.copy_tool_result()
                    self.assertEqual(json.loads(self.app.clipboard().text()), payload)
                    cursor = window.td_result_edit.textCursor()
                    cursor.setPosition(10)
                    cursor.setPosition(20, QTextCursor.KeepAnchor)
                    window.td_result_edit.setTextCursor(cursor)
                    window.show_tool_details("long-tool", '{"query":"行情"}', payload, switch_tab=False)
                    self.assertEqual(window.td_result_edit.textCursor().anchor(), 10)
                    self.assertEqual(window.td_result_edit.textCursor().position(), 20)
                    window.show_tool_details("plain-tool", '{}', "not JSON\nsecond line")
                    self.assertEqual(window.td_result_edit.toPlainText(), "not JSON\nsecond line")
                    window.create_new_session()
                    window.refresh_step_list(window.current_session_id)
                    self.assertEqual(window.td_result_edit.toPlainText(), "")
                    self.assertEqual(window._detail_full_result, "")
                finally:
                    self.app.processEvents()
                    window.file_model.directoryLoaded.disconnect()
                    window.close()
                    window.deleteLater()
                    self.app.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
