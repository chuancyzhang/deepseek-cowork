import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QPushButton, QWidget

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

    def test_initial_window_and_composer_fit_logical_available_screen(self):
        self.window.show()
        self.app.processEvents()
        self.app.processEvents()
        screen = self.window.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.assertLessEqual(self.window.width(), available.width())
        self.assertLessEqual(self.window.height(), available.height())
        self.assertGreaterEqual(self.window.action_btn.geometry().right(), 0)
        self.assertLessEqual(
            self.window.action_btn.geometry().right(),
            self.window.input_card.width() - 1,
        )

    def test_show_maximized_keeps_chat_and_composer_widths_nonzero(self):
        self.window.showMaximized()
        self.app.processEvents()
        self.app.processEvents()

        self.assertTrue(self.window.isMaximized())
        self.assertGreater(self.window.input_card.width(), 0)
        self.assertEqual(self.window.conversation_column.width(), self.window.session_tabs.width())
        self.assertEqual(self.window.conversation_column.width(), self.window.input_card.width())
        self.assertLessEqual(
            self.window.action_btn.geometry().right(),
            self.window.input_card.width() - 1,
        )

    def test_show_full_screen_keeps_chat_and_composer_widths_nonzero(self):
        self.window.showFullScreen()
        self.app.processEvents()
        self.app.processEvents()

        self.assertTrue(self.window.isFullScreen())
        self.assertGreater(self.window.input_card.width(), 0)
        self.assertEqual(self.window.conversation_column.width(), self.window.session_tabs.width())
        self.assertEqual(self.window.conversation_column.width(), self.window.input_card.width())
        self.assertLessEqual(
            self.window.action_btn.geometry().right(),
            self.window.input_card.width() - 1,
        )

    def test_show_respects_size_requested_before_first_display(self):
        self.window.resize(900, 300)
        self.window.show()
        self.app.processEvents()
        self.app.processEvents()

        self.assertEqual(self.window.width(), 900)
        self.assertEqual(self.window.height(), 300)

    def test_programmatic_sidebar_restore_recomputes_conversation_spacers(self):
        self.window.show()
        self.app.processEvents()
        self.window.resize(1707, 1019)
        self.app.processEvents()
        self.window.sync_context_drawer_layout()
        self.app.processEvents()
        total_width = sum(self.window.main_splitter.sizes())

        self.window.main_splitter.setSizes([0, total_width])
        self.app.processEvents()
        self.app.processEvents()
        collapsed_shell = self.window.dynamic_layout_metrics["shell_width"]
        self.assertEqual(
            self.window.conversation_left_spacer.width()
            + self.window.conversation_column.width()
            + self.window.conversation_right_spacer.width(),
            collapsed_shell,
        )

        self.window.main_splitter.setSizes([DesignTokens.sidebar_width, total_width - DesignTokens.sidebar_width])
        self.app.processEvents()
        self.app.processEvents()
        restored_shell = self.window.dynamic_layout_metrics["shell_width"]
        self.assertLess(restored_shell, collapsed_shell)
        self.assertEqual(
            self.window.conversation_left_spacer.width()
            + self.window.conversation_column.width()
            + self.window.conversation_right_spacer.width(),
            restored_shell,
        )
        self.assertGreaterEqual(self.window.sidebar.width(), DesignTokens.sidebar_min_width)
        input_right = self.window.input_card.mapTo(
            self.window.main_container,
            self.window.input_card.rect().topRight(),
        ).x()
        self.assertLess(input_right, self.window.main_container.width())

    def test_composer_toolbar_reflows_only_below_conversation_breakpoint(self):
        wide_metrics = {
            "drawer_open": False,
            "shell_width": DesignTokens.conversation_max_width,
            "conversation_width": DesignTokens.conversation_max_width,
            "left_spacer_width": 0,
            "right_spacer_width": 0,
            "drawer_width": 0,
        }
        with patch.object(self.window, "_compute_conversation_shell_metrics", return_value=wide_metrics):
            self.window.sync_conversation_widths()

        self.assertEqual(self.window._composer_toolbar_mode, "wide")
        self.assertEqual(self.window.prompt_toolbar.direction(), QBoxLayout.LeftToRight)
        self.assertIs(self.window.prompt_context_group.parentWidget(), self.window.prompt_toolbar_container)
        self.assertIs(self.window.prompt_action_group.parentWidget(), self.window.prompt_toolbar_container)

        compact_width = DesignTokens.conversation_min_width - 1
        compact_metrics = dict(wide_metrics)
        compact_metrics.update(
            shell_width=compact_width,
            conversation_width=compact_width,
        )
        with patch.object(self.window, "_compute_conversation_shell_metrics", return_value=compact_metrics):
            self.window.sync_conversation_widths()

        self.assertEqual(self.window._composer_toolbar_mode, "compact")
        self.assertEqual(self.window.prompt_toolbar.direction(), QBoxLayout.TopToBottom)
        self.assertIs(self.window.prompt_context_group.parentWidget(), self.window.prompt_toolbar_container)
        self.assertIs(self.window.prompt_action_group.parentWidget(), self.window.prompt_toolbar_container)
        self.assertEqual(self.window.action_btn.size().width(), 88)
        self.assertEqual(self.window.action_btn.size().height(), 34)
        self.assertEqual(self.window.model_select_btn.minimumWidth(), 170)

    def test_session_switch_after_sidebar_cycle_keeps_composer_in_bounds(self):
        self.window.show()
        self.window.resize(1707, 1019)
        self.app.processEvents()
        direct_session_id = self.window.current_session_id
        total_width = sum(self.window.main_splitter.sizes())
        self.window.main_splitter.setSizes([0, total_width])
        self.app.processEvents()
        self.window.main_splitter.setSizes([DesignTokens.sidebar_width, total_width - DesignTokens.sidebar_width])
        self.app.processEvents()
        self.app.processEvents()

        with tempfile.TemporaryDirectory() as project_dir:
            project_session_id = self.window.create_new_session(workspace_dir=project_dir)
            self.app.processEvents()
            self.assertEqual(self.window.current_session_id, project_session_id)
            project_bounds = self.window.input_card.geometry()

            self.window.activate_session(direct_session_id, ensure_loaded=False)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.window.current_session_id, direct_session_id)
            direct_bounds = self.window.input_card.geometry()
            self.assertEqual(direct_bounds.x(), project_bounds.x())
            self.assertEqual(direct_bounds.y(), project_bounds.y())
            self.assertEqual(direct_bounds.width(), project_bounds.width())
            input_right = self.window.input_card.mapTo(
                self.window.main_container,
                self.window.input_card.rect().topRight(),
            ).x()
            self.assertLess(input_right, self.window.main_container.width())

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

    def test_question_navigator_hides_on_product_pages_and_restores_for_chat(self):
        self.window.show()
        self.app.processEvents()
        state = self.window.get_current_session()
        state.messages = [
            {"id": "question-1", "role": "user", "content": "第一问"},
            {"id": "answer-1", "role": "assistant", "content": "答复一"},
            {"id": "question-2", "role": "user", "content": "第二问"},
            {"id": "answer-2", "role": "assistant", "content": "答复二"},
        ]
        self.window._sync_question_navigator(state.session_id)
        self.app.processEvents()
        self.assertTrue(self.window.question_navigator_rail.isVisible())

        product_page = QWidget()
        product_page.refresh_list = lambda: None
        self.window.main_page_stack.addWidget(product_page)
        with patch.object(self.window, "_ensure_product_page", return_value=product_page):
            self.assertTrue(self.window.show_product_page(self.window.PAGE_CAPABILITIES))
        self.app.processEvents()

        self.assertFalse(self.window.question_navigator_theme_host.isVisible())
        self.assertFalse(self.window.question_navigator_rail.isVisible())
        self.assertEqual(self.window.question_navigator_theme_host.geometry().width(), 0)
        self.assertTrue(self.window.show_conversation_page())
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(self.window.question_navigator_theme_host.isVisible())
        self.assertTrue(self.window.question_navigator_rail.isVisible())

        self.window.main_page_stack.removeWidget(product_page)
        product_page.deleteLater()

    def test_repeated_chat_save_failure_notifies_once_until_success(self):
        state = self.window.get_current_session()
        with patch.object(self.window, "add_system_toast") as toast:
            self.window.handle_chat_save_failed(state.session_id, 3, "conflict")
            self.window.handle_chat_save_failed(state.session_id, 3, "conflict")
            self.window.handle_chat_save_failed(state.session_id, 4, "conflict")

        toast.assert_not_called()
        self.assertEqual(
            state.conversation_notice.label.text(),
            "聊天记录保存失败，正在等待下一次保存重试。",
        )
        self.assertEqual(
            self.window._chat_save_failure_notified_at[state.session_id]["revision"],
            3,
        )

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

    def test_capability_page_keeps_advanced_management_visible(self):
        from main import SkillsCenterDialog
        page = SkillsCenterDialog(self.window.skill_manager, self.window.config_manager, self.window)
        self.assertEqual(page.advanced_btn.text(), "高级管理")
        self.assertIsNone(page.advanced_btn.menu())
        self.assertEqual(page.mode_control.buttons["library"].text(), "发现能力")
        self.assertEqual(page.mode_control.buttons["mine"].text(), "我的能力")
        page.deleteLater()

    def test_capability_routes_separate_simple_detail_and_advanced_management(self):
        from main import AdvancedSkillsCenterDialog, CapabilityWorkbenchDialog, SkillsCenterDialog

        page = SkillsCenterDialog(self.window.skill_manager, self.window.config_manager, self.window)
        self.window._prepare_embedded_product_page(page, self.window.PAGE_CAPABILITIES)
        self.window.current_product_route = self.window.PAGE_CAPABILITIES
        self.window.current_product_subroute = ""
        self.window.main_page_stack.setCurrentWidget(page)

        self.assertTrue(self.window.show_advanced_capabilities())
        advanced = self.window.product_pages["capability_advanced"]
        self.assertIsInstance(advanced, AdvancedSkillsCenterDialog)
        self.assertEqual(self.window.current_product_subroute, "advanced")
        self.assertTrue(self.window.handle_product_back())

        skill = {
            "name": "document-reader",
            "display_name": "本地文档读取",
            "source_type": "bundled_plugin",
            "enabled": True,
            "config_fields": [],
            "presentation": {
                "category": "docs_knowledge",
                "short_name": "文档读取",
                "summary": "读取工作区文档。",
                "examples": ["总结文档", "提取表格"],
                "access_note": "只读取指定文件。",
            },
        }
        self.assertTrue(self.window.show_capability_detail(skill))
        detail = self.window.product_pages["capability_detail"]
        self.assertIsInstance(detail, CapabilityWorkbenchDialog)
        self.assertTrue(detail.simple_mode)
        self.assertEqual(self.window.current_product_subroute, "detail")

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

    def test_project_history_expands_in_five_item_batches_and_can_collapse(self):
        with tempfile.TemporaryDirectory() as project_dir:
            sessions = [
                {
                    "id": f"project-page-{index}",
                    "title": f"项目对话 {index}",
                    "updated_at": 100 - index,
                    "pinned": False,
                }
                for index in range(12)
            ]
            self.window.project_preview_paths.add(project_dir)

            def build_row():
                self.window.history_rows = {}
                self.window.history_buttons = {}
                self.window.history_age_labels = {}
                self.window.history_activity_indicators = {}
                self.window.history_activity_statuses = {}
                self.window.history_skill_capture_indicators = {}
                self.window.history_inline_hosts = {}
                return self.window._make_project_row(
                    {"path": project_dir, "name": "分页项目", "pinned": False},
                    sessions,
                )

            def row_state(row):
                rows = [
                    item for item in row.findChildren(ConversationHistoryRow)
                    if not isinstance(item, ProjectHistoryRow)
                ]
                disclosures = [
                    button.text().strip()
                    for button in row.findChildren(QPushButton, "HistoryDisclosureButton")
                ]
                return len(rows), disclosures

            rows = []
            first = build_row()
            rows.append(first)
            self.assertEqual(row_state(first), (5, ["展开显示"]))

            self.assertTrue(
                self.window.expand_project_history(project_dir, len(sessions), refresh=False)
            )
            second = build_row()
            rows.append(second)
            self.assertEqual(row_state(second), (10, ["展开显示"]))

            self.assertTrue(
                self.window.expand_project_history(project_dir, len(sessions), refresh=False)
            )
            third = build_row()
            rows.append(third)
            self.assertEqual(row_state(third), (12, ["收起显示"]))

            self.assertTrue(
                self.window.collapse_project_history(project_dir, len(sessions), refresh=False)
            )
            collapsed = build_row()
            rows.append(collapsed)
            self.assertEqual(row_state(collapsed), (5, ["展开显示"]))

            for row in rows:
                row.deleteLater()

    def test_sidebar_loads_all_summaries_before_grouping_without_global_more_button(self):
        with tempfile.TemporaryDirectory() as recent_project, tempfile.TemporaryDirectory() as old_project:
            conversations = [
                {
                    "id": f"recent-{index}",
                    "title": f"最近对话 {index}",
                    "updated_at": 200 - index,
                    "status": "completed",
                    "meta": {
                        "workspace_dir": recent_project,
                        "workspace_source": "project",
                    },
                    "im_provider": "",
                }
                for index in range(80)
            ]
            conversations.append(
                {
                    "id": "old-project-conversation",
                    "title": "最旧但仍存在的项目对话",
                    "updated_at": 1,
                    "status": "completed",
                    "meta": {
                        "workspace_dir": old_project,
                        "workspace_source": "project",
                    },
                    "im_provider": "",
                }
            )
            projects = [
                {"path": recent_project, "name": "最近项目", "pinned": False},
                {"path": old_project, "name": "旧项目", "pinned": False},
            ]
            with patch.object(
                self.window.chat_storage,
                "list_conversations",
                return_value=conversations,
            ) as list_all, patch.object(
                self.window.chat_storage,
                "list_conversation_summaries",
                side_effect=AssertionError("侧栏不应再请求全局截断页"),
            ), patch.object(
                self.window.config_manager,
                "get_projects",
                return_value=projects,
            ), patch.object(
                self.window.config_manager,
                "get",
                return_value=[],
            ):
                self.window.project_preview_paths.add(os.path.normpath(recent_project))
                self.window.refresh_history_list()

            list_all.assert_called_once_with()
            self.assertIn(os.path.normpath(old_project), self.window.project_rows)
            self.assertEqual(len(self.window.history_rows), 5)
            history_copy = [
                button.text().strip()
                for button in self.window.history_container.findChildren(QPushButton)
            ]
            self.assertNotIn("显示更多历史", history_copy)

    def test_history_batches_always_include_running_and_waiting_sessions(self):
        live_state = self.window.get_current_session()
        live_state.session_status = "running"
        waiting_id = self.window.create_new_session(make_current=False)
        waiting_state = self.window.get_session(waiting_id)
        waiting_state.pending_interactions = {"request": object()}
        entries = [
            {
                "id": f"ordinary-{index}",
                "title": f"普通聊天 {index}",
                "updated_at": 100 - index,
            }
            for index in range(5)
        ]
        entries.extend(
            [
                {"id": live_state.session_id, "title": "后台运行", "updated_at": 2},
                {"id": waiting_id, "title": "等待输入", "updated_at": 1},
            ]
        )

        visible_ids = {
            item["id"]
            for item in self.window._visible_history_entries(entries, 3)
        }

        self.assertEqual(
            visible_ids,
            {
                "ordinary-0",
                "ordinary-1",
                "ordinary-2",
                live_state.session_id,
                waiting_id,
            },
        )
        live_state.live_activity = False
        waiting_state.pending_interactions = {}

    def test_unassigned_history_pages_by_three_and_search_preserves_page_state(self):
        conversations = [
            {
                "id": f"chat-page-{index}",
                "title": f"独立聊天 {index}",
                "updated_at": 100 - index,
                "status": "completed",
                "meta": {"workspace_source": "chat"},
                "im_provider": "",
            }
            for index in range(8)
        ]
        conversation_ids = [item["id"] for item in conversations]

        def disclosure_copy():
            return [
                button.text().strip()
                for button in self.window.history_disclosure_buttons.values()
            ]

        with patch.object(
            self.window.chat_storage,
            "list_conversations",
            return_value=conversations,
        ), patch.object(
            self.window.config_manager,
            "get_projects",
            return_value=[],
        ), patch.object(
            self.window.config_manager,
            "get",
            return_value=[],
        ):
            self.window.refresh_history_list()
            self.assertEqual(len(self.window.history_rows), 3)
            self.assertEqual(disclosure_copy(), ["展开显示"])

            self.window.expand_unassigned_history(len(conversations))
            self.assertEqual(len(self.window.history_rows), 6)
            self.assertEqual(disclosure_copy(), ["展开显示"])

            self.window._apply_runtime_theme()
            self.assertEqual(len(self.window.history_rows), 6)
            self.assertEqual(disclosure_copy(), ["展开显示"])
            self.assertEqual(self.window.unassigned_history_visible_limit, 6)

            with patch.object(
                self.window,
                "_history_query_text",
                return_value="独立聊天",
            ), patch.object(
                self.window.chat_storage,
                "search_conversations",
                return_value=conversation_ids,
            ):
                self.window.refresh_history_list()
            self.assertEqual(len(self.window.history_rows), 8)
            self.assertEqual(disclosure_copy(), [])
            self.assertEqual(self.window.unassigned_history_visible_limit, 6)

            self.window.refresh_history_list()
            self.assertEqual(len(self.window.history_rows), 6)
            self.assertEqual(disclosure_copy(), ["展开显示"])

            self.window.expand_unassigned_history(len(conversations))
            self.assertEqual(len(self.window.history_rows), 8)
            self.assertEqual(disclosure_copy(), ["收起显示"])

            self.window.collapse_unassigned_history(len(conversations))
            self.assertEqual(len(self.window.history_rows), 3)
            self.assertEqual(disclosure_copy(), ["展开显示"])

    def test_history_disclosure_keeps_its_viewport_anchor_after_expanding(self):
        conversations = [
            {
                "id": f"anchor-chat-{index}",
                "title": f"锚定聊天 {index}",
                "updated_at": 100 - index,
                "status": "completed",
                "meta": {"workspace_source": "chat"},
                "im_provider": "",
            }
            for index in range(9)
        ]
        with patch.object(
            self.window.chat_storage,
            "list_conversations",
            return_value=conversations,
        ), patch.object(
            self.window.config_manager,
            "get_projects",
            return_value=[],
        ), patch.object(
            self.window.config_manager,
            "get",
            return_value=[],
        ):
            self.window.resize(900, 300)
            self.window.show()
            self.window.refresh_history_list()
            self.app.processEvents()
            disclosure_key = self.window._history_disclosure_key("chat")
            button = self.window.history_disclosure_buttons[disclosure_key]
            self.window.history_scroll.ensureWidgetVisible(button, 0, 0)
            self.app.processEvents()
            viewport = self.window.history_scroll.viewport()
            before_y = button.mapTo(viewport, QPoint(0, 0)).y()
            before_scroll = self.window.history_scroll.verticalScrollBar().value()

            QTest.mouseClick(button, Qt.LeftButton)
            QTest.qWait(30)
            self.app.processEvents()

            replacement = self.window.history_disclosure_buttons[disclosure_key]
            after_y = replacement.mapTo(viewport, QPoint(0, 0)).y()
            after_bar = self.window.history_scroll.verticalScrollBar()
            self.assertLessEqual(
                abs(after_y - before_y),
                3,
                (
                    f"before_y={before_y}, after_y={after_y}, "
                    f"before_scroll={before_scroll}, after_scroll={after_bar.value()}, "
                    f"after_max={after_bar.maximum()}"
                ),
            )
            self.assertTrue(replacement.hasFocus())

    def test_project_preview_includes_live_conversation_outside_recent_five(self):
        with tempfile.TemporaryDirectory() as project_dir:
            live_id = self.window.create_new_session(
                make_current=False,
                workspace_dir=project_dir,
            )
            live_state = self.window.get_session(live_id)
            live_state.session_status = "running"
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
        state.session_status = "running"
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
