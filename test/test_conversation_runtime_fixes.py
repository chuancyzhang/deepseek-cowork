import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from main import ChatBubble, InlineInteractionCard, MainWindow, ToolCallCard
from ui.conversation_files import ConversationFileTable


class ConversationRuntimeFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_error_only_results_are_visible_failures(self):
        from core.task_details import tool_result_status
        for result in ({"error": "列表响应结构不兼容"}, "Error: unavailable",
                       {"status": "denied"}, {"status": "invalid_tool_call"}):
            with self.subTest(result=result):
                self.assertEqual(tool_result_status(result), "error")
        self.assertEqual(tool_result_status({"ok": True, "error": "historical diagnostic"}), "done")

    def test_failure_with_legacy_silent_flag_keeps_card_and_details(self):
        card = ToolCallCard("run_skill_script", {"skill_name": "knowledge-library"}, "failed")
        host = QWidget()
        QVBoxLayout(host).addWidget(card)
        host.show()
        state = SimpleNamespace(session_id="session", tool_cards={"failed": card},
            pending_tool_results={}, pending_interactions={}, step_records=[{"tool_id": "failed"}],
            has_file_changes=False, changed_files=[])
        window = MagicMock()
        window.get_session.return_value = state
        window._event_matches_active_run.return_value = True
        window._timeline_find_event.return_value = None
        failure = {"status": "error", "error": "列表响应结构不兼容：items"}
        MainWindow.update_tool_card(window, {"id": "failed", "result": json.dumps(failure),
            "result_obj": failure, "meta": {"silent_repair": True}}, "session")
        self.assertIs(state.tool_cards["failed"], card)
        self.assertTrue(card.isVisible())
        self.assertTrue(card.failed)
        self.assertIn("执行失败", card.args_preview_label.text())
        self.assertEqual(state.step_records[0]["status"], "error")
        self.assertEqual(card.result_obj, failure)
        host.close()
        host.deleteLater()

    def test_only_current_tool_file_changes_request_deliverable_refresh(self):
        state = SimpleNamespace(session_id="session", tool_cards={}, pending_tool_results={},
            step_records=[], has_file_changes=True, changed_files=[])
        window = MagicMock()
        window.current_session_id = "session"
        window.get_session.return_value = state
        window._event_matches_active_run.return_value = True
        window._timeline_find_event.return_value = None
        scenarios = [
            ("read", "text_file_read", {"ok": True, "text": "read existing file"}, [], False),
            ("write", "apply_patch", {"ok": True, "changes": [{"path": "report.md"}]}, ["report.md"], True),
            ("read_again", "text_file_read", {"ok": True, "text": "read again"}, [], False),
            ("partial", "apply_patch", {"error": "partial failure", "applied_changes": [{"path": "saved.md"}]}, [], True),
            ("failed", "apply_patch", {"error": "nothing changed"}, ["unchanged.md"], False),
        ]
        for identity, tool, result, related, expect_refresh in scenarios:
            with self.subTest(identity=identity):
                card = ToolCallCard(tool, {}, identity)
                state.tool_cards[identity] = card
                state.step_records.append({"tool_id": identity, "related_files": related})
                window.refresh_deliverables.reset_mock()
                MainWindow.update_tool_card(window, {"id": identity, "result": json.dumps(result),
                    "result_obj": result}, "session")
                if expect_refresh:
                    window.refresh_deliverables.assert_called_once_with(reason="tool_files_changed")
                else:
                    window.refresh_deliverables.assert_not_called()
                card.deleteLater()

    def test_text_question_field_is_laid_out_before_parent_is_shown(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        card = InlineInteractionCard({"kind": "text", "message": "你想先处理什么？"}, host)
        layout.addWidget(card)
        self.assertGreaterEqual(card.layout().indexOf(card.text_input), 0)
        host.resize(360, 280)
        host.show()
        self.app.processEvents()
        self.assertTrue(card.text_input.isVisible())
        QTest.keyClicks(card.text_input, "local environment")
        card.stop_waiting()
        self.assertEqual(card.text_input.text(), "local environment")
        self.assertTrue(card._terminal)
        self.assertFalse(card.submit_btn.isEnabled())
        self.assertIn("任务已停止", card.validation_label.text())
        host.close()
        host.deleteLater()

    def test_unaccepted_answer_stays_visible_for_recovery(self):
        host = QWidget()
        state = SimpleNamespace(session_id="session", chat_layout=QVBoxLayout(host), pending_interactions={})
        state.chat_layout.addStretch()
        window = MagicMock()
        window.get_session.return_value = state
        window.current_session_id = "session"
        # The real method uses its window as the Qt parent.
        host.get_session = lambda sid: state
        host.current_session_id = "session"
        host.set_session_phase = window.set_session_phase
        host.refresh_session_activity_indicator = window.refresh_session_activity_indicator
        host.request_session_scroll_to_bottom = window.request_session_scroll_to_bottom
        card = MainWindow._show_inline_interaction_request(host,
            {"request_id": "question", "kind": "text"}, "session",
            lambda value: {"status": "ok", "resolved": False})
        card.text_input.setText("保留回答")
        card._submit()
        self.assertIn("question", state.pending_interactions)
        self.assertEqual(card.text_input.text(), "保留回答")
        self.assertIn("提交失败", card.validation_label.text())
        card.stop_waiting()
        host.close()
        host.deleteLater()

    def test_many_files_form_clickable_rows_at_narrow_width(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(12):
                file = Path(directory, f"分析结果与处理脚本_{index}_very_long_name.py")
                file.write_text("pass\n", encoding="utf-8")
                paths.append(str(file))
            host = QWidget()
            layout = QVBoxLayout(host)
            table = ConversationFileTable()
            layout.addWidget(table)
            table.set_files(paths, directory)
            opened = []
            table.fileActivated.connect(opened.append)
            host.resize(360, 350)
            host.show()
            self.app.processEvents()
            self.assertEqual(table.heading.text(), "文件 · 12")
            self.assertEqual(table.rows_layout.count(), 5)
            for index in range(5):
                row = table.rows_layout.itemAt(index).widget()
                self.assertLessEqual(row.geometry().right(), table.width())
                self.assertTrue(row.path_label.text().endswith(".py"))
                self.assertGreaterEqual(row.path_label.height(), row.path_label.fontMetrics().height())
                self.assertLess(row.path_label.geometry().right(), row.type_label.geometry().left())
            QTest.mouseClick(table.rows_layout.itemAt(0).widget(), Qt.LeftButton)
            self.assertEqual(opened, [paths[0]])
            QTest.mouseClick(table.toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(table.rows_layout.count(), 12)
            last = table.rows_layout.itemAt(11).widget()
            last.setFocus()
            QTest.keyClick(last, Qt.Key_Space)
            self.assertEqual(opened[-1], paths[-1])
            QTest.mouseClick(table.toggle, Qt.LeftButton)
            self.assertEqual(table.rows_layout.count(), 5)
            host.close()
            host.deleteLater()

    def test_answer_file_table_keeps_full_file_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, f"file_{index}.py") for index in range(8)]
            for path in paths:
                path.write_text("pass\n", encoding="utf-8")
            text = "\n".join(f"[{path.name}]({path.as_posix()})" for path in paths)
            bubble = ChatBubble("agent", text, workspace_dir=directory)
            bubble.show()
            bubble.set_main_content(text, final=True)
            self.app.processEvents()
            self.assertEqual(len(bubble._deliverable_paths), 8)
            self.assertEqual(bubble.deliverable_cards_layout.count(), 5)
            bubble.deleteLater()

    @unittest.skipUnless(os.name == "nt", "Windows drive paths")
    def test_file_on_another_drive_still_opens_its_original_path(self):
        table = ConversationFileTable()
        path = "E:/exports/report.pdf"
        table.set_files([path], "D:/workspace")
        row = table.rows_layout.itemAt(0).widget()
        self.assertEqual(row.text(), path)
        opened = []
        table.fileActivated.connect(opened.append)
        row.click()
        self.assertEqual(opened, [path])
        table.deleteLater()

    def test_explicit_files_outside_workspace_appear_and_open_from_answer(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            paths = [Path(outside, "第一份 录音对话稿.wav.txt"), Path(outside, "第二份录音对话稿.wav.txt")]
            for path in paths:
                path.write_text("已生成的录音样本", encoding="utf-8")
            text = "已生成的样本：\n" + "\n".join(f"- [{path.stem}](<{path.as_posix()}>)" for path in paths)
            bubble = ChatBubble("agent", text, workspace_dir=workspace)
            bubble.show()
            bubble.set_main_content(text, final=True)
            self.app.processEvents()
            self.assertEqual(bubble._deliverable_paths, [str(path) for path in paths])
            self.assertEqual(bubble.deliverable_cards_layout.count(), 2)
            self.assertIn("cowork-file:", bubble.content_edit.toHtml())
            window = MagicMock()
            state = SimpleNamespace(selected_deliverable_path="")
            window.get_session.return_value = state
            window._workspace_dir_for_state.return_value = workspace
            bubble.deliverablePathActivated.connect(
                lambda path: MainWindow.open_deliverable_from_chat(window, path, "session"))
            row = bubble.deliverable_cards_layout.itemAt(0).widget()
            self.assertEqual(row.text(), paths[0].as_posix())
            QTest.mouseClick(row, Qt.LeftButton)
            self.assertEqual(state.selected_deliverable_path, str(paths[0]))
            window.select_deliverable.assert_called_once_with(str(paths[0]), render_html=True)
            window.add_system_toast.assert_not_called()
            bubble.close()
            bubble.deleteLater()


if __name__ == "__main__":
    unittest.main()
