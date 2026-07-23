import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton, QWidget

from main import (
    ConversationHistoryRow,
    MainWindow,
    ProjectHistoryRow,
    SidebarActivityStatus,
    SessionSkillCaptureIndicator,
    parse_tool_arguments,
)
from core.theme import DesignTokens
from ui.primitives import (
    ProductCodeViewer,
    ProductGrabScrollBar,
    ProductResultViewer,
    ProductSegmentedControl,
    SidebarInlineNameEditor,
)


class MainWorkspaceLinearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_observability_uses_shared_segments_and_structured_viewers(self):
        self.assertFalse(hasattr(self.window, "step_intro_label"))
        self.assertIsInstance(self.window.observability_segment_control, ProductSegmentedControl)
        self.assertIsInstance(self.window.td_args_edit, ProductCodeViewer)
        self.assertIsInstance(self.window.td_result_edit, ProductResultViewer)
        self.assertIsInstance(self.window.td_args_meta_edit, ProductCodeViewer)

    def test_tool_arguments_accept_json_and_python_literal_without_hiding_errors(self):
        parsed, error = parse_tool_arguments("{'code': 'print(1)', 'timeout': 3}")
        self.assertEqual(parsed["code"], "print(1)")
        self.assertFalse(error)
        parsed, error = parse_tool_arguments("not valid payload")
        self.assertIsNone(parsed)
        self.assertIn("无法解析", error)

    def test_chat_scroll_drag_pauses_and_recomputes_auto_scroll(self):
        state = self.window.get_current_session()
        bar = state.chat_scroll.verticalScrollBar()
        self.assertIsInstance(bar, ProductGrabScrollBar)
        bar.setRange(0, 100)
        bar.setValue(20)
        self.window.on_chat_scroll_drag_started(state.session_id)
        self.assertTrue(state.scroll_dragging)
        self.assertFalse(state.auto_scroll_enabled)
        self.window.on_chat_scroll_drag_finished(state.session_id)
        self.assertFalse(state.scroll_dragging)
        self.assertFalse(state.auto_scroll_enabled)
        bar.setValue(100)
        self.window.on_chat_scroll_drag_finished(state.session_id)
        self.assertTrue(state.auto_scroll_enabled)

    def test_chat_scrollbar_uses_thin_visual_and_forgiving_drag_target(self):
        bar = ProductGrabScrollBar(Qt.Vertical)
        bar.setRange(0, 1000)
        bar.setPageStep(200)
        bar.setValue(500)
        bar.resize(14, 600)
        bar.show()
        self.app.processEvents()

        self.assertEqual(bar.sizeHint().width(), 14)
        self.assertIn("margin: 1px 3px", bar.styleSheet())
        handle = bar._slider_rect()
        pressed = []
        released = []
        bar.sliderPressed.connect(lambda: pressed.append(True))
        bar.sliderReleased.connect(lambda: released.append(True))

        edge = handle.center()
        edge.setX(0)
        QTest.mouseMove(bar, edge)
        self.assertEqual(bar.cursor().shape(), Qt.OpenHandCursor)
        QTest.mousePress(bar, Qt.LeftButton, pos=edge)
        self.assertTrue(bar.isSliderDown())
        self.assertEqual(bar.cursor().shape(), Qt.ClosedHandCursor)
        QTest.mouseMove(bar, edge + QPoint(0, 40), delay=1)
        QTest.mouseRelease(bar, Qt.LeftButton, pos=edge + QPoint(0, 40))
        self.assertGreater(bar.value(), 500)
        self.assertEqual(pressed, [True])
        self.assertEqual(released, [True])

        bar.setValue(500)
        handle = bar._slider_rect()
        near_handle = QPoint(handle.center().x(), min(bar.height() - 1, handle.bottom() + 4))
        QTest.mousePress(bar, Qt.LeftButton, pos=near_handle)
        self.assertTrue(bar.isSliderDown())
        QTest.mouseRelease(bar, Qt.LeftButton, pos=near_handle)
        bar.close()

    def test_chat_scrollbar_theme_refresh_updates_visual_and_hit_widths(self):
        bar = ProductGrabScrollBar(Qt.Vertical)
        with patch.object(DesignTokens, "chat_scrollbar_visual_width", 6), patch.object(
            DesignTokens, "chat_scrollbar_hit_width", 18
        ):
            bar.refresh_theme()
            self.assertIn("width: 18px", bar.styleSheet())
            self.assertIn("margin: 1px 6px", bar.styleSheet())
            self.assertEqual(bar.sizeHint().width(), 18)
        bar.close()

    def test_pending_skill_draft_is_owned_by_its_session(self):
        first = self.window.get_current_session()
        second_id = self.window.create_new_session(make_current=True)
        second = self.window.get_session(second_id)
        first.pending_conversation_skill_result = {
            "ok": True,
            "session_id": first.session_id,
            "draft": {"skill_name": "session-skill"},
        }
        captured = []
        with patch.object(self.window, "handle_conversation_skill_finished", captured.append):
            self.assertTrue(self.window.review_pending_conversation_skill_draft(first.session_id))
        self.assertEqual(captured[0]["session_id"], first.session_id)
        self.assertIsNone(first.pending_conversation_skill_result)
        self.assertIsNone(second.pending_conversation_skill_result)

    def test_capability_page_uses_direct_refresh_action(self):
        from main import SkillsCenterDialog
        page = SkillsCenterDialog(self.window.skill_manager, self.window.config_manager, self.window)
        self.assertEqual(page.more_btn.text(), "刷新")
        self.assertIsNone(page.more_btn.menu())
        page.deleteLater()

    def test_project_preview_keeps_five_conversations_and_hidden_actions(self):
        with tempfile.TemporaryDirectory() as project_dir:
            self.window.history_rows = {}
            self.window.history_buttons = {}
            self.window.history_age_labels = {}
            self.window.history_activity_indicators = {}
            self.window.project_rows = {}
            self.window.project_buttons = {}
            self.window.history_inline_hosts = {}
            self.window.project_inline_hosts = {}
            sessions = [
                {"id": f"session-{index}", "title": f"对话 {index}", "updated_at": index, "pinned": False}
                for index in range(7)
            ]
            self.window.project_preview_paths.add(project_dir)
            row = self.window._make_project_row(
                {"path": project_dir, "name": "项目", "pinned": False}, sessions
            )
            conversation_rows = [
                item for item in row.findChildren(ConversationHistoryRow)
                if not isinstance(item, ProjectHistoryRow)
            ]
            self.assertEqual(len(conversation_rows), 5)
            actions = row.findChild(QWidget, "ProjectActions")
            self.assertIsNotNone(actions)
            self.assertTrue(actions.isHidden())
            header = row.findChild(ProjectHistoryRow, "HistoryRow")
            header._set_actions_visible(True)
            self.assertFalse(actions.isHidden())
            header._set_actions_visible(False)
            self.assertTrue(actions.isHidden())
            disclosure = [button.text().strip() for button in row.findChildren(type(self.window.action_btn))]
            self.assertIn("展开显示", disclosure)
            row.deleteLater()

    def test_project_preview_includes_live_conversation_outside_recent_five(self):
        with tempfile.TemporaryDirectory() as project_dir:
            live_id = self.window.create_new_session(
                make_current=False,
                workspace_dir=project_dir,
            )
            live_state = self.window.get_session(live_id)
            live_state.live_activity = True
            sessions = [
                {"id": f"recent-{index}", "title": f"最近 {index}", "updated_at": 100 - index}
                for index in range(6)
            ]
            sessions.append({"id": live_id, "title": "后台运行", "updated_at": 1})
            self.window.project_preview_paths.add(project_dir)
            row = self.window._make_project_row(
                {"path": project_dir, "name": "项目", "pinned": False},
                sessions,
            )
            self.assertIn(live_id, self.window.history_rows)
            project_status = self.window.project_activity_statuses[project_dir]
            self.assertEqual(project_status._state, "running")
            self.assertFalse(project_status.isHidden())
            row.deleteLater()

    def test_sidebar_running_status_survives_long_title_min_width_hover_and_theme_refresh(self):
        state = self.window.get_current_session()
        state.live_activity = True
        self.window.history_rows = {}
        self.window.history_buttons = {}
        self.window.history_age_labels = {}
        self.window.history_activity_indicators = {}
        self.window.history_activity_statuses = {}
        self.window.history_skill_capture_indicators = {}
        row = self.window._make_project_session_row(
            {
                "id": state.session_id,
                "title": "这是一个非常长的会话标题，用来验证运行状态不会被标题挤出侧栏",
                "updated_at": 1,
                "pinned": False,
            },
            compact=True,
        )
        row.resize(DesignTokens.sidebar_min_width - 20, 34)
        row.show()
        self.app.processEvents()
        status = self.window.history_activity_statuses[state.session_id]
        self.assertIsInstance(status, SidebarActivityStatus)
        self.assertFalse(status.isHidden())
        self.assertLessEqual(status.geometry().right(), row.rect().right())
        row._set_actions_visible(True)
        status.refresh_theme()
        self.app.processEvents()
        self.assertFalse(status.isHidden())
        self.assertLessEqual(status.geometry().right(), row.rect().right())
        row.deleteLater()

    def test_manual_title_wins_over_generated_title(self):
        state = SimpleNamespace(
            session_id="manual-title-session",
            messages=[{"role": "user", "content": "自动生成标题"}],
            persisted_conversation_meta={"manual_title": True},
        )
        with patch.object(
            self.window.chat_storage,
            "get_conversation_record",
            return_value={"title": "用户命名", "meta": {"manual_title": True}},
        ):
            self.assertEqual(self.window._resolved_session_title(state), "用户命名")

    def test_chat_workspace_header_never_uses_workspace_uuid(self):
        state = self.window.get_current_session()
        with patch.object(self.window, "_session_workspace_source", return_value="chat"), patch.object(
            self.window, "_workspace_dir_for_state", return_value=r"D:\work\38e12083c3d349bb942869b3517de7fd"
        ), patch.object(self.window, "_resolved_session_title", return_value="客户周报"):
            self.window.update_conversation_header()
        self.assertEqual(self.window.workspace_title_label.text(), "客户周报")

    def test_inline_editor_enter_commits_and_escape_cancels(self):
        committed = []
        editor = SidebarInlineNameEditor("  新名称  ")
        editor.commitRequested.connect(committed.append)
        editor._commit()
        self.assertEqual(committed, ["新名称"])

        cancelled = []
        editor = SidebarInlineNameEditor("原名称")
        editor.cancelRequested.connect(lambda: cancelled.append(True))
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertEqual(cancelled, [True])

    def test_sidebar_navigation_does_not_use_qt_dynamic_widget_properties(self):
        self.assertNotIn("eventFilter", MainWindow.__dict__)
        with tempfile.TemporaryDirectory() as project_dir:
            self.window.project_preview_paths.add(project_dir)
            self.window.history_rows = {}
            self.window.history_buttons = {}
            self.window.history_age_labels = {}
            self.window.history_activity_indicators = {}
            self.window.project_rows = {}
            self.window.project_buttons = {}
            self.window.history_inline_hosts = {}
            self.window.project_inline_hosts = {}
            row = self.window._make_project_row(
                {"path": project_dir, "name": "项目", "pinned": False}, []
            )
            header = row.findChild(ProjectHistoryRow, "HistoryRow")
            self.assertIsNotNone(header)
            self.assertIsNone(header.property("sidebarProjectActions"))

    def test_settings_button_drops_clicked_boolean_argument(self):
        button = next(
            item for item in self.window.findChildren(QPushButton)
            if item.text().strip() == "设置"
        )
        QTest.mouseClick(button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.current_product_route, self.window.PAGE_SETTINGS)

    def test_project_and_chat_rows_accept_real_mouse_clicks(self):
        self.window.history_rows = {}
        self.window.history_buttons = {}
        self.window.history_age_labels = {}
        self.window.history_activity_indicators = {}
        self.window.project_rows = {}
        self.window.project_buttons = {}
        self.window.history_inline_hosts = {}
        self.window.project_inline_hosts = {}
        session_id = self.window.current_session_id
        entry = {"id": session_id, "title": "聊天", "updated_at": 1, "pinned": False}
        project_row = self.window._make_project_session_row(entry, compact=True)
        standalone_row = self.window._make_project_session_row(entry, compact=False)
        with patch.object(self.window, "activate_session") as activate:
            QTest.mouseClick(self.window.history_buttons[session_id], Qt.LeftButton)
            self.app.processEvents()
            activate.assert_called_with(session_id)
        with tempfile.TemporaryDirectory() as project_dir, patch.object(
            self.window, "handle_project_click", return_value=True
        ) as project_click:
            row = self.window._make_project_row(
                {"path": project_dir, "name": "项目", "pinned": False}, []
            )
            QTest.mouseClick(self.window.project_buttons[project_dir], Qt.LeftButton)
            self.app.processEvents()
            project_click.assert_called_with(project_dir, query_active=False)
            row.deleteLater()
        project_row.deleteLater()
        standalone_row.deleteLater()

    def test_skill_capture_sidebar_indicator_tracks_source_session_and_returns_to_it(self):
        state = self.window.get_current_session()
        self.window.history_rows = {}
        self.window.history_buttons = {}
        self.window.history_age_labels = {}
        self.window.history_activity_indicators = {}
        self.window.history_skill_capture_indicators = {}
        state.pending_conversation_skill_result = {
            "capture_id": "capture-sidebar",
            "phase": "compiling",
        }
        row = self.window._make_project_session_row(
            {
                "id": state.session_id,
                "title": "来源会话",
                "updated_at": 1,
                "pinned": False,
            },
            compact=False,
        )
        indicator = self.window.history_skill_capture_indicators[state.session_id]
        self.assertIsInstance(indicator, SessionSkillCaptureIndicator)
        self.assertEqual(indicator._phase, "compiling")
        self.assertFalse(indicator.isHidden())
        self.assertIn("正在编译", indicator.toolTip())

        state.pending_conversation_skill_result["phase"] = "draft_ready"
        self.window.refresh_skill_capture_sidebar_indicator(state.session_id)
        self.assertEqual(indicator._phase, "draft_ready")
        self.assertIn("草稿待确认", indicator.toolTip())

        with patch.object(self.window, "activate_session") as activate:
            QTest.mouseClick(indicator, Qt.LeftButton)
            self.app.processEvents()
            activate.assert_called_once_with(state.session_id)
        row.deleteLater()

    def test_sidebar_uses_chat_product_copy(self):
        labels = [button.text().strip() for button in self.window.findChildren(QPushButton)]
        self.assertIn("新建聊天", labels)
        self.assertNotIn("新建对话", labels)

    def test_first_valid_submit_projects_session_into_sidebar_before_worker_runs(self):
        state = self.window.get_current_session()
        staged = SimpleNamespace(
            session_id=state.session_id,
            revision=1,
        )
        with patch.object(
            self.window,
            "_model_profile_for_state",
            return_value={"id": "test-model"},
        ), patch.object(
            self.window,
            "_selected_model_supports_vision",
            return_value=False,
        ), patch.object(
            self.window,
            "_ensure_vision_attachment_support",
            return_value=True,
        ), patch.object(
            self.window,
            "_stage_chat_save_request",
            return_value=staged,
        ), patch.object(
            self.window,
            "_enqueue_staged_chat_save",
            return_value=True,
        ), patch.object(
            self.window,
            "_ensure_session_visible_in_history",
        ) as ensure_visible, patch.object(
            self.window,
            "_build_run_context",
            return_value={},
        ), patch.object(
            self.window,
            "queue_daemon_connection",
        ), patch.object(
            self.window,
            "process_agent_logic",
        ) as process:
            self.assertTrue(
                self.window._submit_session_request(
                    state,
                    "首次提交立即显示",
                    [],
                    clear_current_input=False,
                )
            )
        ensure_visible.assert_called_once_with(state)
        process.assert_called_once()

    def test_staged_chat_save_is_really_enqueued(self):
        state = self.window.get_current_session()
        request = SimpleNamespace(
            session_id=state.session_id,
            revision=7,
            messages=[{"id": "m1", "role": "user", "content": "enqueue"}],
            title="Enqueue",
            status="running",
            meta={},
        )
        with patch.object(
            self.window.chat_save_worker,
            "enqueue",
            return_value=True,
        ) as enqueue:
            self.assertTrue(self.window._enqueue_staged_chat_save(request))
        enqueue.assert_called_once_with(request)

    def test_conversation_header_has_no_more_menu_button(self):
        self.assertFalse(hasattr(self.window, "conversation_more_btn"))


if __name__ == "__main__":
    unittest.main()
