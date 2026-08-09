import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.conversation_render import build_conversation_render_spans
from core.theme_package import COMPONENT_CATALOG
from main import FileWorkbench, MainWindow, QuestionNavigatorRail


class FileWorkbenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigator_reflows_between_overlay_and_pinned_without_losing_preference(self):
        navigator = QFrame()
        navigator.setObjectName("FileNavigatorPanel")
        content = QWidget()
        workbench = FileWorkbench(navigator, content)
        workbench.resize(860, 520)
        workbench.set_navigator_state(visible=True, pinned=True, width=300)
        workbench.show()
        self.app.processEvents()

        self.assertTrue(workbench.is_effectively_pinned())
        self.assertEqual(content.geometry().left(), 301)

        workbench.resize(560, 520)
        self.app.processEvents()
        self.assertFalse(workbench.is_effectively_pinned())
        self.assertTrue(workbench.navigator_pinned)
        self.assertEqual(content.geometry().left(), 0)

        workbench.set_navigator_state(visible=False)
        self.app.processEvents()
        self.assertTrue(navigator.isHidden())
        self.assertTrue(workbench.navigator_pinned)
        workbench.close()

    def test_editor_focus_hides_navigator_without_changing_preferences(self):
        navigator = QFrame()
        navigator.setObjectName("FileNavigatorPanel")
        content = QWidget()
        workbench = FileWorkbench(navigator, content)
        workbench.resize(860, 520)
        workbench.set_navigator_state(visible=True, pinned=True, width=300)
        workbench.show()
        self.app.processEvents()

        workbench.set_editor_focus(True)
        self.app.processEvents()
        self.assertTrue(navigator.isHidden())
        self.assertEqual(content.geometry().left(), 0)
        self.assertTrue(workbench.navigator_visible)
        self.assertTrue(workbench.navigator_pinned)

        workbench.set_editor_focus(False)
        self.app.processEvents()
        self.assertFalse(navigator.isHidden())
        self.assertEqual(content.geometry().left(), 301)
        self.assertTrue(workbench.navigator_pinned)
        workbench.close()


class QuestionNavigatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_summary_excludes_guidance_and_hidden_runtime_context(self):
        window = MainWindow.__new__(MainWindow)
        state = type(
            "_State",
            (),
            {
                "messages": [
                    {"id": "u1", "role": "user", "content": "第一问\n补充内容"},
                    {"id": "a1", "role": "assistant", "content": "处理中", "meta": {"ui_reply_kind": "stage"}},
                    {"id": "a2", "role": "assistant", "content": "最终回答", "meta": {"ui_reply_kind": "final"}},
                    {"id": "g1", "role": "user", "content": "同轮补充", "meta": {"same_turn_guidance": True}},
                    {
                        "id": "hidden",
                        "role": "user",
                        "content": "运行上下文",
                        "meta": {"hidden": True, "kind": "runtime_context"},
                    },
                    {"id": "u2", "role": "user", "content": "第二问"},
                ]
            },
        )()

        entries = window._question_navigator_entries_for_state(state)

        self.assertEqual([entry["message_id"] for entry in entries], ["u1", "u2"])
        self.assertEqual(entries[0]["title"], "第一问")
        self.assertEqual(entries[0]["answer"], "最终回答")
        self.assertEqual(entries[1]["answer"], "尚无回复")

    def test_rail_hides_for_one_question_and_supports_keyboard_jump(self):
        parent = QWidget()
        parent.resize(120, 180)
        rail = QuestionNavigatorRail(parent)
        rail.setGeometry(0, 0, 40, 120)
        parent.show()
        rail.set_entries([{"message_id": "u1", "title": "一", "answer": "答"}])
        self.app.processEvents()
        self.assertTrue(rail.isHidden())

        entries = [
            {"message_id": f"u{index}", "title": str(index), "answer": "答"}
            for index in range(1, 9)
        ]
        rail.set_entries(entries)
        rail.set_active_message_id("u8")
        rail.setFocus(Qt.OtherFocusReason)
        emitted = []
        rail.jumpRequested.connect(emitted.append)
        self.app.processEvents()
        QTest.keyClick(rail, Qt.Key_Return)

        self.assertFalse(rail.isHidden())
        self.assertEqual(emitted, ["u8"])
        self.assertGreater(rail.window_start, 0)
        parent.close()

    def test_question_navigator_is_theme_configurable(self):
        self.assertIn("conversation.question_navigator", COMPONENT_CATALOG)

    def test_unmaterialized_question_loads_every_span_through_current_history_page(self):
        window = MainWindow.__new__(MainWindow)
        messages = []
        for index in range(1, 4):
            messages.extend(
                [
                    {"id": f"u{index}", "role": "user", "content": f"问题 {index}"},
                    {"id": f"a{index}", "role": "assistant", "content": f"答复 {index}"},
                ]
            )
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel("当前已渲染内容"))
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        state = type("_State", (), {})()
        state.session_id = "session-1"
        state.messages = messages
        state.render_items = build_conversation_render_spans(messages)
        state.displayed_render_count = 2
        state.render_node_by_message_id = {}
        state.history_loaded = True
        state.history_loading = False
        state.auto_loading_history = False
        state.chat_layout = layout
        state.chat_scroll = scroll
        state.auto_scroll_enabled = True
        state.pending_scroll_force = True
        window.get_current_session = MagicMock(return_value=state)
        window.add_system_toast = MagicMock()
        window._render_next_history_page_span = MagicMock()

        with patch("main.log_ui_navigation"), patch("main.QTimer.singleShot") as single_shot:
            window.jump_to_question("u1")

        rendered_start = len(state.render_items) - 2
        self.assertEqual(state.history_page_queue, state.render_items[:rendered_start])
        self.assertEqual(state.displayed_render_count, len(state.render_items))
        self.assertFalse(state.auto_scroll_enabled)
        self.assertFalse(state.pending_scroll_force)
        self.assertEqual(state.history_page_jump_target_id, "u1")
        single_shot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
