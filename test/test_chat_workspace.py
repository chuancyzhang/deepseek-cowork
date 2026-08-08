import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.clarify_mode import (
    GRILL_MODE_ARMED,
    GRILL_MODE_DISABLED,
    RUN_MODE_EXECUTION,
    RUN_MODE_GRILLING,
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
)
from main import HISTORY_RENDER_PAGE_SIZE, MainWindow


class _State:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.workspace_dir = ""
        self.workspace_source = ""
        self.messages = []
        self.render_items = []
        self.displayed_count = 0
        self.displayed_render_count = 0
        self.history_loaded = True
        self.history_loading = False
        self.llm_worker = None
        self.daemon_running = False
        self.code_worker = None
        self.chat_layout = MagicMock()
        self.empty_state = None
        self.tool_cards = {}
        self.pending_tool_results = {}
        self.last_agent_bubble = None
        self.temp_thinking_bubble = None
        self.persisted_conversation_meta = {}
        self.selected_skill_names = []
        self.selected_model_id = ""
        self.clarify_mode_state = "exploring"
        self.pending_clarify_questions = []
        self.grill_mode_state = GRILL_MODE_DISABLED
        self.grill_round_count = 0
        self.grill_cycle_count = 0
        self.grill_execution_confirmed = False
        self.office_conversion_source_files = []
        self.office_conversion_template_file = ""
        self.office_task_target_format = ""


class TestChatWorkspaceHelpers(unittest.TestCase):
    def test_grill_metadata_ignores_legacy_clarification_count(self):
        state = _State()
        state.grill_mode_state = GRILL_MODE_ARMED
        state.grill_round_count = 4
        state.grill_cycle_count = 2
        state.clarify_round_count = 3

        meta = MainWindow._session_clarify_meta(MainWindow.__new__(MainWindow), state)

        self.assertNotIn("clarify_round_count", meta)
        self.assertEqual(meta["grill_mode_state"], GRILL_MODE_ARMED)
        self.assertEqual(meta["grill_round_count"], 4)
        self.assertEqual(meta["grill_cycle_count"], 2)

    def test_grill_submit_conflicts_preserve_input_before_any_runtime_start(self):
        state = _State()
        state.grill_mode_state = GRILL_MODE_ARMED
        window = MainWindow.__new__(MainWindow)
        window.current_session_id = state.session_id
        window._session_is_busy = lambda _state: False
        window._extract_agent_mentions = lambda _text: ([{"id": "agent-a"}], "执行任务")
        window.add_system_toast = MagicMock()

        submitted = MainWindow._submit_session_request(
            window,
            state,
            "@Agent 执行任务",
            [],
        )

        self.assertFalse(submitted)
        self.assertEqual(state.grill_mode_state, GRILL_MODE_ARMED)
        toast_text = window.add_system_toast.call_args.args[0]
        self.assertIn("输入和附件已保留", toast_text)

        window._extract_agent_mentions = lambda _text: ([], "")
        window.add_system_toast.reset_mock()
        submitted = MainWindow._submit_session_request(
            window,
            state,
            "生成办公稿",
            [],
            workflow_mode=WORKFLOW_MODE_OFFICE_HTML_FIRST,
        )
        self.assertFalse(submitted)
        self.assertEqual(state.grill_mode_state, GRILL_MODE_ARMED)
        self.assertIn("输入和附件已保留", window.add_system_toast.call_args.args[0])

    def _window(self, base_dir):
        window = MainWindow.__new__(MainWindow)
        window.config_manager = MagicMock()
        window.config_manager.get_selected_model_id.return_value = "model-a"
        window.config_manager.get_chat_workspace_root.return_value = os.path.join(
            base_dir, "conversation_workspaces"
        )
        window.config_manager.iter_model_profiles.return_value = [
            {
                "id": "model-a",
                "provider_type": "openai",
                "api_key": "key-a",
                "base_url": "https://a.example/v1",
                "model_name": "model-a-name",
                "display_name": "Model A",
                "reasoning_efforts": ["low", "high"],
                "reasoning_effort": "high",
            },
            {
                "id": "model-b",
                "provider_type": "openai",
                "api_key": "key-b",
                "base_url": "https://b.example/v1",
                "model_name": "model-b-name",
            },
        ]
        return window

    def _workspace_window(self):
        window = MainWindow.__new__(MainWindow)
        window.config_manager = MagicMock()
        window.config_manager.get.return_value = ""
        window._selected_reasoning_effort = MagicMock(return_value="")
        window.sessions = {}
        window.current_session_id = ""
        window.workspace_dir = ""
        window.current_project_path = ""
        window.project_preview_paths = set()
        window.project_history_visible_limits = {}
        window._apply_workspace_to_ui = MagicMock(return_value=True)
        window.refresh_history_list = MagicMock()
        window.normalize_session_ui = MagicMock()
        window.refresh_project_selector = MagicMock()
        return window

    def test_direct_chat_creates_per_session_workspace_and_meta(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            state = _State("abc123")

            workspace = window._ensure_session_workspace(state)

            self.assertEqual(
                workspace,
                os.path.join(base_dir, "conversation_workspaces", "abc123"),
            )
            self.assertTrue(os.path.isdir(workspace))
            self.assertEqual(state.workspace_dir, workspace)
            self.assertEqual(state.workspace_source, "chat")
            self.assertEqual(state.persisted_conversation_meta["workspace_dir"], workspace)
            self.assertEqual(state.persisted_conversation_meta["workspace_source"], "chat")

    def test_project_workspace_keeps_project_source(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as project_dir, patch(
            "main.get_base_dir", return_value=base_dir
        ):
            window = self._window(base_dir)
            state = _State("abc123")

            workspace = window._set_session_workspace(state, project_dir, source="project")

            self.assertEqual(workspace, os.path.normpath(os.path.abspath(project_dir)))
            self.assertEqual(state.workspace_source, "project")
            self.assertEqual(state.persisted_conversation_meta["workspace_source"], "project")

    def test_empty_history_session_lazily_gets_chat_workspace_for_run_context(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            state = _State("history-session")

            ctx = window._build_run_context(state, RUN_MODE_EXECUTION)

            self.assertEqual(ctx["workspace_mode"], "project")
            self.assertEqual(state.workspace_source, "chat")
            self.assertTrue(state.workspace_dir.endswith(os.path.join("conversation_workspaces", "history-session")))
            self.assertTrue(os.path.isdir(state.workspace_dir))

    def test_run_context_freezes_session_model_profile(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            state = _State("session-model")
            state.selected_model_id = "model-b"

            ctx = window._build_run_context(state, RUN_MODE_EXECUTION)

            self.assertEqual(ctx["selected_model_id"], "model-b")
            self.assertEqual(ctx["selected_model_profile"]["model_name"], "model-b-name")
            self.assertEqual(ctx["selected_model_profile"]["api_key"], "key-b")

    def test_grill_run_context_is_identical_for_local_and_daemon_dispatch(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            state = _State("grill-session")
            state.grill_mode_state = GRILL_MODE_ARMED
            state.grill_round_count = 7
            state.grill_cycle_count = 3

            ctx = window._build_run_context(state, RUN_MODE_GRILLING)

            self.assertEqual(ctx["mode"], RUN_MODE_GRILLING)
            self.assertEqual(ctx["grill_round_count"], 7)
            self.assertEqual(ctx["grill_cycle_count"], 3)
            self.assertFalse(ctx["grill_execution_confirmed"])

    def test_new_session_defaults_to_current_conversation_model(self):
        window = self._window(tempfile.gettempdir())
        state = _State("current")
        state.selected_model_id = "model-b"
        window.sessions = {state.session_id: state}
        window.current_session_id = state.session_id

        self.assertEqual(window._default_model_id_for_new_session(), "model-b")

    def test_chat_workspace_is_not_grouped_as_project_history(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            workspace = os.path.join(base_dir, "conversation_workspaces", "abc123")
            conversation = {
                "id": "abc123",
                "meta": {"workspace_dir": workspace, "workspace_source": "chat"},
            }

            self.assertEqual(window._conversation_workspace_path(conversation), "")

    def test_direct_chat_uses_configured_workspace_root(self):
        with tempfile.TemporaryDirectory() as configured_root:
            window = self._window(tempfile.gettempdir())
            window.config_manager.get_chat_workspace_root.return_value = configured_root
            state = _State("configured-session")

            workspace = window._ensure_session_workspace(state)

            self.assertEqual(workspace, os.path.join(configured_root, "configured-session"))
            self.assertTrue(os.path.isdir(workspace))

    def test_load_workspace_does_not_reassign_existing_session(self):
        with tempfile.TemporaryDirectory() as project_a, tempfile.TemporaryDirectory() as project_b:
            window = self._workspace_window()
            state = _State("session-1")
            state.messages = [{"id": "u1", "role": "user", "content": "keep me here"}]
            window._set_session_workspace(state, project_a, source="project")
            window.sessions[state.session_id] = state
            window.current_session_id = state.session_id

            self.assertTrue(window.load_workspace(project_b, refresh_sidebar=False))

            self.assertEqual(state.workspace_dir, os.path.normpath(os.path.abspath(project_a)))
            self.assertEqual(state.workspace_source, "project")
            window._apply_workspace_to_ui.assert_called_once()

    def test_project_click_opens_workspace_without_reassigning_session(self):
        with tempfile.TemporaryDirectory() as project_a, tempfile.TemporaryDirectory() as project_b:
            window = self._workspace_window()
            state = _State("session-1")
            state.messages = [{"id": "u1", "role": "user", "content": "existing"}]
            window._set_session_workspace(state, project_a, source="project")
            window.sessions[state.session_id] = state
            window.current_session_id = state.session_id

            self.assertTrue(window.handle_project_click(project_b))

            self.assertEqual(state.workspace_dir, os.path.normpath(os.path.abspath(project_a)))
            self.assertEqual(state.workspace_source, "project")
            window._apply_workspace_to_ui.assert_called_once()

    def test_add_project_does_not_reassign_existing_session(self):
        with tempfile.TemporaryDirectory() as project_a, tempfile.TemporaryDirectory() as project_b:
            window = self._workspace_window()
            window.config_manager.upsert_project.return_value = {
                "path": os.path.normpath(os.path.abspath(project_b)),
                "name": "Project B",
            }
            state = _State("session-1")
            state.messages = [{"id": "u1", "role": "user", "content": "existing"}]
            window._set_session_workspace(state, project_a, source="project")
            window.sessions[state.session_id] = state
            window.current_session_id = state.session_id

            with patch("main.QFileDialog.getExistingDirectory", return_value=project_b):
                window.add_project_from_dialog()

            self.assertEqual(state.workspace_dir, os.path.normpath(os.path.abspath(project_a)))
            self.assertEqual(state.workspace_source, "project")

    def test_project_new_conversation_does_not_reassign_current_session(self):
        with tempfile.TemporaryDirectory() as project_a, tempfile.TemporaryDirectory() as project_b:
            window = self._workspace_window()
            state = _State("session-1")
            state.messages = [{"id": "u1", "role": "user", "content": "existing"}]
            window._set_session_workspace(state, project_a, source="project")
            window.sessions[state.session_id] = state
            window.current_session_id = state.session_id
            window.create_new_session = MagicMock(return_value="session-2")

            window.new_conversation_for_project(project_b)

            self.assertEqual(state.workspace_dir, os.path.normpath(os.path.abspath(project_a)))
            self.assertEqual(state.workspace_source, "project")
            window.create_new_session.assert_called_once_with(
                workspace_dir=os.path.normpath(os.path.abspath(project_b))
            )

    def test_legacy_full_history_rewrite_is_explicitly_disabled(self):
        window = MainWindow.__new__(MainWindow)
        window.chat_storage = MagicMock()
        window.chat_storage.normalize_messages.side_effect = lambda messages, **kwargs: messages
        window.clear_chat_layout = MagicMock()
        window._render_session_history_spans = MagicMock()
        window.queue_session_bubble_virtualization = MagicMock()
        state = _State("session-1")
        messages = [
            {"id": f"u{index}", "role": "user", "content": f"message {index}"}
            for index in range(HISTORY_RENDER_PAGE_SIZE + 5)
        ]

        with self.assertRaisesRegex(RuntimeError, "全量历史重写已禁用"):
            window._render_rewritten_session(state, messages)

        window.clear_chat_layout.assert_not_called()
        window._render_session_history_spans.assert_not_called()

if __name__ == "__main__":
    unittest.main()
