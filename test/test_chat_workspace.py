import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.clarify_mode import RUN_MODE_EXECUTION
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
        self.clarify_mode_state = "exploring"
        self.pending_clarify_questions = []
        self.clarify_round_count = 0
        self.office_conversion_source_files = []
        self.office_conversion_template_file = ""
        self.office_task_target_format = ""


class TestChatWorkspaceHelpers(unittest.TestCase):
    def _window(self, base_dir):
        window = MainWindow.__new__(MainWindow)
        window.config_manager = MagicMock()
        window.config_manager.get_selected_model_id.return_value = "model-a"
        window._selected_reasoning_effort = MagicMock(return_value="")
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
        window.project_full_expanded_paths = set()
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

    def test_chat_workspace_is_not_grouped_as_project_history(self):
        with tempfile.TemporaryDirectory() as base_dir, patch("main.get_base_dir", return_value=base_dir):
            window = self._window(base_dir)
            workspace = os.path.join(base_dir, "conversation_workspaces", "abc123")
            conversation = {
                "id": "abc123",
                "meta": {"workspace_dir": workspace, "workspace_source": "chat"},
            }

            self.assertEqual(window._conversation_workspace_path(conversation), "")

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

    def test_rewritten_long_history_renders_only_initial_page(self):
        window = MainWindow.__new__(MainWindow)
        window.chat_storage = MagicMock()
        window.chat_storage.normalize_messages.side_effect = lambda messages: messages
        window.clear_chat_layout = MagicMock()
        window._render_session_history_spans = MagicMock()
        window.queue_session_bubble_virtualization = MagicMock()
        state = _State("session-1")
        messages = [
            {"id": f"u{index}", "role": "user", "content": f"message {index}"}
            for index in range(HISTORY_RENDER_PAGE_SIZE + 5)
        ]

        window._render_rewritten_session(state, messages)

        rendered_spans = window._render_session_history_spans.call_args.args[1]
        self.assertEqual(len(rendered_spans), HISTORY_RENDER_PAGE_SIZE)
        self.assertEqual(state.displayed_render_count, HISTORY_RENDER_PAGE_SIZE)
        self.assertEqual(state.messages, messages)

    def test_rewritten_short_history_renders_all_spans(self):
        window = MainWindow.__new__(MainWindow)
        window.chat_storage = MagicMock()
        window.chat_storage.normalize_messages.side_effect = lambda messages: messages
        window.clear_chat_layout = MagicMock()
        window._render_session_history_spans = MagicMock()
        window.queue_session_bubble_virtualization = MagicMock()
        state = _State("session-1")
        messages = [
            {"id": f"u{index}", "role": "user", "content": f"message {index}"}
            for index in range(3)
        ]

        window._render_rewritten_session(state, messages)

        rendered_spans = window._render_session_history_spans.call_args.args[1]
        self.assertEqual(len(rendered_spans), 3)
        self.assertEqual(state.displayed_render_count, 3)


if __name__ == "__main__":
    unittest.main()
