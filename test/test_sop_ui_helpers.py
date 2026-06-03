import os
import sys
import tempfile
import unittest
import shutil
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import create_sop_run, mark_step_awaiting_confirmation
from core.chat_storage import ChatStorage
from main import (
    QApplication,
    AutomationTaskDialog,
    ChatBubble,
    MainWindow,
    SkillsCenterDialog,
    SopTemplateManager,
    SystemToast,
    SubAgentEventSummaryRow,
    SubAgentEventTile,
    SubAgentMonitor,
    SOP_EXECUTOR_BASH_COMMAND,
    SOP_EXECUTOR_PYTHON_FILE,
    skill_center_matches_filters,
    summarize_skill_terms,
)
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QScrollArea, QWidget


class _State:
    def __init__(self, sop_run=None):
        self.sop_run = sop_run


class _AgentUiState:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.sub_agent_events = []
        self.sub_agent_render_queued = False


class SkillCenterHelperTests(unittest.TestCase):
    def test_summarize_skill_terms_truncates_cleanly(self):
        summary = summarize_skill_terms(
            ["read_file", "write_file", "update_file", "rename_file"],
            max_items=3,
            max_chars=24,
        )
        self.assertTrue(summary.startswith("read_file"))
        self.assertTrue(summary.endswith("…"))
        self.assertNotIn("rename_file", summary)

    def test_skill_center_matches_filters_supports_query_and_status(self):
        skill = {
            "name": "file-system",
            "display_name": "文件整理与读写",
            "user_description": "提供工作区内统一的文件发现、读取、写入能力",
            "tools": ["read_file", "write_file"],
            "use_cases": ["整理文件", "读取文档"],
            "enabled": True,
        }
        self.assertTrue(skill_center_matches_filters(skill, query="read_file", status_filter="all"))
        self.assertTrue(skill_center_matches_filters(skill, query="文件整理", status_filter="enabled"))
        self.assertFalse(skill_center_matches_filters(skill, query="浏览器", status_filter="all"))
        self.assertFalse(skill_center_matches_filters(skill, query="read_file", status_filter="disabled"))
        self.tool_cards = {}
        self.last_agent_bubble = None
        self.llm_worker = None
        self.daemon_running = True
        self.sop_run = None

    def test_skill_center_copy_skill_name_copies_internal_name(self):
        app = QApplication.instance() or QApplication([])
        dialog = SkillsCenterDialog.__new__(SkillsCenterDialog)
        clipboard = MagicMock()
        button = MagicMock()

        with patch.object(QApplication, "clipboard", return_value=clipboard), patch("main.QTimer.singleShot") as single_shot:
            dialog.copy_skill_name("claim-expert", button)

        clipboard.setText.assert_called_once_with("claim-expert")
        button.setToolTip.assert_called_with("已复制")
        self.assertEqual(single_shot.call_args.args[0], 1200)

    def test_skill_center_card_title_is_selectable(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())

        card = dialog._build_skill_card(
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": True,
                "risk_level": "medium",
                "tools": [],
            }
        )

        title_labels = [label for label in card.findChildren(QLabel) if label.text() == "Claim Expert"]
        self.assertTrue(title_labels)
        self.assertTrue(title_labels[0].textInteractionFlags() & Qt.TextSelectableByMouse)


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


class _HistoryActionState:
    def __init__(self, session_id="session-1", messages=None):
        self.session_id = session_id
        self.messages = list(messages or [])
        self.history_loaded = True
        self.history_loading = False
        self.llm_worker = None
        self.code_worker = None
        self.daemon_running = False


class TestSopUiHelpers(unittest.TestCase):
    def test_prompt_tool_menu_order_helper_matches_spec(self):
        window = MainWindow.__new__(MainWindow)
        entries = window._prompt_tool_menu_entries()
        self.assertEqual(
            [label for _key, label in entries],
            ["添加文件", "添加智能体", "添加自动化", "从对话生成 SOP", "指定能力", "反问模式"],
        )
        self.assertNotIn("能力中心", [label for _key, label in entries])

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

    def test_clear_session_sop_removes_current_run_and_refreshes_ui(self):
        window = MainWindow.__new__(MainWindow)
        state = type(
            "_Session",
            (),
            {
                "session_id": "session-1",
                "sop_run": create_sop_run(
                    {
                        "id": "office",
                        "name": "Office",
                        "steps": [{"title": "Step 1"}],
                    }
                ),
            },
        )()
        window.RIGHT_TAB_SOP = MainWindow.RIGHT_TAB_SOP
        window.right_drawer_open = True
        window.right_drawer_tab = MainWindow.RIGHT_TAB_SOP
        window.get_current_session = MagicMock(return_value=state)
        window.save_chat_history = MagicMock()
        window.refresh_sop_controls = MagicMock()
        window.refresh_context_badges = MagicMock()
        window.normalize_session_ui = MagicMock()
        window.hide_context_drawer = MagicMock()
        window.add_system_toast = MagicMock()

        window.clear_session_sop()

        self.assertIsNone(state.sop_run)
        window.save_chat_history.assert_called_once_with(session_id="session-1")
        window.refresh_sop_controls.assert_called_once_with("session-1")
        window.refresh_context_badges.assert_called_once_with("session-1")
        window.hide_context_drawer.assert_called_once_with(reason="sop_cleared")

    def test_clear_session_selected_skills_removes_current_skills(self):
        window = MainWindow.__new__(MainWindow)
        state = type("_Session", (), {"session_id": "session-1", "selected_skill_names": ["python-runner"]})()
        window.get_session = MagicMock(return_value=state)
        window.refresh_selected_skill_controls = MagicMock()
        window.refresh_context_badges = MagicMock()
        window.save_chat_history = MagicMock()
        window.add_system_toast = MagicMock()

        window.clear_session_selected_skills("session-1")

        self.assertEqual(state.selected_skill_names, [])
        window.save_chat_history.assert_called_once_with(session_id="session-1")
        window.refresh_selected_skill_controls.assert_called_once_with("session-1")
        window.refresh_context_badges.assert_called_once_with("session-1")

    def test_start_conversation_sop_flow_requires_messages(self):
        window = MainWindow.__new__(MainWindow)
        window.conversation_sop_worker = None
        window.get_current_session = MagicMock(return_value=type("_Session", (), {"messages": []})())
        window.add_system_toast = MagicMock()

        window.start_conversation_sop_flow()

        window.add_system_toast.assert_called_once()
        self.assertIn("还没有可提炼的 SOP 内容", window.add_system_toast.call_args.args[0])

    def test_start_conversation_sop_flow_skips_when_worker_running(self):
        window = MainWindow.__new__(MainWindow)
        worker = MagicMock()
        worker.isRunning.return_value = True
        window.conversation_sop_worker = worker
        window.add_system_toast = MagicMock()

        window.start_conversation_sop_flow()

        window.add_system_toast.assert_called_once()
        self.assertEqual("SOP 草稿生成中", window.add_system_toast.call_args.args[0])

    def test_system_toast_uses_compact_wrapped_layout(self):
        app = QApplication.instance() or QApplication([])
        toast = SystemToast("已绑定自动化，正在等待下一步执行说明", "success")
        app.processEvents()

        self.assertEqual("SystemToast", toast.objectName())
        self.assertLessEqual(toast.maximumWidth(), 720)
        self.assertIs(toast.icon_label, toast.icon_badge)
        self.assertTrue(toast.message_label.wordWrap())

        text_label = toast.findChild(QLabel, "SystemToastText")
        self.assertIsNotNone(text_label)
        self.assertEqual("已绑定自动化，正在等待下一步执行说明", text_label.text())

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

    def test_sop_template_manager_saves_step_executor_fields(self):
        app = QApplication.instance() or QApplication([])
        manager = SopTemplateManager(
            [
                {
                    "id": "office",
                    "name": "Office",
                    "steps": [{"title": "Step 1"}],
                }
            ],
            skill_provider=lambda: [],
            agent_profile_provider=lambda: [],
        )
        manager.template_list.setCurrentRow(0)
        manager.step_list.setCurrentRow(0)
        app.processEvents()

        manager.step_executor_type_combo.setCurrentIndex(
            manager.step_executor_type_combo.findData(SOP_EXECUTOR_BASH_COMMAND)
        )
        manager.step_bash_command_edit.setPlainText("echo hi")
        manager.step_timeout_spin.setValue(42)

        templates = manager.get_templates()
        self.assertEqual(templates[0]["steps"][0]["executor_type"], SOP_EXECUTOR_BASH_COMMAND)
        self.assertEqual(templates[0]["steps"][0]["bash_command"], "echo hi")
        self.assertEqual(templates[0]["steps"][0]["timeout_seconds"], 42)

    def test_sop_template_manager_uploads_python_script_asset(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = os.path.join(temp_dir, "demo.py")
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write("print('ok')\n")
            with patch("main.get_app_data_dir", return_value=temp_dir), patch(
                "main.QFileDialog.getOpenFileName",
                return_value=(script_path, "Python Files (*.py)"),
            ):
                manager = SopTemplateManager(
                    [{"id": "office", "name": "Office", "steps": [{"title": "Step 1"}]}],
                    skill_provider=lambda: [],
                    agent_profile_provider=lambda: [],
                )
                manager.template_list.setCurrentRow(0)
                manager.step_list.setCurrentRow(0)
                app.processEvents()
                manager._choose_step_python_script()

                templates = manager.get_templates()
                script = templates[0]["steps"][0]["python_script"]
                self.assertEqual(templates[0]["steps"][0]["executor_type"], SOP_EXECUTOR_PYTHON_FILE)
                self.assertEqual(script["filename"], "demo.py")
                self.assertTrue(os.path.isfile(script["path"]))
                self.assertEqual(script["source_path"], os.path.normpath(script_path))

    def test_automation_task_dialog_returns_cron_payload(self):
        app = QApplication.instance() or QApplication([])
        dialog = AutomationTaskDialog(
            [{"id": "tpl-1", "name": "Template", "steps": [{"title": "Step 1"}]}]
        )
        dialog.schedule_mode_combo.setCurrentIndex(dialog.schedule_mode_combo.findData("cron"))
        dialog.cron_expression_input.setText("15 8 * * 1-5")
        app.processEvents()

        payload = dialog.task_payload()

        self.assertEqual(payload["schedule_type"], "cron")
        self.assertEqual(payload["cron_expression"], "15 8 * * 1-5")

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

    def test_build_user_message_payload_marks_images_when_vision_enabled(self):
        window = MainWindow.__new__(MainWindow)
        window.workspace_dir = None

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "截图.png")
            with open(image_path, "wb") as handle:
                handle.write(b"png")

            payload = window._build_user_message_payload("识别这张图的文字", [image_path], supports_vision=True)

        self.assertEqual(payload["content_parts"][1]["type"], "input_image")
        self.assertEqual(payload["meta"]["user_added_images"], [os.path.normpath(image_path)])
        self.assertTrue(payload["meta"]["vision_requested"])

    def test_build_user_message_payload_keeps_image_parts_when_vision_disabled(self):
        window = MainWindow.__new__(MainWindow)
        window.workspace_dir = None

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "截图.png")
            with open(image_path, "wb") as handle:
                handle.write(b"png")

            payload = window._build_user_message_payload("识别这张图的文字", [image_path], supports_vision=False)

        self.assertEqual(payload["content_parts"][1]["type"], "input_image")

    def test_build_user_message_payload_auto_attaches_named_workspace_image(self):
        window = MainWindow.__new__(MainWindow)

        with tempfile.TemporaryDirectory() as temp_dir:
            window.workspace_dir = temp_dir
            image_path = os.path.join(temp_dir, "screenshot.png")
            with open(image_path, "wb") as handle:
                handle.write(b"png")

            payload = window._build_user_message_payload("看看 screenshot.png", [], supports_vision=True)

        self.assertEqual(payload["content_parts"][1]["type"], "input_image")
        self.assertEqual(payload["content_parts"][1]["path"], os.path.normpath(os.path.abspath(image_path)))
        self.assertEqual(payload["meta"]["workspace_referenced_images"], [os.path.normpath(os.path.abspath(image_path))])

    def test_fork_conversation_at_message_creates_clean_branch_session(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = os.path.join(temp_dir, "chat_history.sqlite")
        storage = ChatStorage(db_path)
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(workspace_dir)
        parent_messages = [
            {"id": "u1", "role": "user", "content": "first"},
            {"id": "a1", "role": "assistant", "content": "reply"},
            {"id": "u2", "role": "user", "content": "second"},
        ]
        storage.save_conversation(
            "parent",
            parent_messages,
            title="Parent task",
            status="running",
            meta={
                "workspace_dir": workspace_dir,
                "selected_skill_names": ["python-runner"],
                "sop_run": {"template_id": "demo"},
            },
        )

        window = MainWindow.__new__(MainWindow)
        window.chat_storage = storage
        window.workspace_dir = workspace_dir
        window.save_chat_history = MagicMock()
        window.refresh_history_list = MagicMock()
        window.activate_session = MagicMock()
        window.add_system_toast = MagicMock()
        window.sessions = {}

        created_states = {}

        def create_new_session(session_id=None, title=None, make_current=True):
            state = type("_ForkState", (), {})()
            state.session_id = session_id
            state.selected_skill_names = []
            state.run_phase = ""
            state.session_status = ""
            state.has_file_changes = True
            state.changed_files = ["demo.py"]
            state.clarify_mode_enabled = True
            state.clarify_phase = "awaiting"
            state.clarify_mode_state = "awaiting"
            state.pending_clarify_questions = [{"id": "q1"}]
            state.clarify_source_user_text = "source"
            state.clarify_answers_context = ["answer"]
            state.sop_run = {"template_id": "demo"}
            state.completed_agent_result_ids = {"a"}
            state.automation_task_id = "task"
            state.automation_run_id = "run"
            state.automation_trigger_source = "manual"
            state.automation_template_id = "tpl"
            state.conversation_branch = None
            created_states[session_id] = state
            return session_id

        window.create_new_session = create_new_session
        window.get_session = lambda session_id: created_states.get(session_id)

        ok = window.fork_conversation_at_message("parent", "a1")

        self.assertTrue(ok)
        window.save_chat_history.assert_called_once_with(session_id="parent")
        window.refresh_history_list.assert_called_once()
        window.activate_session.assert_called_once()

        new_session_id = window.activate_session.call_args.args[0]
        new_record = storage.get_conversation_record(new_session_id)
        new_messages = storage.get_messages(new_session_id)
        new_state = created_states[new_session_id]

        self.assertEqual([msg["role"] for msg in new_messages], ["user", "assistant"])
        self.assertEqual([msg["content"] for msg in new_messages], ["first", "reply"])
        self.assertNotEqual([msg["id"] for msg in new_messages], ["u1", "a1"])
        self.assertEqual(new_record["status"], "draft")
        self.assertEqual(new_record["title"], "Parent task - 分支")
        self.assertEqual(new_record["meta"]["workspace_dir"], workspace_dir)
        self.assertEqual(new_record["meta"]["selected_skill_names"], ["python-runner"])
        self.assertEqual(new_record["meta"]["conversation_branch"]["parent_session_id"], "parent")
        self.assertEqual(new_record["meta"]["conversation_branch"]["parent_message_id"], "a1")
        self.assertEqual(new_state.selected_skill_names, ["python-runner"])
        self.assertFalse(new_state.clarify_mode_enabled)
        self.assertIsNone(new_state.sop_run)
        self.assertEqual(new_state.conversation_branch["parent_session_id"], "parent")
        self.assertEqual(new_record["meta"]["conversation_branch"]["action"], "branch")

    def test_chat_bubble_user_shows_edit_delete_and_branch_actions(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("User", "hello", source_message_id="u1")

        self.assertIsNotNone(app)
        self.assertIsNotNone(bubble.edit_btn)
        self.assertIsNotNone(bubble.delete_btn)
        self.assertIsNotNone(bubble.branch_btn)
        self.assertFalse(bubble.edit_btn.isHidden())
        self.assertFalse(bubble.delete_btn.isHidden())
        self.assertFalse(bubble.branch_btn.isHidden())

    def test_edit_user_message_from_branch_creates_new_session_and_resubmits(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = os.path.join(temp_dir, "chat_history.sqlite")
        storage = ChatStorage(db_path)
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(workspace_dir)
        attachment_path = os.path.join(workspace_dir, "brief.txt")
        with open(attachment_path, "w", encoding="utf-8") as handle:
            handle.write("brief")
        parent_messages = [
            {"id": "u1", "role": "user", "content": "first"},
            {"id": "a1", "role": "assistant", "content": "reply"},
            {
                "id": "u2",
                "role": "user",
                "content": "[用户添加的文件]\n\nsecond",
                "content_parts": [
                    {"type": "text", "text": "second"},
                    {"type": "input_file", "path": attachment_path, "name": "brief.txt"},
                ],
                "meta": {
                    "display_content": "second",
                    "user_added_files": [attachment_path],
                },
            },
            {"id": "a2", "role": "assistant", "content": "after second"},
        ]
        storage.save_conversation(
            "parent",
            parent_messages,
            title="Parent task",
            status="completed",
            meta={"workspace_dir": workspace_dir, "selected_skill_names": ["python-runner"]},
        )

        window = MainWindow.__new__(MainWindow)
        window.chat_storage = storage
        window.workspace_dir = workspace_dir
        window.save_chat_history = MagicMock()
        window.refresh_history_list = MagicMock()
        window.activate_session = MagicMock()
        window.add_system_toast = MagicMock()
        window._submit_session_request = MagicMock(return_value=True)
        window.sessions = {}

        parent_state = _HistoryActionState("parent", parent_messages)
        created_states = {}

        def create_new_session(session_id=None, title=None, make_current=True):
            state = _HistoryActionState(session_id)
            created_states[session_id] = state
            return session_id

        window.create_new_session = create_new_session
        window.get_session = lambda session_id=None: created_states.get(session_id) or (parent_state if session_id == "parent" else None)

        with patch("main.QInputDialog.getMultiLineText", return_value=("rewritten second", True)):
            ok = window.edit_user_message_from_branch("parent", "u2")

        self.assertTrue(ok)
        window.save_chat_history.assert_called_once_with(session_id="parent")
        window.refresh_history_list.assert_called_once()
        window.activate_session.assert_called_once()
        window._submit_session_request.assert_called_once()

        new_session_id = window.activate_session.call_args.args[0]
        new_record = storage.get_conversation_record(new_session_id)
        new_messages = storage.get_messages(new_session_id)
        submit_args = window._submit_session_request.call_args

        self.assertEqual([msg["content"] for msg in new_messages], ["first", "reply"])
        self.assertEqual(new_record["meta"]["conversation_branch"]["action"], "edit_user_message")
        self.assertEqual(submit_args.args[0].session_id, new_session_id)
        self.assertEqual(submit_args.args[1], "rewritten second")
        self.assertEqual(submit_args.args[2], [attachment_path])
        self.assertFalse(submit_args.kwargs["check_duplicates"])

    def test_delete_user_message_from_branch_creates_new_session_without_resubmit(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = os.path.join(temp_dir, "chat_history.sqlite")
        storage = ChatStorage(db_path)
        workspace_dir = os.path.join(temp_dir, "workspace")
        os.makedirs(workspace_dir)
        parent_messages = [
            {"id": "u1", "role": "user", "content": "first"},
            {"id": "a1", "role": "assistant", "content": "reply"},
            {"id": "u2", "role": "user", "content": "second"},
            {"id": "a2", "role": "assistant", "content": "after second"},
        ]
        storage.save_conversation(
            "parent",
            parent_messages,
            title="Parent task",
            status="completed",
            meta={"workspace_dir": workspace_dir},
        )

        window = MainWindow.__new__(MainWindow)
        window.chat_storage = storage
        window.workspace_dir = workspace_dir
        window.save_chat_history = MagicMock()
        window.refresh_history_list = MagicMock()
        window.activate_session = MagicMock()
        window.add_system_toast = MagicMock()
        window._submit_session_request = MagicMock()
        window.input_field = MagicMock()
        window.sessions = {}

        parent_state = _HistoryActionState("parent", parent_messages)
        created_states = {}

        def create_new_session(session_id=None, title=None, make_current=True):
            state = _HistoryActionState(session_id)
            created_states[session_id] = state
            return session_id

        window.create_new_session = create_new_session
        window.get_session = lambda session_id=None: created_states.get(session_id) or (parent_state if session_id == "parent" else None)

        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            ok = window.delete_user_message_from_branch("parent", "u2")

        self.assertTrue(ok)
        window.save_chat_history.assert_called_once_with(session_id="parent")
        window.refresh_history_list.assert_called_once()
        window.activate_session.assert_called_once()
        window._submit_session_request.assert_not_called()
        window.input_field.setFocus.assert_called_once()

        new_session_id = window.activate_session.call_args.args[0]
        new_record = storage.get_conversation_record(new_session_id)
        new_messages = storage.get_messages(new_session_id)

        self.assertEqual([msg["content"] for msg in new_messages], ["first", "reply"])
        self.assertEqual(new_record["meta"]["conversation_branch"]["action"], "delete_user_message")

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

    def test_observability_prompt_preview_prefers_append_content(self):
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        state.system_prompt_text = "BASE-" + ("x" * 7000)
        state.system_prompt_appends = [
            {"source": "selected_skills", "content": "APPEND-CONTENT"},
        ]

        full_text, preview_text = window._build_observability_prompt_texts(state, preview_limit=200)

        self.assertTrue(full_text.startswith(state.system_prompt_text))
        self.assertIn("APPEND-CONTENT", full_text)
        self.assertIn("APPEND-CONTENT", preview_text)
        self.assertNotIn("BASE-", preview_text)

    def test_observability_prompt_preview_keeps_tail_when_append_is_long(self):
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        state.system_prompt_appends = [
            {"source": "system", "content": ("a" * 400) + "TAIL-MARKER"},
        ]

        _, preview_text = window._build_observability_prompt_texts(state, preview_limit=120)

        self.assertIn("TAIL-MARKER", preview_text)
        self.assertIn("hidden", preview_text)
        self.assertTrue(preview_text.startswith("...[hidden"))

    def test_copy_observability_full_prompt_copies_complete_text(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        state.system_prompt_text = "BASE"
        state.system_prompt_appends = [
            {"source": "tool", "content": "APPEND"},
        ]
        clipboard = MagicMock()
        button = MagicMock()
        window.get_current_session = lambda: state
        window.observability_copy_prompt_btn = button

        with patch.object(QApplication, "clipboard", return_value=clipboard):
            window.copy_observability_full_prompt()

        copied_text = clipboard.setText.call_args[0][0]
        self.assertIn("BASE", copied_text)
        self.assertIn("APPEND", copied_text)
        button.setText.assert_called_with("已复制")

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
