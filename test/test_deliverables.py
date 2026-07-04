import hashlib
import inspect
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QEventLoop, QIODevice, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStackedWidget, QTextEdit, QWidget

from main import (
    AgentModuleDialog,
    ChatBubble,
    EmptyStateWidget,
    OfficeDraftTaskCard,
    DeliverableWebPreview,
    MainWindow,
    OFFICE_OUTPUT_PROFILE_FREE,
    OFFICE_OUTPUT_PROFILE_PPT,
    PPT_AGENT_PREFERENCE_BUSINESS,
    PPT_AGENT_STRATEGY_AUTO,
    PPT_AGENT_STRATEGY_HUASHU,
    WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
    deliverable_preview_bootstrap_script,
    deliverable_preview_settle_script,
    load_qwebengine_view,
    scan_workspace_deliverables,
    is_auto_query_skill_context_message,
    session_history_ready,
    sidebar_symbol_icon,
)


class TestDeliverableScanning(unittest.TestCase):
    def test_sidebar_symbol_icons_render_expected_size(self):
        QApplication.instance() or QApplication([])
        for kind in ("folder", "folder-open", "folder-plus", "ellipsis", "plus"):
            pixmap = sidebar_symbol_icon(kind, "#4b5563", 16).pixmap(16, 16)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(pixmap.width(), 16)
            self.assertEqual(pixmap.height(), 16)

    def test_session_history_ready_requires_loaded_and_not_loading(self):
        state = type("State", (), {})()
        state.history_loaded = True
        state.history_loading = False
        self.assertTrue(session_history_ready(state))

        state.history_loading = True
        self.assertFalse(session_history_ready(state))

        state.history_loading = False
        state.history_loaded = False
        self.assertFalse(session_history_ready(state))
        self.assertFalse(session_history_ready(None))

    def test_auto_query_skill_context_detection_is_source_specific(self):
        for source in ("skill_prompt", "skill_prompt_query_match", "skill_prompt_tool_search"):
            self.assertTrue(
                is_auto_query_skill_context_message(
                    {
                        "role": "system",
                        "content": "auto matched skill prompt",
                        "meta": {
                            "kind": "skill_context",
                            "source": source,
                        },
                    }
                )
            )
        self.assertFalse(
            is_auto_query_skill_context_message(
                {
                    "role": "system",
                    "content": "user selected skill prompt",
                    "meta": {
                        "kind": "skill_context",
                        "source": "selected_skill",
                    },
                }
            )
        )
        self.assertFalse(is_auto_query_skill_context_message({"role": "user", "content": "hello"}))

    def test_merge_generated_messages_skips_auto_skill_contexts(self):
        window = MainWindow.__new__(MainWindow)
        existing = [{"role": "user", "content": "生成报告"}]
        generated = [
            {
                "role": "system",
                "content": "auto skill prompt",
                "meta": {"kind": "skill_context", "source": "skill_prompt_tool_search"},
            },
            {"role": "assistant", "content": "完成"},
        ]

        merged = MainWindow._merge_generated_messages(window, existing, generated)

        self.assertEqual(merged, [{"role": "assistant", "content": "完成"}])

    def test_normalize_session_ui_resets_guidance_label_when_idle(self):
        QApplication.instance() or QApplication([])
        state = type("State", (), {})()
        state.session_id = "session-idle"
        state.history_loaded = True
        state.history_loading = False
        state.llm_worker = None
        state.code_worker = None
        state.daemon_running = False
        state.turn_steerable = False
        state.selected_skill_names = []
        state.persisted_conversation_meta = {}
        state.workspace_dir = ""

        window = MainWindow.__new__(MainWindow)
        window.action_btn = QPushButton("引导")
        window.input_field = QTextEdit()
        window.tool_menu_btn = QPushButton()
        window.stop_btn = QPushButton()
        window.pause_btn = QPushButton()
        window.loop_hint = QLabel()
        window.refresh_selected_skill_controls = MagicMock()
        window.refresh_project_selector = MagicMock()
        window.refresh_context_badges = MagicMock()
        window.refresh_observability_view = MagicMock()
        window.update_skill_capture_button_state = MagicMock()

        MainWindow.normalize_session_ui(window, state)

        self.assertEqual(window.action_btn.text(), "开始")
        self.assertFalse(window.stop_btn.isVisible())

    def test_refresh_clarify_controls_does_not_reenter_context_badges(self):
        state = type("State", (), {"session_id": "session-1"})()
        window = MainWindow.__new__(MainWindow)
        window.current_session_id = "session-1"
        window.get_session = MagicMock(return_value=state)
        window.refresh_context_badges = MagicMock()

        MainWindow.refresh_clarify_controls(window, "session-1")

        window.refresh_context_badges.assert_not_called()

    def test_ppt_agent_observability_records_prompt_and_tool_events(self):
        state = type(
            "State",
            (),
            {
                "session_id": "session-1",
                "system_prompt_text": "",
                "runtime_context_text": "",
                "prompt_cache_meta": {},
                "system_prompt_appends": [],
                "observability_events": [],
            },
        )()
        window = MainWindow.__new__(MainWindow)
        window.current_session_id = "session-1"
        window.get_session = MagicMock(return_value=state)
        window.set_context_tab_hint = MagicMock()
        window.refresh_observability_view = MagicMock()
        window.refresh_context_badges = MagicMock()

        MainWindow.handle_observability_event(
            window,
            {
                "type": "system_prompt",
                "content": "stable prompt",
                "runtime_context": "策略 [PPT Agent]: 生成 HTML deliverable",
                "skill_contexts": [{"source": "ppt_agent", "content": "Huashu Design"}],
            },
            "session-1",
        )
        MainWindow.handle_observability_event(
            window,
            {"type": "tool_call", "name": "run_python_code", "id": "tool-1", "args": {"x": 1}},
            "session-1",
        )

        self.assertEqual(state.system_prompt_text, "stable prompt")
        self.assertIn("PPT Agent", state.runtime_context_text)
        self.assertEqual(state.system_prompt_appends[0]["source"], "ppt_agent")
        self.assertEqual([event["type"] for event in state.observability_events], ["system_prompt", "tool_call"])
        window.set_context_tab_hint.assert_called()
        window.refresh_observability_view.assert_called_with("session-1")

    def test_agent_bubble_builds_clickable_cards_for_workspace_deliverables(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as workspace:
            report = os.path.join(workspace, "季度报告.html")
            deck = os.path.join(workspace, "汇报.pptx")
            for path in (report, deck):
                with open(path, "wb") as handle:
                    handle.write(b"x")

            bubble = ChatBubble("Agent", "", workspace_dir=workspace)
            activated = []
            bubble.deliverablePathActivated.connect(activated.append)
            bubble.set_main_content(f"完成：{report}\n另一个版本：{deck}\n重复：{report}", final=True)
            app.processEvents()

            self.assertFalse(bubble.deliverable_cards.isHidden())
            self.assertEqual(bubble.deliverable_cards_layout.count(), 2)
            first_button = bubble.deliverable_cards_layout.itemAt(0).widget()
            self.assertEqual(first_button.text(), "季度报告.html")
            first_button.click()
            self.assertEqual(activated, [os.path.normpath(report)])

    def test_agent_bubble_builds_card_for_inline_code_delivery_path(self):
        app = QApplication.instance() or QApplication([])
        workspace = r"D:\code\数据分析测试"
        path = os.path.join(workspace, "html_test_output_20260625_225209.pptx")
        if not os.path.isfile(path):
            self.skipTest("Screenshot regression fixture is unavailable")

        bubble = ChatBubble("Agent", "", workspace_dir=workspace)
        bubble.set_main_content(f"主文件路径： `{path}` (35,331 字节)", final=True)
        app.processEvents()

        self.assertEqual(bubble.deliverable_cards_layout.count(), 1)
        self.assertEqual(
            bubble.deliverable_cards_layout.itemAt(0).widget().text(),
            "html_test_output_20260625_225209.pptx",
        )
        self.assertIn("cowork-file:", bubble.content_rich_edit.toHtml())

    def test_agent_bubble_ignores_non_workspace_deliverable_cards(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            external = os.path.join(outside, "external.pdf")
            with open(external, "wb") as handle:
                handle.write(b"x")

            bubble = ChatBubble("Agent", "", workspace_dir=workspace)
            bubble.set_main_content(f"文件：{external}", final=True)
            app.processEvents()

            self.assertFalse(bubble.deliverable_cards.isVisible())
            self.assertEqual(bubble.deliverable_cards_layout.count(), 0)

    def test_main_window_loads_deliverable_preferences_after_config_initialization(self):
        source = inspect.getsource(MainWindow.__init__)

        config_init = source.index("self.config_manager = ConfigManager()")
        layout_preference = source.index(
            'legacy_deliverable_layout_mode = self.config_manager.get("deliverable_layout_mode", "list")'
        )

        self.assertLess(config_init, layout_preference)

    def test_scans_supported_deliverables_sorted_by_modified_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_html = os.path.join(tmp, "report.html")
            new_pptx = os.path.join(tmp, "deck.pptx")
            ignored = os.path.join(tmp, "notes.txt")
            with open(old_html, "w", encoding="utf-8") as f:
                f.write("<!doctype html><html><body>Report</body></html>")
            with open(new_pptx, "wb") as f:
                f.write(b"pptx")
            with open(ignored, "w", encoding="utf-8") as f:
                f.write("ignore")
            now = time.time()
            os.utime(old_html, (now - 20, now - 20))
            os.utime(new_pptx, (now, now))

            items = scan_workspace_deliverables(tmp)

        self.assertEqual([item["name"] for item in items], ["deck.pptx", "report.html"])
        self.assertEqual(items[0]["kind"], "pptx")
        self.assertEqual(items[1]["kind"], "html")

    def test_skips_cache_and_build_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            visible = os.path.join(tmp, "page.html")
            hidden_dir = os.path.join(tmp, ".git")
            build_dir = os.path.join(tmp, "build")
            os.makedirs(hidden_dir)
            os.makedirs(build_dir)
            with open(visible, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with open(os.path.join(hidden_dir, "hidden.html"), "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with open(os.path.join(build_dir, "built.html"), "w", encoding="utf-8") as f:
                f.write("<html></html>")

            items = scan_workspace_deliverables(tmp)

        self.assertEqual([item["name"] for item in items], ["page.html"])

    def test_empty_state_replaces_report_card_with_office_deliverable_card(self):
        app = QApplication.instance() or QApplication([])
        class PromptBox:
            def setText(self, text):
                self.text = text

        class MainWindowStub:
            def __init__(self):
                self.input_field = PromptBox()
                self.workflow_mode = ""
                self.office_output_profile = ""
                self.opened_settings_page = None
                self.ppt_opened = False

            def open_settings(self, initial_page_label=None):
                self.opened_settings_page = initial_page_label

            def open_ppt_agent_mode(self):
                self.ppt_opened = True

        main_window = MainWindowStub()
        widget = EmptyStateWidget(main_window)
        try:
            titles = [item[0] for item in widget.actions_data]
            self.assertEqual(titles, ["PPT Agent", "📁 整理文件", "🖼️ 处理图片", "办公交付物"])
            self.assertIn("办公交付物", titles)
            self.assertNotIn("代码搜索", titles)
            self.assertNotIn("生成报告", titles)
            widget.action_cards[0].click()
            self.assertTrue(main_window.ppt_opened)
            office_card = next(item for item in widget.actions_data if item[0] == "办公交付物")
            self.assertEqual(office_card[1], "预览修改，再生成文件")
            self.assertIn("右侧交付物视图", office_card[2])
            self.assertIn("PPTX、DOCX 或 PDF", office_card[2])
            self.assertEqual(office_card[3], "fa5s.file-code")
            widget.action_cards[titles.index("办公交付物")].click()
            self.assertEqual(main_window.input_field.text, office_card[2])
            self.assertEqual(main_window.workflow_mode, "")
            self.assertEqual(main_window.office_output_profile, "")
            self.assertIsNotNone(widget.findChild(QPushButton, None))
            labels = [label.text() for label in widget.findChildren(QLabel)]
            self.assertIn("需要处理文档或数据？", labels)
            self.assertTrue(any("文档工具包" in text and "数据分析工具包" in text for text in labels))
            widget.toolkit_hint_button.click()
            self.assertEqual(main_window.opened_settings_page, "组件与依赖")
        finally:
            widget.deleteLater()

    def test_agent_module_exposes_builtin_ppt_agent_without_custom_profile_storage(self):
        app = QApplication.instance() or QApplication([])
        dialog = AgentModuleDialog(
            agent_profiles=[
                {"id": "agent-writer", "name": "写作助手", "description": "润色输出", "skill_names": []}
            ]
        )
        try:
            self.assertEqual(dialog.selected_builtin, "")
            self.assertIsNone(dialog.selected_profile)
            dialog.ppt_agent_button.click()
            self.assertEqual(dialog.selected_builtin, "ppt_agent")
            self.assertIsNone(dialog.selected_profile)
        finally:
            dialog.deleteLater()
            app.processEvents()

    def test_ppt_agent_request_submits_ppt_office_workflow(self):
        with tempfile.TemporaryDirectory() as workspace:
            source_path = os.path.join(workspace, "research.md")
            template_path = os.path.join(workspace, "template.pptx")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("# 研究材料")
            with open(template_path, "wb") as handle:
                handle.write(b"pptx")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "session-1", "messages": []})()
            window.get_session = MagicMock(return_value=state)
            window.get_current_session = MagicMock(return_value=state)
            window._ensure_session_workspace = MagicMock(return_value=workspace)
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            submitted = window.handle_ppt_agent_requested(
                "做一份高级感商业汇报",
                preference=PPT_AGENT_PREFERENCE_BUSINESS,
                strategy=PPT_AGENT_STRATEGY_AUTO,
                source_files=[source_path],
                template_file=template_path,
                session_id="session-1",
            )

            self.assertTrue(submitted)
            submit_call = window._submit_session_request.call_args
            self.assertIs(submit_call.args[0], state)
            self.assertIn("PPT Agent", submit_call.args[1])
            self.assertIn("HTML 工作稿", submit_call.args[1])
            self.assertIn(source_path, submit_call.args[1])
            self.assertIn(template_path, submit_call.args[1])
            self.assertEqual(submit_call.args[2], [source_path, template_path])
            self.assertEqual(submit_call.kwargs["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
            self.assertEqual(submit_call.kwargs["office_output_profile"], OFFICE_OUTPUT_PROFILE_PPT)
            self.assertTrue(submit_call.kwargs["ppt_agent_mode"])
            self.assertEqual(submit_call.kwargs["ppt_agent_selected_strategy"], PPT_AGENT_STRATEGY_HUASHU)
            window.add_system_toast.assert_called_once()

    def test_ppt_agent_request_accepts_source_file_without_manual_prompt(self):
        with tempfile.TemporaryDirectory() as workspace:
            source_path = os.path.join(workspace, "markDown1782479938589.md")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("# 会议资料")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "session-1", "messages": []})()
            window.get_session = MagicMock(return_value=state)
            window.get_current_session = MagicMock(return_value=state)
            window._ensure_session_workspace = MagicMock(return_value=workspace)
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            submitted = window.handle_ppt_agent_requested(
                "",
                preference=PPT_AGENT_PREFERENCE_BUSINESS,
                strategy=PPT_AGENT_STRATEGY_AUTO,
                source_files=[source_path],
                session_id="session-1",
            )

            self.assertTrue(submitted)
            submit_call = window._submit_session_request.call_args
            self.assertIn("请基于附加资料生成一份演示文稿 PPT 工作稿", submit_call.args[1])
            self.assertIn(os.path.basename(source_path), submit_call.args[1])
            self.assertEqual(submit_call.args[2], [source_path])
            self.assertEqual(submit_call.kwargs["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
            self.assertTrue(submit_call.kwargs["ppt_agent_mode"])

    def test_office_draft_request_submits_profiled_generation_prompt(self):
        with tempfile.TemporaryDirectory() as workspace:
            window = MainWindow.__new__(MainWindow)
            state = type(
                "_Session",
                (),
                {
                    "session_id": "session-1",
                    "messages": [{"id": "assistant-1", "role": "assistant", "content": "做 Agent 的行业观点"}],
                },
            )()
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            submitted = window.handle_office_draft_requested(
                OFFICE_OUTPUT_PROFILE_PPT,
                "assistant-1",
                "fallback",
                session_id="session-1",
            )

            self.assertTrue(submitted)
            submit_call = window._submit_session_request.call_args
            self.assertIs(submit_call.args[0], state)
            self.assertIn("PPT", submit_call.args[1])
            self.assertIn("做 Agent 的行业观点", submit_call.args[1])
            self.assertEqual(submit_call.kwargs["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
            self.assertEqual(submit_call.kwargs["office_output_profile"], OFFICE_OUTPUT_PROFILE_PPT)
            self.assertFalse(submit_call.kwargs["check_duplicates"])
            window.add_system_toast.assert_called_once()

    def test_office_draft_request_uses_chat_workspace_without_project(self):
        window = MainWindow.__new__(MainWindow)
        state = type("_Session", (), {"session_id": "session-1", "messages": []})()
        window.get_session = MagicMock(return_value=state)
        window._ensure_session_workspace = MagicMock(return_value="D:/app/conversation_workspaces/session-1")
        window._submit_session_request = MagicMock()
        window.add_system_toast = MagicMock()

        submitted = window.handle_office_draft_requested("ppt", "", "source", session_id="session-1")

        self.assertTrue(submitted)
        window._submit_session_request.assert_called_once()
        window.add_system_toast.assert_called_once()

    def test_conversion_continues_in_current_conversation_for_all_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            for target_format in ("pptx", "docx", "pdf"):
                with self.subTest(target_format=target_format):
                    window = MainWindow.__new__(MainWindow)
                    state = type("_Session", (), {"session_id": "current-session"})()
                    window.current_deliverable_path = html_path
                    window.workspace_dir = tmp
                    window._workspace_dir_for_state = MagicMock(return_value=tmp)
                    window.get_current_session = MagicMock(return_value=state)
                    window.create_new_session = MagicMock()
                    window._set_prompt_files = MagicMock()
                    window._submit_session_request = MagicMock(return_value=True)
                    window.add_system_toast = MagicMock()

                    window.start_deliverable_conversion(target_format)

                    window.create_new_session.assert_not_called()
                    self.assertEqual(state.selected_deliverable_path, html_path)
                    window._set_prompt_files.assert_called_once_with(
                        [html_path], session_id="current-session", refresh=True
                    )
                    submit_call = window._submit_session_request.call_args
                    self.assertIs(submit_call.args[0], state)
                    self.assertIn(f"生成 {target_format.upper()} 办公文件", submit_call.args[1])
                    self.assertEqual(submit_call.args[2], [html_path])
                    self.assertFalse(submit_call.kwargs["check_duplicates"])
                    self.assertTrue(submit_call.kwargs["clear_current_input"])
                    self.assertEqual(submit_call.kwargs["workflow_mode"], WORKFLOW_MODE_OFFICE_FILE_CONVERSION)
                    self.assertEqual(submit_call.kwargs["office_conversion_target"], target_format)
                    window.add_system_toast.assert_called_once_with(
                        f"已在当前对话中开始生成 {target_format.upper()}，你可以先收起面板继续工作。",
                        "info",
                        session_id="current-session",
                        auto_close_ms=3200,
                    )

    def test_pptx_conversion_can_attach_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            template_path = os.path.join(tmp, "template.pptx")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")
            with open(template_path, "wb") as f:
                f.write(b"pptx")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "current-session"})()
            window.current_deliverable_path = html_path
            window.workspace_dir = tmp
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=state)
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pptx", template_path=template_path)

            window._set_prompt_files.assert_called_once_with(
                [html_path, template_path], session_id="current-session", refresh=True
            )
            submit_call = window._submit_session_request.call_args
            self.assertIn(f"PPT 模板: {template_path}", submit_call.args[1])
            self.assertIn("主题、母版、字号、色彩和版式节奏", submit_call.args[1])
            self.assertIn("顶部和底部的图片元素", submit_call.args[1])
            self.assertEqual(submit_call.args[2], [html_path, template_path])

    def test_office_file_conversion_run_messages_are_isolated_from_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>Report</body></html>")
            window = MainWindow.__new__(MainWindow)
            window._normalize_prompt_file_paths = lambda paths: [
                os.path.normpath(path) for path in paths if os.path.isfile(path)
            ]
            state = type(
                "_Session",
                (),
                {
                    "office_conversion_source_files": [html_path],
                    "office_conversion_template_file": "",
                    "office_task_target_format": "pptx",
                    "messages": [
                        {
                            "role": "user",
                            "content": "请基于下面这段 AI 回复，生成一份自由类型的可预览办公稿",
                            "meta": {"workflow_mode": WORKFLOW_MODE_OFFICE_HTML_FIRST},
                        },
                        {"role": "assistant", "content": "已生成 HTML"},
                        {
                            "role": "user",
                            "content": f"请读取本轮附加的源 HTML 文件并生成 PPTX。\n- 源 HTML 文件: {html_path}",
                            "content_parts": [
                                {"type": "text", "text": "请读取本轮附加的源 HTML 文件并生成 PPTX。"},
                                {"type": "input_file", "path": os.path.normpath(html_path), "name": "report.html"},
                            ],
                            "meta": {
                                "workflow_mode": WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
                                "office_conversion_target": "pptx",
                            },
                        },
                    ],
                },
            )()

            messages = window._office_file_conversion_run_messages(state)

            joined = "\n".join(str(message.get("content") or "") for message in messages)
            self.assertEqual(len(messages), 2)
            self.assertNotIn("下面这段 AI 回复", joined)
            self.assertIn(os.path.normpath(html_path), joined)
            self.assertEqual(messages[-1]["content_parts"][1]["type"], "input_file")
            self.assertEqual(messages[-1]["content_parts"][1]["path"], os.path.normpath(html_path))

    def test_worker_messages_use_isolated_context_for_office_file_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            window = MainWindow.__new__(MainWindow)
            window._normalize_prompt_file_paths = lambda paths: [
                os.path.normpath(path) for path in paths if os.path.isfile(path)
            ]
            state = type(
                "_Session",
                (),
                {
                    "office_conversion_source_files": [html_path],
                    "office_conversion_template_file": "",
                    "office_task_target_format": "pptx",
                    "messages": [
                        {"role": "user", "content": "旧的办公稿生成请求"},
                        {
                            "role": "user",
                            "content": "生成 PPTX",
                            "content_parts": [
                                {"type": "text", "text": "生成 PPTX"},
                                {"type": "input_file", "path": os.path.normpath(html_path), "name": "report.html"},
                            ],
                            "meta": {
                                "workflow_mode": WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
                                "office_conversion_target": "pptx",
                            },
                        },
                    ],
                },
            )()

            messages = window._messages_for_worker(
                state,
                {"workflow_mode": WORKFLOW_MODE_OFFICE_FILE_CONVERSION},
            )

            self.assertEqual(len(messages), 2)
            self.assertNotIn("旧的办公稿生成请求", "\n".join(message.get("content", "") for message in messages))

    def test_chat_file_link_opens_deliverable_in_focus_mode(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = os.path.join(workspace, "报告.doc")
            with open(path, "wb") as handle:
                handle.write(b"doc")
            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"selected_deliverable_path": ""})()
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window.show_context_drawer = MagicMock()
            window.select_deliverable = MagicMock()
            window.add_system_toast = MagicMock()

            window.open_deliverable_from_chat(path, "session-1")

            self.assertEqual(state.selected_deliverable_path, os.path.normpath(path))
            self.assertEqual(window.file_workspace_view_mode, "detail")
            self.assertEqual(window.file_workspace_route_origin, "chat")
            window.show_context_drawer.assert_called_once_with(window.RIGHT_TAB_DELIVERABLES)
            window.select_deliverable.assert_called_once_with(os.path.normpath(path), render_html=True)
            window.add_system_toast.assert_not_called()

    def test_open_deliverables_view_follows_latest_new_chat_path(self):
        with tempfile.TemporaryDirectory() as workspace:
            first = os.path.join(workspace, "first.html")
            latest = os.path.join(workspace, "latest.pdf")
            for path in (first, latest):
                with open(path, "wb") as handle:
                    handle.write(b"x")
            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"selected_deliverable_path": first})()
            window.current_session_id = "session-1"
            window.right_drawer_open = True
            window.right_drawer_tab = window.RIGHT_TAB_FILES
            window.file_workspace_section = window.FILE_SECTION_DELIVERABLES
            window.current_deliverable_path = first
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window.select_deliverable = MagicMock()

            window.handle_chat_deliverable_paths_changed([first, latest], "session-1")

            self.assertEqual(state.selected_deliverable_path, os.path.normpath(latest))
            self.assertEqual(window.file_workspace_view_mode, "detail")
            self.assertEqual(window.file_workspace_route_origin, "chat")
            window.select_deliverable.assert_called_once_with(os.path.normpath(latest), render_html=True)

    def test_file_rail_entry_opens_browse_view(self):
        class Stack:
            def __init__(self):
                self.current = None

            def setCurrentWidget(self, widget):
                self.current = widget

        window = MainWindow.__new__(MainWindow)
        window.file_workspace_view_mode = "detail"
        window.file_workspace_route_origin = "chat"
        window.file_workspace_return_section = window.FILE_SECTION_DELIVERABLES
        window.file_workspace_section = window.FILE_SECTION_DELIVERABLES
        window.file_workspace_stack = Stack()
        window.file_browse_page = object()
        window.file_detail_page = object()
        window.config_manager = MagicMock()
        window.show_context_drawer = MagicMock()

        window.open_file_workspace_from_rail()

        self.assertEqual(window.file_workspace_view_mode, "browse")
        self.assertEqual(window.file_workspace_route_origin, "browse")
        self.assertIs(window.file_workspace_stack.current, window.file_browse_page)
        window.show_context_drawer.assert_called_once_with(window.RIGHT_TAB_FILES)

    def test_deliverable_section_does_not_show_unmounted_legacy_buttons(self):
        class Stack:
            def setCurrentIndex(self, index):
                self.index = index

            def setVisible(self, visible):
                self.visible = visible

        window = MainWindow.__new__(MainWindow)
        window.file_source_stack = Stack()
        window.file_section_buttons = {}
        window.file_workspace_view_mode = "browse"
        window.deliverable_layout_btn = MagicMock()
        window.deliverable_render_btn = MagicMock()
        window.deliverables_refresh_btn = MagicMock()
        window.deliverable_expand_btn = MagicMock()
        window._sync_deliverable_action_visibility = MagicMock()
        window.refresh_deliverables = MagicMock()
        window.update_context_drawer_header = MagicMock()

        window.set_file_workspace_section(window.FILE_SECTION_DELIVERABLES, refresh=False)

        window.deliverable_layout_btn.setVisible.assert_not_called()
        window.deliverable_render_btn.setVisible.assert_not_called()
        window.deliverables_refresh_btn.setVisible.assert_not_called()
        window.deliverable_expand_btn.setVisible.assert_called_once_with(True)

    def test_chat_path_does_not_follow_when_deliverables_view_is_closed(self):
        with tempfile.TemporaryDirectory() as workspace:
            latest = os.path.join(workspace, "latest.html")
            with open(latest, "wb") as handle:
                handle.write(b"x")
            window = MainWindow.__new__(MainWindow)
            window.current_session_id = "session-1"
            window.right_drawer_open = False
            state = type("_Session", (), {"office_draft_preview_pending": False})()
            window.get_session = MagicMock(return_value=state)
            window.select_deliverable = MagicMock()

            window.handle_chat_deliverable_paths_changed([latest], "session-1")

            window.get_session.assert_called_once_with("session-1")
            window.select_deliverable.assert_not_called()

    def test_office_draft_opens_deliverables_view_for_new_chat_path(self):
        with tempfile.TemporaryDirectory() as workspace:
            latest = os.path.join(workspace, "latest.html")
            with open(latest, "wb") as handle:
                handle.write(b"x")
            window = MainWindow.__new__(MainWindow)
            window.current_session_id = "session-1"
            window.right_drawer_open = False
            window.current_deliverable_path = ""
            state = type(
                "_Session",
                (),
                {
                    "office_draft_preview_pending": True,
                    "selected_deliverable_path": "",
                },
            )()
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window.set_file_workspace_section = MagicMock()
            window.show_context_drawer = MagicMock()
            window.select_deliverable = MagicMock()

            window.handle_chat_deliverable_paths_changed([latest], "session-1")

            self.assertEqual(state.selected_deliverable_path, os.path.normpath(latest))
            self.assertEqual(window.file_workspace_view_mode, "detail")
            self.assertEqual(window.file_workspace_route_origin, "chat")
            window.set_file_workspace_section.assert_called_once_with(window.FILE_SECTION_DELIVERABLES, refresh=False)
            window.show_context_drawer.assert_called_once_with(window.RIGHT_TAB_FILES)
            window.select_deliverable.assert_called_once_with(os.path.normpath(latest), render_html=True)

    def test_office_task_card_syncs_path_after_preview_pending_is_cleared(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as workspace:
            latest = os.path.join(workspace, "latest.html")
            with open(latest, "wb") as handle:
                handle.write(b"x")
            card = OfficeDraftTaskCard("自由")
            window = MainWindow.__new__(MainWindow)
            window.current_session_id = "session-1"
            window.right_drawer_open = False
            window.current_deliverable_path = latest
            state = type(
                "_Session",
                (),
                {
                    "office_draft_preview_pending": False,
                    "office_draft_task_card": card,
                    "office_task_result_paths": [],
                    "selected_deliverable_path": "",
                },
            )()
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window._apply_deliverable_layout_mode = MagicMock()
            window.select_deliverable = MagicMock()

            try:
                window.handle_chat_deliverable_paths_changed([latest], "session-1")
                app.processEvents()

                self.assertEqual(card.result_layout.count(), 1)
                self.assertFalse(card.result_container.isHidden())
                window._apply_deliverable_layout_mode.assert_not_called()
                window.select_deliverable.assert_not_called()
            finally:
                card.deleteLater()
                app.processEvents()

    def test_office_task_card_process_placeholder_hides_when_process_content_arrives(self):
        app = QApplication.instance() or QApplication([])
        card = OfficeDraftTaskCard("PPT")
        try:
            self.assertEqual(card._running_title(), "正在生成PPT文稿")
            self.assertEqual(card.process_widget_count(), 0)
            card.set_process_visible(True)
            self.assertFalse(card.process_placeholder.isHidden())

            process_widget = QLabel("生成过程")
            card.add_process_widget(process_widget)

            self.assertEqual(card.process_widget_count(), 1)
            self.assertTrue(card.process_placeholder.isHidden())
        finally:
            card.deleteLater()
            app.processEvents()

    def test_deliverable_conversion_running_state_is_local_to_action_bar(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window.file_workspace_view_mode = "detail"
            window.deliverable_conversion_status_label = QLabel()
            window.deliverable_conversion_cancel_btn = QPushButton("取消")
            pptx_btn = QPushButton("生成 PPTX")
            pptx_btn.setProperty("conversionTarget", "pptx")
            docx_btn = QPushButton("生成 DOCX")
            docx_btn.setProperty("conversionTarget", "docx")
            window.deliverable_convert_buttons = [pptx_btn, docx_btn]
            window.deliverable_conversion_row = QWidget()

            window._set_deliverable_conversion_running("pptx")

            self.assertEqual(window.deliverable_conversion_running_target, "pptx")
            self.assertFalse(window.deliverable_conversion_status_label.isHidden())
            self.assertIn("PPTX", window.deliverable_conversion_status_label.text())
            self.assertFalse(window.deliverable_conversion_cancel_btn.isHidden())
            self.assertEqual(pptx_btn.text(), "正在生成 PPTX...")
            self.assertTrue(pptx_btn.isEnabled())
            self.assertFalse(docx_btn.isEnabled())

            window._set_deliverable_conversion_running("")

            self.assertEqual(window.deliverable_conversion_running_target, "")
            self.assertEqual(pptx_btn.text(), "生成 PPTX")
            self.assertEqual(docx_btn.text(), "生成 DOCX")

    def test_conversion_keeps_html_in_current_conversation_when_submission_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "current-session"})()
            window.current_deliverable_path = html_path
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=state)
            window.create_new_session = MagicMock()
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=False)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pdf")

            window.create_new_session.assert_not_called()
            self.assertEqual(state.selected_deliverable_path, html_path)
            self.assertIn("HTML 已保留在当前对话中", window.add_system_toast.call_args.args[0])
            self.assertEqual(window.add_system_toast.call_args.args[1], "warning")

    def test_conversion_reports_when_current_conversation_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=None)
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock()
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("docx")

            window._set_prompt_files.assert_not_called()
            window._submit_session_request.assert_not_called()
            window.add_system_toast.assert_called_once_with(
                "当前没有可继续的对话，请先新建对话。",
                "warning",
                auto_close_ms=3200,
            )

    def test_render_uses_cache_busting_local_url(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window.current_deliverable_stale = True
            window.deliverable_web_view = QWidget()
            window.deliverable_web_view.setUrl = MagicMock()
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.deliverable_web_view)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            rendered_url = window.deliverable_web_view.setUrl.call_args.args[0]
            self.assertTrue(rendered_url.isLocalFile())
            self.assertEqual(os.path.normcase(rendered_url.toLocalFile()), os.path.normcase(html_path))
            self.assertIn("cowork_refresh=", rendered_url.query())
            self.assertFalse(window.current_deliverable_stale)
            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_web_view)

    def test_render_reuses_unchanged_html(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window.current_deliverable_stale = False
            window.deliverable_render_path = html_path
            window.deliverable_render_fingerprint = window._deliverable_fingerprint(html_path)
            window.deliverable_web_view = QWidget()
            window.deliverable_web_view.setUrl = MagicMock()
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.deliverable_web_view)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            window.deliverable_web_view.setUrl.assert_not_called()
            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_web_view)

    def test_office_deliverable_uses_builtin_structured_preview(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "report.docx")
            with open(docx_path, "wb") as handle:
                handle.write(b"docx")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = docx_path
            window.current_deliverable_stale = True
            window.deliverable_render_fingerprint = None
            window.deliverable_render_path = ""
            window.deliverable_web_view = QWidget()
            window.deliverable_web_view.setHtml = MagicMock()
            window.deliverable_web_preview = window.deliverable_web_view
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.deliverable_web_view)
            window.deliverable_status_label = QLabel()

            with patch(
                "main.render_structured_document_preview",
                return_value={"format": "DOCX", "html": "<html>preview</html>", "text": "preview"},
            ) as preview_mock:
                window.render_selected_deliverable()

            preview_mock.assert_called_once_with(docx_path)
            window.deliverable_web_view.setHtml.assert_called_once()
            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_web_view)
            self.assertIn("内置预览", window.deliverable_status_label.text())

    def test_image_deliverable_renders_in_preview_label(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, "chart.png")
            pixmap = QPixmap(24, 16)
            pixmap.fill(QColor("#007aff"))
            self.assertTrue(pixmap.save(image_path, "PNG"))

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = image_path
            window.current_deliverable_stale = True
            window.deliverable_render_fingerprint = None
            window.deliverable_render_path = ""
            window.preview_image = QLabel()
            window.preview_pixmap = None
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.resize(320, 240)
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.preview_image)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.preview_image)
            self.assertFalse(window.preview_image.pixmap().isNull())
            self.assertFalse(window.current_deliverable_stale)
            self.assertIn("图片", window.deliverable_status_label.text())

    def test_invalid_image_deliverable_reports_decode_failure(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, "broken.png")
            with open(image_path, "wb") as handle:
                handle.write(b"not an image")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = image_path
            window.preview_image = QLabel()
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.preview_image)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_text_preview)
            self.assertIn("无法解码图片文件", window.deliverable_text_preview.toPlainText())
            self.assertEqual("图片预览失败。", window.deliverable_status_label.text())

    def test_pdf_view_is_bound_to_document_before_preview(self):
        app = QApplication.instance() or QApplication([])

        class FakePdfDocument:
            def __init__(self, parent=None):
                self.parent = parent

        class FakePdfView(QWidget):
            class PageMode:
                MultiPage = object()

            class ZoomMode:
                FitToWidth = object()

            def __init__(self):
                super().__init__()
                self.bound_document = None

            def setDocument(self, document):
                self.bound_document = document

            def setPageMode(self, mode):
                self.page_mode = mode

            def setZoomMode(self, mode):
                self.zoom_mode = mode

        window = MainWindow.__new__(MainWindow)
        window.deliverable_pdf_view = None
        window.deliverable_pdf_document = None
        window.deliverable_preview_stack = QStackedWidget()

        with patch("main.load_qpdf_classes", return_value=(FakePdfDocument, FakePdfView)):
            view = window._ensure_deliverable_pdf_view()

        self.assertIs(view.bound_document, window.deliverable_pdf_document)
        self.assertEqual(window.deliverable_preview_stack.indexOf(view), 0)

    def test_light_preview_scripts_throttle_continuous_rendering(self):
        bootstrap = deliverable_preview_bootstrap_script()
        settle = deliverable_preview_settle_script()

        self.assertIn("requestAnimationFrame", bootstrap)
        self.assertIn("Math.max(100", bootstrap)
        self.assertIn("animation:none", bootstrap)
        self.assertIn("MutationObserver", bootstrap)
        self.assertIn("__coworkScrollWheelAt", bootstrap)
        self.assertIn("__coworkPreviewMetrics", bootstrap)
        self.assertNotIn("addEventListener('wheel'", bootstrap)
        self.assertIn("document.scrollingElement", bootstrap)
        self.assertIn("getAnimations", settle)
        self.assertIn("media.pause", settle)

    def test_light_preview_qt_input_and_scrollbars_scroll_both_axes(self):
        webengine_view_cls = load_qwebengine_view()
        if webengine_view_cls is None:
            self.skipTest("QtWebEngine is unavailable")
        app = QApplication.instance() or QApplication([])
        view = webengine_view_cls()
        view.resize(320, 240)
        window = MainWindow.__new__(MainWindow)
        window.deliverable_web_view = view
        window._configure_deliverable_web_view()
        preview = DeliverableWebPreview(view)
        preview.resize(320, 240)
        preview.show()

        loaded = []
        load_loop = QEventLoop()
        view.loadFinished.connect(lambda ok: (loaded.append(bool(ok)), load_loop.quit()))
        view.setHtml(
            "<!doctype html><html><body style='margin:0;width:1600px;height:1600px'>"
            "<div id='nested' style='position:absolute;left:0;top:0;width:100px;height:100px;overflow:auto'>"
            "<div style='width:400px;height:500px'>nested</div></div>preview</body></html>"
        )
        QTimer.singleShot(5000, load_loop.quit)
        load_loop.exec()
        self.assertEqual(loaded, [True])

        def evaluate(script):
            result = []
            loop = QEventLoop()
            view.page().runJavaScript(script, lambda value: (result.append(value), loop.quit()))
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertTrue(result, f"JavaScript callback timed out: {script}")
            return result[0]

        def render_digest():
            payload = QByteArray()
            buffer = QBuffer(payload)
            buffer.open(QIODevice.WriteOnly)
            self.assertTrue(view.grab().save(buffer, "PNG"))
            return hashlib.sha256(bytes(payload)).hexdigest()

        routing_target = QWidget()
        routing_target.setObjectName("UnrelatedWheelTarget")
        outside_wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(view.mapToGlobal(QPoint(-20, -20))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, outside_wheel)
        QApplication.processEvents()
        self.assertEqual(evaluate("document.scrollingElement.scrollTop"), 0)
        control_wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.ControlModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, control_wheel)
        QApplication.processEvents()
        self.assertEqual(evaluate("document.scrollingElement.scrollTop"), 0)
        before_wheel_digest = render_digest()
        wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, wheel)
        QApplication.processEvents()
        wheel_loop = QEventLoop()
        QTimer.singleShot(100, wheel_loop.quit)
        wheel_loop.exec()
        self.assertGreater(evaluate("document.scrollingElement.scrollTop"), 0)
        paint_loop = QEventLoop()
        QTimer.singleShot(200, paint_loop.quit)
        paint_loop.exec()
        self.assertNotEqual(render_digest(), before_wheel_digest)
        evaluate("window.scrollTo(0,0)")
        horizontal_wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.ShiftModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, horizontal_wheel)
        QApplication.processEvents()
        horizontal_loop = QEventLoop()
        QTimer.singleShot(100, horizontal_loop.quit)
        horizontal_loop.exec()
        self.assertGreater(evaluate("document.scrollingElement.scrollLeft"), 0)
        evaluate("window.scrollTo(0,0)")
        nested_wheel = QWheelEvent(
            QPointF(50, 50),
            QPointF(view.mapToGlobal(QPoint(50, 50))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, nested_wheel)
        QApplication.processEvents()
        self.assertGreater(evaluate("document.getElementById('nested').scrollTop"), 0)
        preview.schedule_scrollbar_sync()
        QApplication.processEvents()
        evaluate("0")
        expected_vertical_max = int(evaluate(
            "document.scrollingElement.scrollHeight-document.scrollingElement.clientHeight"
        ))
        expected_horizontal_max = int(evaluate(
            "document.scrollingElement.scrollWidth-document.scrollingElement.clientWidth"
        ))
        self.assertEqual(preview.vertical_scrollbar.maximum(), expected_vertical_max)
        self.assertEqual(preview.horizontal_scrollbar.maximum(), expected_horizontal_max)
        preview.vertical_scrollbar.setValue(preview.vertical_scrollbar.maximum())
        preview.horizontal_scrollbar.setValue(preview.horizontal_scrollbar.maximum())
        QApplication.processEvents()
        self.assertAlmostEqual(
            evaluate("document.scrollingElement.scrollTop"),
            preview.vertical_scrollbar.maximum(),
            delta=1,
        )
        self.assertAlmostEqual(
            evaluate("document.scrollingElement.scrollLeft"),
            preview.horizontal_scrollbar.maximum(),
            delta=1,
        )
        QApplication.instance().removeEventFilter(preview)
        preview.close()


if __name__ == "__main__":
    unittest.main()
