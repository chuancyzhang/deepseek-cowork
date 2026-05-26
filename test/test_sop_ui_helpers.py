import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import create_sop_run, mark_step_awaiting_confirmation
from main import QApplication, MainWindow, SubAgentEventSummaryRow, SubAgentEventTile, SubAgentMonitor
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QScrollArea, QWidget


class _State:
    def __init__(self, sop_run=None):
        self.sop_run = sop_run


class _AgentUiState:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.sub_agent_events = []
        self.sub_agent_render_queued = False
        self.tool_cards = {}
        self.last_agent_bubble = None
        self.llm_worker = None
        self.daemon_running = True
        self.sop_run = None


class _ObservabilityState:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.system_prompt_text = ""
        self.system_prompt_appends = []
        self.observability_events = []


class _MousePressEventStub:
    def __init__(self, global_pos):
        self._global_pos = global_pos

    def type(self):
        return QEvent.MouseButtonPress

    def globalPos(self):
        return self._global_pos


class TestSopUiHelpers(unittest.TestCase):
    def test_prompt_tool_menu_order_helper_matches_spec(self):
        window = MainWindow.__new__(MainWindow)
        entries = window._prompt_tool_menu_entries()
        self.assertEqual(
            [label for _key, label in entries],
            ["添加文件", "添加智能体", "添加自动化", "从对话生成 SOP", "指定能力", "反问模式"],
        )

    def test_should_block_send_for_sop_only_when_awaiting_confirmation(self):
        window = MainWindow.__new__(MainWindow)
        active_run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "steps": [{"title": "Step 1"}],
            }
        )
        awaiting_run = mark_step_awaiting_confirmation(active_run, {"finished_at": 1})

        self.assertFalse(window._should_block_send_for_sop(_State(active_run)))
        self.assertTrue(window._should_block_send_for_sop(_State(awaiting_run)))

    def test_start_conversation_sop_flow_requires_messages(self):
        window = MainWindow.__new__(MainWindow)
        window.conversation_sop_worker = None
        window.get_current_session = MagicMock(return_value=type("_Session", (), {"messages": []})())
        window.add_system_toast = MagicMock()

        window.start_conversation_sop_flow()

        window.add_system_toast.assert_called_once()
        self.assertIn("没有可提炼 SOP", window.add_system_toast.call_args.args[0])

    def test_start_conversation_sop_flow_skips_when_worker_running(self):
        window = MainWindow.__new__(MainWindow)
        worker = MagicMock()
        worker.isRunning.return_value = True
        window.conversation_sop_worker = worker
        window.add_system_toast = MagicMock()

        window.start_conversation_sop_flow()

        window.add_system_toast.assert_called_once()
        self.assertIn("正在生成中", window.add_system_toast.call_args.args[0])

    def test_save_conversation_sop_draft_saves_template_and_binds_run(self):
        window = MainWindow.__new__(MainWindow)
        state = type("_Session", (), {"session_id": "session-1", "sop_run": None})()
        stored_templates = []

        config_manager = MagicMock()
        config_manager.get_sop_templates.side_effect = lambda: list(stored_templates)

        def set_templates(templates):
            stored_templates[:] = templates

        config_manager.set_sop_templates.side_effect = set_templates
        window.config_manager = config_manager
        window.save_chat_history = MagicMock()
        window.refresh_sop_controls = MagicMock()
        window.refresh_context_badges = MagicMock()
        window.show_context_drawer = MagicMock()
        window.add_system_toast = MagicMock()
        window.RIGHT_TAB_SOP = MainWindow.RIGHT_TAB_SOP

        ok = window._save_conversation_sop_draft(
            state,
            {
                "name": "对话 SOP",
                "description": "从当前对话提炼",
                "steps": [{"title": "整理流程", "instructions": "输出完整 SOP"}],
            },
        )

        self.assertTrue(ok)
        self.assertEqual(stored_templates[0]["name"], "对话 SOP")
        self.assertIsNotNone(state.sop_run)
        self.assertEqual(state.sop_run["template_name"], "对话 SOP")
        config_manager.set_sop_templates.assert_called_once()
        window.save_chat_history.assert_called_once_with(session_id="session-1")
        window.show_context_drawer.assert_called_once_with(MainWindow.RIGHT_TAB_SOP)

    def test_add_prompt_files_loads_workspace_and_tracks_attachments(self):
        class _InputField:
            def __init__(self, text=""):
                self._text = text
                self.focused = False
                self.cursor_moved = False
                self.cursor_visible = False
                self.height_adjusted = False

            def toPlainText(self):
                return self._text

            def setPlainText(self, text):
                self._text = text

            def moveCursor(self, *_args):
                self.cursor_moved = True

            def ensureCursorVisible(self):
                self.cursor_visible = True

            def setFocus(self):
                self.focused = True

            def adjustHeight(self):
                self.height_adjusted = True

        window = MainWindow.__new__(MainWindow)
        window.input_field = _InputField("已有说明")
        window.workspace_dir = None
        window.current_session_id = "session-1"
        window.sessions = {"session-1": type("_State", (), {"prompt_files": []})()}
        loaded_dirs = []
        hint_calls = []
        window.refresh_prompt_file_chips = MagicMock()
        window.load_workspace = lambda path: loaded_dirs.append(path)
        window.set_context_tab_hint = lambda tab, available=True: hint_calls.append((tab, available))

        with tempfile.TemporaryDirectory() as temp_dir:
            file_a = os.path.join(temp_dir, "a.txt")
            file_b = os.path.join(temp_dir, "b.txt")
            for path in (file_a, file_b):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")

            added = window._add_prompt_files([file_a, file_b])

        self.assertEqual(added, [os.path.normpath(file_a), os.path.normpath(file_b)])
        self.assertEqual(loaded_dirs, [temp_dir])
        self.assertEqual(window.input_field.toPlainText(), "已有说明")
        self.assertEqual(
            window.sessions["session-1"].prompt_files,
            [os.path.normpath(file_a), os.path.normpath(file_b)],
        )
        self.assertEqual(hint_calls, [(window.RIGHT_TAB_FILES, True)])
        window.refresh_prompt_file_chips.assert_called()
        self.assertTrue(window.input_field.focused)
        self.assertTrue(window.input_field.cursor_moved)
        self.assertTrue(window.input_field.cursor_visible)
        self.assertTrue(window.input_field.height_adjusted)

    def test_build_user_message_payload_marks_user_added_files(self):
        window = MainWindow.__new__(MainWindow)

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "AI 赋能数据分析.docx")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("x")

            payload = window._build_user_message_payload("整理下这篇文章", [file_path])

        self.assertIn("[用户添加的文件]", payload["content"])
        self.assertIn("整理下这篇文章", payload["content"])
        self.assertEqual(payload["display_content"], "整理下这篇文章")
        self.assertEqual(payload["attachments"][0]["name"], "AI 赋能数据分析.docx")
        self.assertEqual(payload["meta"]["display_content"], "整理下这篇文章")
        self.assertEqual(payload["meta"]["user_added_files"], [os.path.normpath(file_path)])
        self.assertEqual(payload["content_parts"][0], {"type": "text", "text": "整理下这篇文章"})
        self.assertEqual(payload["content_parts"][1]["type"], "input_file")
        self.assertEqual(payload["content_parts"][1]["path"], os.path.normpath(file_path))

    def test_select_files_for_prompt_forwards_dialog_selection(self):
        window = MainWindow.__new__(MainWindow)
        window.workspace_dir = ""
        window.config_manager = MagicMock()
        window.config_manager.get.return_value = ""
        window._add_prompt_files = MagicMock()

        with patch("main.QFileDialog.getOpenFileNames", return_value=(["C:\\demo.txt"], "所有文件 (*.*)")):
            window.select_files_for_prompt()

        window._add_prompt_files.assert_called_once_with(["C:\\demo.txt"])

    def test_sub_agent_state_without_bubble_does_not_auto_open_drawer(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        hints = []
        window._agent_state_ui_event_seq = 0
        window.current_session_id = state.session_id
        window.right_drawer_open = False
        window.right_drawer_tab = window.RIGHT_TAB_FILES
        window.get_session = lambda session_id=None: state
        window.set_session_phase = MagicMock()
        window.set_context_tab_hint = lambda tab, available=True: hints.append((tab, available))
        window.show_context_drawer = MagicMock()

        window._handle_agent_state_ui(
            {
                "agent_id": "agent-1",
                "agent_name": "worker",
                "status": "pending",
                "task": "Starting task",
            },
            state.session_id,
        )

        window.show_context_drawer.assert_not_called()
        self.assertEqual(len(state.sub_agent_events), 1)
        self.assertEqual(hints[-1], (window.RIGHT_TAB_SUB_AGENTS, True))
        self.assertFalse(state.sub_agent_render_queued)

    def test_sub_agent_delta_events_merge_before_render(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        hints = []
        window.current_session_id = state.session_id
        window.set_context_tab_hint = lambda tab, available=True: hints.append((tab, available))

        window._record_sub_agent_event(
            state,
            {"agent_id": "agent-1", "status": "content", "content_delta": "hello "},
            "hello ",
        )
        window._record_sub_agent_event(
            state,
            {"agent_id": "agent-1", "status": "content", "content_delta": "world"},
            "world",
        )

        self.assertEqual(len(state.sub_agent_events), 1)
        self.assertEqual(state.sub_agent_events[0]["content"], "hello world")
        self.assertEqual(state.sub_agent_events[0]["content_delta"], "hello world")
        self.assertIn((window.RIGHT_TAB_SUB_AGENTS, True), hints)

    def test_observability_event_handles_long_payload_without_refresh_crash(self):
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        hints = []
        window.current_session_id = state.session_id
        window.get_session = lambda session_id=None: state
        window.set_context_tab_hint = lambda tab, available=True: hints.append((tab, available))
        window.refresh_observability_view = MagicMock()
        window.refresh_context_badges = MagicMock()

        window.handle_observability_event(
            {
                "type": "tool_result",
                "name": "huge_tool",
                "result": "x" * 100000,
            },
            state.session_id,
        )
        window.handle_observability_event("not-a-dict", state.session_id)

        self.assertEqual(len(state.observability_events), 1)
        self.assertEqual(hints[-1], (window.RIGHT_TAB_OBSERVABILITY, True))
        window.refresh_observability_view.assert_called_once_with(state.session_id)

    def test_safe_text_preview_truncates_large_observability_payload(self):
        from main import _safe_text_preview

        preview = _safe_text_preview("x" * 100, limit=12)

        self.assertTrue(preview.startswith("x" * 12))
        self.assertIn("truncated", preview)

    def test_show_context_drawer_refreshes_observability_safely(self):
        window = MainWindow.__new__(MainWindow)
        window.RIGHT_TAB_OBSERVABILITY = MainWindow.RIGHT_TAB_OBSERVABILITY
        window.RIGHT_TAB_SUB_AGENTS = MainWindow.RIGHT_TAB_SUB_AGENTS
        window.right_drawer_tab = window.RIGHT_TAB_FILES
        window.right_drawer_open = False
        window.current_session_id = "session-1"
        window.right_stack = MagicMock()
        window.right_stack.count.return_value = 4
        window.right_sidebar = MagicMock()
        window.refresh_observability_view = MagicMock()
        window.update_context_drawer_header = MagicMock()
        window.sync_context_drawer_layout = MagicMock()
        window.update_context_rail_badges = MagicMock()

        window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)

        window.refresh_observability_view.assert_called_once_with("session-1")
        window.right_stack.setCurrentIndex.assert_called_once_with(window.RIGHT_TAB_OBSERVABILITY)
        self.assertTrue(window.right_drawer_open)

    def test_show_context_drawer_opens_sub_agent_tab_safely(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        window.RIGHT_TAB_OBSERVABILITY = MainWindow.RIGHT_TAB_OBSERVABILITY
        window.RIGHT_TAB_SUB_AGENTS = MainWindow.RIGHT_TAB_SUB_AGENTS
        window.right_drawer_tab = window.RIGHT_TAB_FILES
        window.right_drawer_open = False
        window.current_session_id = state.session_id
        window.right_stack = MagicMock()
        window.right_stack.count.return_value = 4
        window.right_sidebar = MagicMock()
        window.sub_agent_monitor = MagicMock()
        window.update_context_drawer_header = MagicMock()
        window.sync_context_drawer_layout = MagicMock()
        window.update_context_rail_badges = MagicMock()
        window.get_current_session = MagicMock(return_value=state)
        window._queue_render_sub_agent_monitor_for_state = MagicMock()

        window.show_context_drawer(window.RIGHT_TAB_SUB_AGENTS)

        window.sub_agent_monitor.reset.assert_called_once()
        window.right_stack.setCurrentIndex.assert_called_once_with(window.RIGHT_TAB_SUB_AGENTS)
        window._queue_render_sub_agent_monitor_for_state.assert_called_once_with(state, delay_ms=250)
        self.assertTrue(window.right_drawer_open)
        self.assertEqual(window.right_drawer_tab, window.RIGHT_TAB_SUB_AGENTS)

    def test_queue_sub_agent_monitor_render_uses_configurable_delay(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        window._flush_sub_agent_monitor_render = MagicMock()

        with patch("main.QTimer.singleShot") as single_shot:
            window._queue_render_sub_agent_monitor_for_state(state)

        self.assertTrue(state.sub_agent_render_queued)
        self.assertEqual(single_shot.call_args.args[0], 120)

        state.sub_agent_render_queued = False
        with patch("main.QTimer.singleShot") as single_shot:
            window._queue_render_sub_agent_monitor_for_state(state, delay_ms=250)

        self.assertTrue(state.sub_agent_render_queued)
        self.assertEqual(single_shot.call_args.args[0], 250)

    def test_queue_sub_agent_monitor_render_skips_duplicate_queue(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        state.sub_agent_render_queued = True

        with patch("main.QTimer.singleShot") as single_shot:
            window._queue_render_sub_agent_monitor_for_state(state, delay_ms=250)

        single_shot.assert_not_called()

    def test_render_sub_agent_monitor_truncates_to_stable_limit(self):
        window = MainWindow.__new__(MainWindow)
        state = _AgentUiState()
        state.sub_agent_events = [
            {
                "agent_id": "agent-1",
                "agent_name": "worker",
                "status": "content",
                "content": f"event-{index}",
                "ts": index + 1,
            }
            for index in range(100)
        ]
        window.sub_agent_monitor = MagicMock()
        window.right_drawer_open = True
        window.right_drawer_tab = window.RIGHT_TAB_SUB_AGENTS

        window._render_sub_agent_monitor_for_state(state)

        window.sub_agent_monitor.reset.assert_called_once()
        window.sub_agent_monitor.set_notice.assert_called_once()
        self.assertEqual(window.sub_agent_monitor.update_log.call_count, 80)

    def test_sub_agent_monitor_renders_lightweight_rows(self):
        app = QApplication.instance() or QApplication([])
        monitor = SubAgentMonitor()
        events = [
            {"agent_id": "agent-1", "agent_name": "worker", "status": "input", "input_text": "task", "content": "task", "ts": 1},
            {"agent_id": "agent-1", "agent_name": "worker", "status": "tool_use", "tool_name": "read_file", "tool_args": {"path": "a.txt"}, "content": "call", "ts": 2},
            {"agent_id": "agent-1", "agent_name": "worker", "status": "tool_result", "tool_name": "read_file", "tool_result": "ok", "content": "ok", "ts": 3},
            {"agent_id": "agent-1", "agent_name": "worker", "status": "completed", "output_text": "done", "content": "done", "ts": 4},
        ]

        for event in events:
            monitor.update_log(
                event["agent_id"],
                event.get("content", ""),
                event.get("status", ""),
                agent_name=event.get("agent_name", ""),
                event=event,
            )

        app.processEvents()

        self.assertEqual(len(monitor.agents), 1)
        card = monitor.agents["agent-1"]
        self.assertEqual(card.timeline_layout.count(), len(events))

    def test_sub_agent_event_widgets_keep_qt_event_callable(self):
        app = QApplication.instance() or QApplication([])
        payload = {"agent_id": "agent-1", "status": "input", "content": "task", "ts": 1}

        summary_row = SubAgentEventSummaryRow(payload)
        detail_tile = SubAgentEventTile(payload)
        app.processEvents()

        self.assertTrue(callable(summary_row.event))
        self.assertTrue(callable(detail_tile.event))
        self.assertEqual(summary_row.focusPolicy(), Qt.NoFocus)

    def test_context_drawer_click_helper_keeps_drawer_open_for_sub_agent_child_widget(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.right_drawer_tab = window.RIGHT_TAB_SUB_AGENTS
        host = QWidget()
        host.setGeometry(0, 0, 480, 360)
        window.right_sidebar = QWidget(host)
        window.right_sidebar.setGeometry(220, 20, 220, 300)
        window.context_rail = QWidget(host)
        window.context_rail.setGeometry(12, 20, 44, 300)
        inside = QWidget(window.right_sidebar)
        inside.setGeometry(16, 16, 80, 24)
        host.show()
        app.processEvents()

        event = _MousePressEventStub(inside.mapToGlobal(inside.rect().center()))
        should_hide, hit_test = window._should_hide_context_drawer_for_click(inside, event)

        self.assertFalse(should_hide)
        self.assertTrue(hit_test["in_drawer"])
        host.close()

    def test_context_drawer_click_helper_keeps_drawer_open_for_scroll_viewport(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.right_drawer_tab = window.RIGHT_TAB_SUB_AGENTS
        host = QWidget()
        host.setGeometry(0, 0, 480, 360)
        window.right_sidebar = QWidget(host)
        window.right_sidebar.setGeometry(220, 20, 220, 300)
        window.context_rail = QWidget(host)
        window.context_rail.setGeometry(12, 20, 44, 300)
        scroll = QScrollArea(window.right_sidebar)
        scroll.setGeometry(10, 10, 180, 160)
        scroll.setWidget(QWidget())
        host.show()
        app.processEvents()

        viewport = scroll.viewport()
        event = _MousePressEventStub(viewport.mapToGlobal(viewport.rect().center()))
        should_hide, hit_test = window._should_hide_context_drawer_for_click(viewport, event)

        self.assertFalse(should_hide)
        self.assertTrue(hit_test["in_drawer"])
        host.close()

    def test_context_drawer_click_helper_hides_drawer_for_click_outside_context_zone(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.right_drawer_tab = window.RIGHT_TAB_SUB_AGENTS
        host = QWidget()
        host.setGeometry(0, 0, 480, 360)
        window.right_sidebar = QWidget(host)
        window.right_sidebar.setGeometry(220, 20, 220, 300)
        window.context_rail = QWidget(host)
        window.context_rail.setGeometry(12, 20, 44, 300)
        outside = QWidget(host)
        outside.setGeometry(80, 60, 80, 40)
        host.show()
        app.processEvents()

        event = _MousePressEventStub(outside.mapToGlobal(outside.rect().center()))
        should_hide, hit_test = window._should_hide_context_drawer_for_click(outside, event)

        self.assertTrue(should_hide, msg=str(hit_test))
        self.assertFalse(hit_test["in_drawer"])
        self.assertFalse(hit_test["in_rail"])
        host.close()

    def test_show_tool_details_uses_safe_preview_setter(self):
        window = MainWindow.__new__(MainWindow)
        state = type("_ToolState", (), {"tool_cards": {}})()
        calls = []
        window.current_session_id = "session-1"
        window.get_current_session = lambda: state
        window.set_context_tab_hint = MagicMock()
        window._set_observability_text = lambda edit, text, field_name, limit=6000: calls.append((field_name, len(text), limit))
        window.td_info_label = MagicMock()
        window.td_meta_label = MagicMock()
        window.td_args_edit = object()
        window.td_result_edit = object()

        window.show_tool_details("tool-1", {"k": "v"}, "x" * 100000, switch_tab=False)

        self.assertEqual(calls[0][0], "tool_args")
        self.assertEqual(calls[1][0], "tool_result")
        self.assertEqual(calls[1][2], 12000)


if __name__ == "__main__":
    unittest.main()
