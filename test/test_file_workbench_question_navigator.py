import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.conversation_render import build_conversation_render_spans
from core.theme import DesignTokens
from core.theme_package import COMPONENT_CATALOG
from main import FileTabStrip, FileWorkbench, MainWindow, QuestionNavigatorRail


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

        workbench.resize(500, 520)
        self.app.processEvents()
        self.assertFalse(workbench.is_effectively_pinned())
        self.assertTrue(workbench.navigator_pinned)
        self.assertEqual(content.geometry().left(), 0)
        self.assertEqual(navigator.geometry().topLeft(), QPoint(0, 0))
        self.assertEqual(navigator.height(), workbench.height())

        workbench.set_navigator_state(visible=False)
        self.app.processEvents()
        self.assertTrue(navigator.isHidden())
        self.assertTrue(workbench.navigator_pinned)
        workbench.close()

    def test_editor_focus_keeps_pinned_navigator_on_the_left(self):
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
        self.assertFalse(navigator.isHidden())
        self.assertEqual(content.geometry().left(), 301)
        self.assertTrue(workbench.navigator_visible)
        self.assertTrue(workbench.navigator_pinned)

        workbench.set_editor_focus(False)
        self.app.processEvents()
        self.assertFalse(navigator.isHidden())
        self.assertEqual(content.geometry().left(), 301)
        self.assertTrue(workbench.navigator_pinned)
        workbench.close()


class FileTabStripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tabs_deduplicate_emit_paths_and_mark_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "first.txt")
            second = os.path.join(directory, "second.py")
            missing = os.path.join(directory, "missing.md")
            for path in (first, second):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(os.path.basename(path))
            strip = FileTabStrip()
            strip.resize(320, 36)
            activated = []
            closed = []
            strip.activateRequested.connect(activated.append)
            strip.closeRequested.connect(closed.append)
            strip.set_paths([first, second, first, missing], second)
            strip.show()
            self.app.processEvents()

            self.assertEqual(strip.paths, [first, second, missing])
            self.assertEqual(strip.active_path, second)
            missing_frame = strip._tab_frames[strip._path_key(missing)]
            self.assertTrue(missing_frame.property("missing"))
            second_frame = strip._tab_frames[strip._path_key(second)]
            second_frame.findChild(QWidget, "FileTabBody").click()
            second_frame.findChild(QWidget, "FileTabClose").click()

            self.assertEqual(activated, [second])
            self.assertEqual(closed, [second])
            strip.resize(180, 36)
            self.app.processEvents()
            strip._sync_overflow()
            self.assertFalse(strip.overflow_btn.isHidden())
            strip.resize(900, 36)
            self.app.processEvents()
            strip._sync_overflow()
            self.app.processEvents()
            self.assertTrue(strip.overflow_btn.isHidden())
            self.assertEqual(
                {frame.width() for frame in strip._tab_frames.values()},
                {DesignTokens.file_tab_preferred_width},
            )
            strip.close()

    def test_tabs_keep_preferred_width_until_the_strip_needs_to_compress(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("first-document.txt", "second-document.py"):
                path = os.path.join(directory, name)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(name)
                paths.append(path)
            strip = FileTabStrip()
            strip.resize(760, 36)
            strip.set_paths(paths[:1], paths[0])
            strip.show()
            self.app.processEvents()
            strip._sync_tab_widths()

            first_frame = strip._tab_frames[strip._path_key(paths[0])]
            self.assertEqual(first_frame.width(), DesignTokens.file_tab_preferred_width)
            self.assertLess(first_frame.width(), strip.scroll.viewport().width())

            strip.set_paths(paths, paths[1])
            self.app.processEvents()
            strip._sync_tab_widths()
            wide_widths = [
                strip._tab_frames[strip._path_key(path)].width() for path in paths
            ]
            self.assertEqual(
                wide_widths,
                [DesignTokens.file_tab_preferred_width] * 2,
            )

            strip.resize(360, 36)
            self.app.processEvents()
            strip._sync_tab_widths()
            compact_widths = [
                strip._tab_frames[strip._path_key(path)].width() for path in paths
            ]
            self.assertEqual(compact_widths[0], compact_widths[1])
            self.assertGreaterEqual(compact_widths[0], DesignTokens.file_tab_min_width)
            self.assertLess(compact_widths[0], DesignTokens.file_tab_preferred_width)
            self.assertLessEqual(
                sum(compact_widths) + strip.tabs_layout.spacing(),
                strip.scroll.viewport().width(),
            )
            strip.close()

    def test_main_window_keeps_ordered_tabs_per_runtime_session(self):
        window = MainWindow()
        try:
            with tempfile.TemporaryDirectory() as directory:
                paths = []
                for name in ("one.txt", "two.py", "three.json"):
                    path = os.path.join(directory, name)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(name)
                    paths.append(path)
                window._apply_workspace_to_ui(directory, refresh_sidebar=False)
                for path in paths:
                    self.assertTrue(window.select_deliverable(path, render_html=False))
                state = window.get_current_session()

                self.assertEqual(state.open_file_paths, paths)
                self.assertEqual(state.selected_deliverable_path, paths[-1])
                self.assertNotIn(os.path.basename(paths[-1]), window.preview_meta_label.text())
                self.assertTrue(window.deliverable_status_label.isHidden())
                self.assertTrue(window.select_deliverable(paths[0], render_html=False))
                self.assertEqual(state.open_file_paths, paths)
                self.assertEqual(state.selected_deliverable_path, paths[0])

                with patch.object(window, "confirm_leave_deliverable_edit", return_value=False):
                    self.assertFalse(window.close_file_tab(paths[0]))
                self.assertEqual(state.open_file_paths, paths)
                self.assertEqual(state.selected_deliverable_path, paths[0])

                self.assertTrue(window.close_file_tab(paths[0]))
                self.assertEqual(state.open_file_paths, paths[1:])
                self.assertEqual(state.selected_deliverable_path, paths[1])

                second_session_id = window.create_new_session(
                    make_current=False,
                    workspace_dir=directory,
                )
                second_state = window.get_session(second_session_id)
                second_state.open_file_paths = [paths[2]]
                second_state.selected_deliverable_path = paths[2]
                window.set_current_session(second_session_id)
                self.assertEqual(window.file_tab_strip.paths, [paths[2]])
                self.assertEqual(window.file_tab_strip.active_path, paths[2])
                window.set_current_session(state.session_id)
                self.assertEqual(window.file_tab_strip.paths, paths[1:])
                self.assertEqual(window.file_tab_strip.active_path, paths[1])

                window.resize(1280, 760)
                window.show()
                window.show_context_drawer(window.RIGHT_TAB_FILES)
                window.file_navigator_pinned = False
                window.file_navigator_pin_btn.setChecked(False)
                window._sync_file_navigator_layout()
                window.file_navigator_pin_btn.click()
                self.app.processEvents()
                QTest.qWait(20)
                self.app.processEvents()
                self.assertTrue(window.context_drawer_expanded)
                self.assertTrue(window.file_navigator_pinned)
                self.assertTrue(window.file_workbench.is_effectively_pinned())
                self.assertGreater(window.file_workbench.content.geometry().left(), 0)

                window.file_workbench.resize(500, window.file_workbench.height())
                window._sync_file_navigator_layout()
                self.assertFalse(window.file_workbench.is_effectively_pinned())
                self.assertTrue(window.file_navigator_pin_btn.isChecked())
                self.assertFalse(window.file_navigator_pin_notice.isHidden())

                window.file_workbench.resize(860, window.file_workbench.height())
                window._sync_file_navigator_layout()
                self.assertTrue(window.file_workbench.is_effectively_pinned())
                self.assertTrue(window.file_navigator_pin_notice.isHidden())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


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

    def test_question_navigator_is_anchored_near_sidebar_edge(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.messages = [
                {"id": "u1", "role": "user", "content": "第一问"},
                {"id": "a1", "role": "assistant", "content": "答复一"},
                {"id": "u2", "role": "user", "content": "第二问"},
            ]
            window.resize(1280, 760)
            window.show()
            self.app.processEvents()
            window._sync_question_navigator(state.session_id)
            self.app.processEvents()

            self.assertIs(window.question_navigator_theme_host.parentWidget(), window.main_container)
            self.assertEqual(
                window.question_navigator_theme_host.x(),
                DesignTokens.question_navigator_sidebar_gap,
            )
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

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
