import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.clarify_mode import RUN_MODE_EXECUTION
from main import MainWindow


class _State:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.workspace_dir = ""
        self.workspace_source = ""
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


if __name__ == "__main__":
    unittest.main()
