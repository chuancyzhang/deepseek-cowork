import os
import sys
import tempfile
import unittest
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import create_sop_run, mark_step_awaiting_confirmation
from core.chat_storage import ChatStorage
from core.theme import DesignTokens
from main import (
    BACKGROUND_AUTOMATION_START_DELAY_MS,
    BACKGROUND_DAEMON_MONITOR_DELAY_MS,
    BACKGROUND_DAEMON_PREWARM_DELAY_MS,
    BACKGROUND_TRAY_START_DELAY_MS,
    QApplication,
    AppleSelectionCheck,
    AppleSwitch,
    AutoResizingInputEdit,
    AutoResizingPlainTextEdit,
    AutoResizingTextEdit,
    AutomationTaskDialog,
    CapabilityWorkbenchDialog,
    ChatBubble,
    ConversationHistoryRow,
    DaemonConnectWorker,
    DaemonStreamWorker,
    format_token_usage_chip_text,
    format_token_usage_tooltip,
    MainWindow,
    OfficeDraftTaskCard,
    OFFICE_OUTPUT_PROFILE_DESIGN,
    OFFICE_OUTPUT_PROFILE_FREE,
    OFFICE_OUTPUT_PROFILE_PPT,
    normalize_token_usage_summary,
    SidebarHoverTipController,
    SessionActivityIndicator,
    SessionState,
    StartupLoadingWindow,
    sidebar_symbol_icon,
    UI_ERROR_LOG_FILENAME,
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
    WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
    SkillsCenterDialog,
    SopTemplateManager,
    SystemToast,
    SubAgentEventSummaryRow,
    SubAgentEventTile,
    SubAgentMonitor,
    subprocess_kwargs_no_window,
    TokenUsageChip,
    SOP_EXECUTOR_BASH_COMMAND,
    SOP_EXECUTOR_PYTHON_FILE,
    _MARKDOWN_RENDER_CACHE,
    render_markdown_or_html_with_cache,
    is_auto_query_skill_context_message,
    session_history_ready,
    skill_center_tab_key,
    skill_center_matches_filters,
    skill_runtime_reload_pending,
    summarize_skill_terms,
    log_ui_exception,
    initialize_desktop_theme,
    schedule_main_window_startup,
)
from PySide6.QtCore import QEvent, QPoint, Qt, QMimeData, QTimer
from PySide6.QtGui import QTextOption, QShowEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QMainWindow, QLabel, QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget


class _State:
    def __init__(self, sop_run=None):
        self.sop_run = sop_run


class _AgentUiState:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.sub_agent_events = []
        self.sub_agent_render_queued = False


class SkillCenterHelperTests(unittest.TestCase):
    @patch("main.apply_tooltip_theme")
    def test_desktop_theme_initialization_applies_tooltip_only_theme(self, apply_tooltip_theme_mock):
        app = MagicMock()
        font = MagicMock()
        app.font.return_value = font

        initialize_desktop_theme(app)

        app.setStyle.assert_called_once_with("Fusion")
        font.setFamily.assert_called_once_with("Segoe UI")
        font.setPointSize.assert_called_once_with(10)
        app.setFont.assert_called_once_with(font)
        apply_tooltip_theme_mock.assert_called_once_with(app)

    def test_sidebar_symbols_are_qt_drawn_icons(self):
        app = QApplication.instance() or QApplication([])

        for kind in ("plus", "ellipsis", "folder", "folder-open", "folder-plus"):
            with self.subTest(kind=kind):
                icon = sidebar_symbol_icon(kind, DesignTokens.text_secondary, 16)
                self.assertFalse(icon.isNull())
                self.assertFalse(icon.pixmap(16, 16).isNull())

    def test_project_selector_only_allows_empty_ready_idle_session(self):
        window = MainWindow.__new__(MainWindow)
        ready = type(
            "State",
            (),
            {
                "history_loaded": True,
                "history_loading": False,
                "messages": [],
                "llm_worker": None,
                "daemon_running": False,
                "code_worker": None,
            },
        )()
        with_messages = type(
            "State",
            (),
            {
                "history_loaded": True,
                "history_loading": False,
                "messages": [{"role": "user", "content": "hello"}],
                "llm_worker": None,
                "daemon_running": False,
                "code_worker": None,
            },
        )()
        loading = type(
            "State",
            (),
            {
                "history_loaded": False,
                "history_loading": True,
                "messages": [],
                "llm_worker": None,
                "daemon_running": False,
                "code_worker": None,
            },
        )()

        self.assertTrue(window._project_selector_switch_allowed(ready))
        self.assertFalse(window._project_selector_switch_allowed(with_messages))
        self.assertFalse(window._project_selector_switch_allowed(loading))

    def test_select_project_for_current_conversation_rebinds_empty_session(self):
        window = MainWindow.__new__(MainWindow)
        state = type(
            "State",
            (),
            {
                "session_id": "session-1",
                "workspace_dir": "",
                "persisted_conversation_meta": {},
                "history_loaded": True,
                "history_loading": False,
                "messages": [],
                "llm_worker": None,
                "daemon_running": False,
                "code_worker": None,
            },
        )()
        window.get_current_session = lambda: state
        window.config_manager = MagicMock()
        window._apply_workspace_to_ui = MagicMock()
        window.normalize_session_ui = MagicMock()
        window.refresh_project_selector = MagicMock()
        with tempfile.TemporaryDirectory() as project:
            selected = window.select_project_for_current_conversation(project)

        normalized = os.path.normpath(os.path.abspath(project))
        self.assertTrue(selected)
        self.assertEqual(state.workspace_dir, normalized)
        self.assertEqual(state.persisted_conversation_meta["workspace_dir"], normalized)
        window.config_manager.upsert_project.assert_called_once_with(normalized)
        window._apply_workspace_to_ui.assert_called_once_with(
            normalized,
            refresh_sidebar=True,
            remember_workspace=True,
            persist_default=True,
        )

    def test_session_history_ready_requires_completed_load(self):
        ready = type("State", (), {"history_loaded": True, "history_loading": False})()
        loading = type("State", (), {"history_loaded": False, "history_loading": True})()

        self.assertTrue(session_history_ready(ready))
        self.assertFalse(session_history_ready(loading))
        self.assertFalse(session_history_ready(None))

    def test_auto_query_skill_context_detection_is_source_specific(self):
        auto = {
            "role": "system",
            "meta": {"kind": "skill_context", "source": "skill_prompt_query_match"},
        }
        searched = {
            "role": "system",
            "meta": {"kind": "skill_context", "source": "skill_prompt_tool_search"},
        }

        self.assertTrue(is_auto_query_skill_context_message(auto))
        self.assertFalse(is_auto_query_skill_context_message(searched))

    def test_finish_history_load_rebinds_current_session_aliases(self):
        window = MainWindow.__new__(MainWindow)
        window.current_session_id = "session-1"
        window.set_current_session = MagicMock()
        state = type(
            "State",
            (),
            {"session_id": "session-1", "history_loaded": False, "history_loading": True},
        )()

        window._finish_session_history_load(state)

        self.assertTrue(state.history_loaded)
        self.assertFalse(state.history_loading)
        window.set_current_session.assert_called_once_with("session-1")

    def test_available_session_skills_flushes_pending_runtime_reload(self):
        window = MainWindow.__new__(MainWindow)
        window.skill_manager = MagicMock()
        window.skill_manager.get_all_skills.return_value = [
            {"name": "claim-expert", "enabled": True},
            {"name": "disabled-skill", "enabled": False},
        ]
        setattr(window.skill_manager, "_skill_runtime_reload_pending", True)

        skills = window._available_session_skills()

        window.skill_manager.load_skills.assert_called_once()
        self.assertEqual([skill["name"] for skill in skills], ["claim-expert"])
        self.assertFalse(skill_runtime_reload_pending(window.skill_manager))

    def test_history_migration_removes_only_auto_query_skill_context(self):
        window = MainWindow.__new__(MainWindow)
        window.workspace_dir = ""
        window.sessions = {}
        window.chat_storage = MagicMock()
        window._compute_session_title = MagicMock(return_value="demo")
        auto = {
            "role": "system",
            "content": "AUTO",
            "meta": {"kind": "skill_context", "source": "skill_prompt_query_match"},
        }
        searched = {
            "role": "system",
            "content": "SEARCHED",
            "meta": {"kind": "skill_context", "source": "skill_prompt_tool_search"},
        }
        user = {"role": "user", "content": "hello"}

        migrated = window._normalize_and_persist_session_messages(
            "session-1",
            [user, auto, searched],
            existing_meta={"history_migration_version": 2},
        )

        self.assertEqual(migrated, [user, searched])
        saved_messages = window.chat_storage.save_conversation.call_args.args[1]
        self.assertEqual(saved_messages, [user, searched])

    def test_skill_center_tab_key_places_mcp_in_dedicated_tab(self):
        self.assertEqual(
            skill_center_tab_key({"name": "showdoc-mcp", "source_format": "mcp_server"}),
            "mcp",
        )
        self.assertEqual(
            skill_center_tab_key({"name": "claim-expert", "type": "ai_generated", "created_by": "ai"}),
            "custom",
        )
        self.assertEqual(
            skill_center_tab_key({"name": "document-reader", "source_type": "bundled_plugin"}),
            "optional",
        )
        self.assertEqual(skill_center_tab_key({"name": "filesystem"}), "builtin")

    def test_summarize_skill_terms_truncates_cleanly(self):
        summary = summarize_skill_terms(
            ["text_file_read", "text_file_write", "text_file_update", "workspace_rename_path"],
            max_items=3,
            max_chars=24,
        )
        self.assertTrue(summary.startswith("text_file_read"))
        self.assertTrue(summary.endswith("…"))
        self.assertNotIn("workspace_rename_path", summary)

    def test_open_settings_reloads_skills_only_when_required(self):
        window = MainWindow.__new__(MainWindow)
        window.config_manager = MagicMock()
        window.skill_manager = MagicMock()
        window.refresh_model_selector = MagicMock()
        window.refresh_context_badges = MagicMock()
        window.update_ui_state_for_workspace = MagicMock()
        window.input_field = None

        dialog = MagicMock()
        dialog.exec.return_value = QDialog.Accepted
        dialog.requires_skill_reload = False
        with patch("main.SettingsDialog", return_value=dialog):
            MainWindow.open_settings(window)
        window.skill_manager.load_skills.assert_not_called()

        dialog.requires_skill_reload = True
        with patch("main.SettingsDialog", return_value=dialog):
            MainWindow.open_settings(window)
        window.skill_manager.load_skills.assert_called_once()

    def test_skill_center_matches_filters_supports_query_and_status(self):
        skill = {
            "name": "file-system",
            "display_name": "文件整理与读写",
            "user_description": "提供工作区内统一的文件发现、读取、写入能力",
            "tools": ["text_file_read", "text_file_write"],
            "use_cases": ["整理文件", "读取文档"],
            "enabled": True,
        }
        self.assertTrue(skill_center_matches_filters(skill, query="text_file_read", status_filter="all"))
        self.assertTrue(skill_center_matches_filters(skill, query="文件整理", status_filter="enabled"))
        self.assertFalse(skill_center_matches_filters(skill, query="浏览器", status_filter="all"))
        self.assertFalse(skill_center_matches_filters(skill, query="text_file_read", status_filter="disabled"))
        self.tool_cards = {}
        self.last_agent_bubble = None
        self.llm_worker = None
        self.daemon_running = True
        self.sop_run = None

    def test_main_input_paste_prefers_plain_text_over_html(self):
        app = QApplication.instance() or QApplication([])
        edit = AutoResizingInputEdit()
        mime = QMimeData()
        mime.setHtml("<p><span style='font-size:24px;color:red;font-weight:bold'>粗体内容</span></p>")
        mime.setText("纯文本内容")

        edit.insertFromMimeData(mime)
        app.processEvents()

        self.assertEqual(edit.toPlainText(), "纯文本内容")
        self.assertNotIn("color:red", edit.toHtml())
        self.assertNotIn("font-size:24px", edit.toHtml())
        self.assertNotIn("<span", edit.toHtml())

    def test_main_input_paste_keeps_multiline_plain_text(self):
        app = QApplication.instance() or QApplication([])
        edit = AutoResizingInputEdit()
        mime = QMimeData()
        mime.setText("第一行\n第二行")

        edit.insertFromMimeData(mime)
        app.processEvents()

        self.assertEqual(edit.toPlainText(), "第一行\n第二行")

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

    def test_skill_center_list_item_uses_switch_without_export_button(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())

        item = dialog._build_skill_card(
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": True,
                "risk_level": "medium",
                "tools": ["review_claim"],
            }
        )

        self.assertEqual(item.objectName(), "SkillListItem")
        self.assertTrue(item.findChildren(AppleSwitch))
        self.assertFalse([button for button in item.findChildren(QPushButton) if button.text() == "导出"])

    def test_skill_center_switch_track_click_does_not_open_workbench(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)
        dialog.handle_skill_item_clicked = MagicMock()
        item = dialog._build_skill_card(
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": True,
                "risk_level": "medium",
                "tools": [],
                "type": "ai_generated",
                "created_by": "ai",
            }
        )
        item.show()
        app.processEvents()
        toggle = item.findChildren(AppleSwitch)[0]

        QTest.mouseClick(toggle, Qt.LeftButton, pos=QPoint(toggle.width() - 4, toggle.height() // 2))
        app.processEvents()

        config_manager.set_skill_enabled.assert_called_once_with("claim-expert", False)
        dialog.handle_skill_item_clicked.assert_not_called()

    def test_skill_center_has_builtin_mcp_and_custom_tabs(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())

        self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())], ["内置能力", "可选插件", "MCP", "自定义能力"])

    def test_skill_center_builtin_switch_is_read_only(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)

        item = dialog._build_skill_card(
            {
                "name": "filesystem",
                "display_name": "Filesystem",
                "description": "Built-in file access.",
                "enabled": True,
                "risk_level": "medium",
                "tools": ["text_file_read"],
            }
        )

        toggle = item.findChildren(AppleSwitch)[0]
        self.assertFalse(toggle.isEnabled())
        toggle.click()
        config_manager.set_skill_enabled.assert_not_called()

    def test_skill_center_mcp_switch_remains_mutable(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)

        item = dialog._build_skill_card(
            {
                "name": "showdoc-mcp",
                "display_name": "MCP / showdoc",
                "description": "Remote MCP tools.",
                "enabled": True,
                "risk_level": "medium",
                "tools": ["mcp_showdoc_list_items"],
                "source_format": "mcp_server",
            }
        )

        toggle = item.findChildren(AppleSwitch)[0]
        self.assertTrue(toggle.isEnabled())
        toggle.click()
        config_manager.set_skill_enabled.assert_called_once_with("showdoc-mcp", False)

    @patch("main.QMessageBox.information")
    def test_capability_workbench_renders_and_saves_skill_config(self, _info_mock):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.is_skill_editable.return_value = False
        skill_manager.get_skill_config_fields.return_value = []
        skill_manager.get_skill_config_status.return_value = {"missing_required": [], "complete": True}
        skill_manager.list_skill_files.return_value = {"ok": True, "editable": False, "files": []}
        skill_manager.get_tool_record.return_value = None
        config_manager = MagicMock()
        config_manager.get_skill_config.return_value = {"app_id": "cli_a", "app_secret": "old"}
        skill = {
            "name": "feishu-docs",
            "display_name": "飞书文档",
            "tools": [],
            "script_entries": [],
            "config_fields": [
                {"name": "app_id", "label": "App ID", "required": True, "env": "FEISHU_APP_ID"},
                {"name": "app_secret", "label": "App Secret", "kind": "secret", "required": True, "env": "FEISHU_APP_SECRET"},
            ],
        }

        dialog = CapabilityWorkbenchDialog(skill, skill_manager, config_manager)
        self.assertEqual(dialog.tabs.tabText(0), "配置")
        editors = dialog.findChildren(QLineEdit)
        secret_editors = [editor for editor in editors if editor.echoMode() == QLineEdit.Password]
        self.assertTrue(secret_editors)

        dialog.config_editors["app_secret"].setText("new-secret")
        dialog.save_skill_config()

        config_manager.set_skill_config.assert_called_once_with(
            "feishu-docs",
            {"app_id": "cli_a", "app_secret": "new-secret"},
        )

    def test_capability_workbench_reads_dict_tool_record_schema(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.is_skill_editable.return_value = False
        skill_manager.get_skill_config_fields.return_value = []
        skill_manager.get_skill_config_status.return_value = {"missing_required": [], "complete": True}
        skill_manager.list_skill_files.return_value = {"ok": True, "editable": False, "files": []}
        skill_manager.get_tool_record.return_value = {
            "parameters_schema": {
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"],
            }
        }
        config_manager = MagicMock()
        config_manager.get_skill_config.return_value = {}
        skill = {
            "name": "tencent-docs",
            "display_name": "Tencent Docs",
            "tools": ["run_skill_script"],
            "script_entries": [{"name": "setup", "path": "scripts/setup.js"}],
            "config_fields": [],
        }

        dialog = CapabilityWorkbenchDialog(skill, skill_manager, config_manager)

        self.assertIn("skill_name", dialog.tool_schema.toPlainText())
        self.assertEqual(dialog.script_combo.count(), 1)

    def test_capability_workbench_keeps_object_tool_record_schema_compatible(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.is_skill_editable.return_value = False
        skill_manager.get_skill_config_fields.return_value = []
        skill_manager.get_skill_config_status.return_value = {"missing_required": [], "complete": True}
        skill_manager.list_skill_files.return_value = {"ok": True, "editable": False, "files": []}
        skill_manager.get_tool_record.return_value = SimpleNamespace(
            parameters_schema={
                "type": "object",
                "properties": {"args": {"type": "array"}},
                "required": [],
            }
        )
        config_manager = MagicMock()
        config_manager.get_skill_config.return_value = {}
        skill = {
            "name": "feishu-docs",
            "display_name": "Feishu Docs",
            "tools": ["run_skill_script"],
            "script_entries": [],
            "config_fields": [],
        }

        dialog = CapabilityWorkbenchDialog(skill, skill_manager, config_manager)

        self.assertIn("args", dialog.tool_schema.toPlainText())

    def test_skill_center_toggle_defers_runtime_reload_until_next_use(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = [
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": True,
                "risk_level": "medium",
                "tools": [],
                "type": "ai_generated",
                "created_by": "ai",
            }
        ]
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)

        dialog.toggle_skill("claim-expert", False)

        config_manager.set_skill_enabled.assert_called_once_with("claim-expert", False)
        skill_manager.load_skills.assert_not_called()
        self.assertTrue(skill_runtime_reload_pending(skill_manager))
        self.assertFalse(dialog._all_skills[0]["enabled"])

    def test_skill_center_mcp_toggle_syncs_server_enabled_state(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = [
            {
                "name": "mcp-server-showdoc",
                "display_name": "MCP / showdoc",
                "description": "Remote MCP tools.",
                "enabled": True,
                "risk_level": "medium",
                "tools": ["mcp__showdoc__list_items"],
                "source_format": "mcp_server",
            }
        ]
        config_manager = MagicMock()
        config_manager.get_mcp_servers.return_value = [
            {
                "id": "showdoc",
                "name": "showdoc",
                "enabled": True,
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
            }
        ]
        dialog = SkillsCenterDialog(skill_manager, config_manager)

        dialog.toggle_skill("mcp-server-showdoc", False)

        updated_servers = config_manager.set_mcp_servers.call_args.args[0]
        self.assertFalse(updated_servers[0]["enabled"])
        config_manager.set_skill_enabled.assert_not_called()
        skill_manager.load_skills.assert_not_called()
        self.assertTrue(skill_runtime_reload_pending(skill_manager))

    def test_skill_center_toggle_updates_filtered_current_tab_without_full_reload(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = [
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": True,
                "risk_level": "medium",
                "tools": [],
                "type": "ai_generated",
                "created_by": "ai",
            }
        ]
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)
        dialog.tabs.setCurrentIndex(3)
        dialog.set_status_filter("enabled")

        dialog.toggle_skill("claim-expert", False)

        self.assertIn("显示 0 / 1 个能力", dialog.count_label.text())
        self.assertIn("下次使用时刷新", dialog.count_label.text())

    def test_skill_center_workbench_flushes_pending_reload_once(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.side_effect = [
            [
                {
                    "name": "claim-expert",
                    "display_name": "Claim Expert",
                    "description": "Review claim evidence and consistency.",
                    "enabled": True,
                    "risk_level": "medium",
                    "tools": [],
                    "type": "ai_generated",
                    "created_by": "ai",
                }
            ],
            [
                {
                    "name": "claim-expert",
                    "display_name": "Claim Expert",
                    "description": "Review claim evidence and consistency.",
                    "enabled": False,
                    "risk_level": "medium",
                    "tools": [],
                    "type": "ai_generated",
                    "created_by": "ai",
                }
            ],
            [
                {
                    "name": "claim-expert",
                    "display_name": "Claim Expert",
                    "description": "Review claim evidence and consistency.",
                    "enabled": False,
                    "risk_level": "medium",
                    "tools": [],
                    "type": "ai_generated",
                    "created_by": "ai",
                }
            ],
        ]
        config_manager = MagicMock()
        dialog = SkillsCenterDialog(skill_manager, config_manager)
        dialog.tabs.setCurrentIndex(3)
        dialog.toggle_skill("claim-expert", False)
        fake_workbench = MagicMock()
        fake_workbench.exec.return_value = None

        with patch("main.CapabilityWorkbenchDialog", return_value=fake_workbench) as workbench_cls:
            dialog.handle_skill_item_clicked({"name": "claim-expert", "enabled": True})

        skill_manager.load_skills.assert_called_once()
        self.assertFalse(skill_runtime_reload_pending(skill_manager))
        opened_skill = workbench_cls.call_args.args[0]
        self.assertFalse(opened_skill["enabled"])

    def test_skill_center_manual_refresh_clears_pending_reload_flag(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())
        dialog.toggle_skill("missing-skill", False)
        self.assertTrue(skill_runtime_reload_pending(skill_manager))

        with patch("main.QMessageBox.information"):
            dialog.manual_refresh()

        skill_manager.load_skills.assert_called_once()
        self.assertFalse(skill_runtime_reload_pending(skill_manager))

    def test_skill_center_selection_mode_adds_checkbox(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())
        dialog.selection_mode = True

        item = dialog._build_skill_card(
            {
                "name": "claim-expert",
                "display_name": "Claim Expert",
                "description": "Review claim evidence and consistency.",
                "enabled": False,
                "risk_level": "medium",
                "tools": [],
            }
        )

        self.assertTrue(item.findChildren(AppleSelectionCheck))
        self.assertTrue(item.findChildren(AppleSwitch))

    def test_skill_center_item_click_opens_workbench_in_normal_mode(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())
        dialog.refresh_list = MagicMock()
        fake_workbench = MagicMock()
        fake_workbench.exec.return_value = None

        with patch("main.CapabilityWorkbenchDialog", return_value=fake_workbench) as workbench_cls:
            dialog.handle_skill_item_clicked({"name": "claim-expert", "display_name": "Claim Expert"})

        workbench_cls.assert_called_once()
        fake_workbench.exec.assert_called_once()
        dialog.refresh_list.assert_called_once()

    def test_skill_center_item_click_selects_in_selection_mode(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())
        dialog.selection_mode = True
        dialog._render_skill_groups = MagicMock()

        with patch("main.CapabilityWorkbenchDialog") as workbench_cls:
            dialog.handle_skill_item_clicked({"name": "claim-expert", "display_name": "Claim Expert"})

        workbench_cls.assert_not_called()
        self.assertIn("claim-expert", dialog.selected_skill_names)

    def test_skill_center_selection_mode_shows_context_actions(self):
        app = QApplication.instance() or QApplication([])
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = []
        dialog = SkillsCenterDialog(skill_manager, MagicMock())

        self.assertTrue(dialog.selection_bar.isHidden())
        self.assertFalse(dialog.import_btn.isHidden())
        dialog.toggle_selection_mode()

        self.assertFalse(dialog.selection_bar.isHidden())
        self.assertTrue(dialog.import_btn.isHidden())
        self.assertTrue(dialog.more_btn.isHidden())
        self.assertFalse(dialog.export_selected_btn.isEnabled())
        self.assertFalse(dialog.delete_selected_btn.isEnabled())

        dialog.set_skill_selected("claim-expert", True)
        self.assertTrue(dialog.export_selected_btn.isEnabled())
        self.assertTrue(dialog.delete_selected_btn.isEnabled())


class _ObservabilityState:
    def __init__(self, session_id="session-1"):
        self.session_id = session_id
        self.system_prompt_text = ""
        self.runtime_context_text = ""
        self.prompt_cache_meta = {}
        self.system_prompt_appends = []
        self.observability_events = []
        self.token_usage_summary = normalize_token_usage_summary({})
        self.last_token_usage = {}
        self.persisted_conversation_meta = {}
        self.token_usage_label = None


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
    def _build_history_sidebar_window(self, conversations, query=""):
        window = MainWindow.__new__(MainWindow)
        window.history_container = QWidget()
        window.history_layout = QVBoxLayout(window.history_container)
        window.history_rows = {}
        window.history_buttons = {}
        window.project_rows = {}
        window.project_buttons = {}
        window.project_preview_paths = set()
        window.project_full_expanded_paths = set()
        window.unassigned_history_full_expanded = False
        window.current_session_id = ""
        window.current_project_path = ""
        window.workspace_dir = ""
        window.sidebar_sort_mode = "recent"
        window.chat_storage = MagicMock()
        window.chat_storage.list_conversations.return_value = list(conversations)
        window.chat_storage.search_conversations.return_value = [item["id"] for item in conversations]
        window.config_manager = MagicMock()
        window.config_manager.get_projects.return_value = []
        window.config_manager.get.return_value = []
        window._history_query_text = lambda: query
        window._conversation_workspace_path = lambda conv: conv.get("workspace_dir")
        window._project_key = lambda path: str(path or "")
        window._project_display_name = lambda path, project=None: str((project or {}).get("name") or path or "")
        window._normalize_project_path = lambda path: path or ""
        window._legacy_history_file_paths = lambda: []
        window._add_history_group_label = lambda text: window.history_layout.addWidget(QLabel(text))
        window._add_history_empty_state = lambda text: window.history_layout.addWidget(QLabel(text))
        window._make_project_row = lambda project, sessions, query="": QLabel(project.get("name") or "")
        window.update_history_selection = MagicMock()

        def _make_project_session_row(entry, compact=True):
            row = QWidget()
            row.setProperty("session_id", entry["id"])
            return row

        window._make_project_session_row = _make_project_session_row
        return window

    def _history_session_ids(self, window):
        session_ids = []
        for index in range(window.history_layout.count()):
            item = window.history_layout.itemAt(index)
            widget = item.widget()
            if widget and widget.property("session_id"):
                session_ids.append(widget.property("session_id"))
        return session_ids

    def _history_button_texts(self, window):
        if not hasattr(window, "history_layout"):
            return []
        texts = []
        for index in range(window.history_layout.count()):
            item = window.history_layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, QPushButton):
                texts.append(widget.text().strip())
        return texts

    def test_empty_history_does_not_advertise_legacy_json_migration(self):
        QApplication.instance() or QApplication([])
        window = self._build_history_sidebar_window([])
        window._legacy_history_file_paths = lambda: ["chat_history_legacy.json"]

        window.refresh_history_list()

        labels = [
            window.history_layout.itemAt(index).widget().text()
            for index in range(window.history_layout.count())
            if isinstance(window.history_layout.itemAt(index).widget(), QLabel)
        ]
        self.assertIn("还没有项目，点击项目标题栏的文件夹按钮添加", labels)
        self.assertFalse(any("迁移旧版 JSON 历史" in text for text in labels))

    def test_unassigned_conversations_default_to_preview_with_expand_button(self):
        conversations = [
            {"id": f"session-{idx}", "title": f"Task {idx}", "updated_at": 100 - idx, "status": "draft"}
            for idx in range(5)
        ]
        window = self._build_history_sidebar_window(conversations)

        window.refresh_history_list()

        self.assertEqual(self._history_session_ids(window), ["session-0", "session-1", "session-2"])
        self.assertIn("展开显示", self._history_button_texts(window))

    def test_unassigned_conversations_expand_to_full_list(self):
        conversations = [
            {"id": f"session-{idx}", "title": f"Task {idx}", "updated_at": 100 - idx, "status": "draft"}
            for idx in range(5)
        ]
        window = self._build_history_sidebar_window(conversations)
        window.set_unassigned_history_full_expanded(True, refresh=False)

        window.refresh_history_list()

        self.assertEqual(
            self._history_session_ids(window),
            ["session-0", "session-1", "session-2", "session-3", "session-4"],
        )
        self.assertIn("收起全部", self._history_button_texts(window))

    def test_unassigned_conversations_show_all_matches_during_search(self):
        conversations = [
            {"id": f"session-{idx}", "title": f"Task {idx}", "updated_at": 100 - idx, "status": "draft"}
            for idx in range(5)
        ]
        window = self._build_history_sidebar_window(conversations, query="task")

        window.refresh_history_list()

        self.assertEqual(
            self._history_session_ids(window),
            ["session-0", "session-1", "session-2", "session-3", "session-4"],
        )

    def test_handle_project_click_only_toggles_project_visibility(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        active_workspace = os.path.join(temp_dir, "active")
        clicked_workspace = os.path.join(temp_dir, "clicked")
        os.makedirs(active_workspace)
        os.makedirs(clicked_workspace)

        window = MainWindow.__new__(MainWindow)
        window.project_preview_paths = set()
        window.project_full_expanded_paths = set()
        window.current_project_path = ""
        window.current_session_id = "current"
        window.workspace_dir = active_workspace
        window.sessions = {
            "current": type(
                "_Session",
                (),
                {"workspace_dir": active_workspace, "persisted_conversation_meta": {"workspace_dir": active_workspace}},
            )()
        }
        window.config_manager = MagicMock()
        window.refresh_history_list = MagicMock()

        ok = window.handle_project_click(clicked_workspace)

        self.assertTrue(ok)
        self.assertEqual(window.current_project_path, os.path.normpath(clicked_workspace))
        self.assertEqual(window.sessions["current"].workspace_dir, active_workspace)
        self.assertEqual(window.workspace_dir, active_workspace)
        self.assertIn(os.path.normpath(clicked_workspace), window.project_preview_paths)
        window.refresh_history_list.assert_called_once()
        window.config_manager.upsert_project.assert_called_once_with(os.path.normpath(clicked_workspace))

    def test_compose_session_meta_keeps_session_workspace(self):
        window = MainWindow.__new__(MainWindow)
        session_workspace = os.path.normpath("D:/workspace/session")
        window.workspace_dir = os.path.normpath("D:/workspace/window")
        state = type(
            "_Session",
            (),
            {
                "workspace_dir": session_workspace,
                "persisted_conversation_meta": {"workspace_dir": session_workspace},
                "run_phase": "Idle",
                "session_status": "draft",
                "has_file_changes": False,
                "clarify_mode_enabled": False,
                "pending_clarify_questions": [],
                "selected_skill_names": [],
                "workflow_mode": "",
                "office_output_profile": OFFICE_OUTPUT_PROFILE_FREE,
                "sop_run": None,
            },
        )()

        meta = window._compose_session_meta(state)

        self.assertEqual(meta["workspace_dir"], session_workspace)
        self.assertNotIn("workflow_mode", meta)
        self.assertNotIn("office_output_profile", meta)

    def test_new_conversation_for_project_binds_new_session_workspace(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        active_workspace = os.path.join(temp_dir, "active")
        target_workspace = os.path.join(temp_dir, "target")
        os.makedirs(active_workspace)
        os.makedirs(target_workspace)

        window = MainWindow.__new__(MainWindow)
        window.current_project_path = ""
        window.current_session_id = "current"
        window.workspace_dir = active_workspace
        window.sessions = {
            "current": type(
                "_Session",
                (),
                {"workspace_dir": active_workspace, "persisted_conversation_meta": {"workspace_dir": active_workspace}},
            )()
        }
        window.config_manager = MagicMock()
        window.refresh_history_list = MagicMock()
        captured = {}

        def create_new_session(session_id=None, title=None, make_current=True, workspace_dir=None):
            captured["workspace_dir"] = workspace_dir
            return "new-session"

        window.create_new_session = create_new_session

        window.new_conversation_for_project(target_workspace)

        self.assertEqual(captured["workspace_dir"], os.path.normpath(target_workspace))
        self.assertEqual(window.sessions["current"].workspace_dir, active_workspace)
        window.config_manager.upsert_project.assert_called_once_with(os.path.normpath(target_workspace))
        self.assertNotIn("展开显示", self._history_button_texts(window))
        self.assertNotIn("收起全部", self._history_button_texts(window))

    def test_top_new_conversation_creates_unassigned_session(self):
        window = MainWindow.__new__(MainWindow)
        window.create_new_session = MagicMock(return_value="chat-only")
        window.refresh_history_list = MagicMock()

        window.new_conversation()

        window.create_new_session.assert_called_once_with(workspace_dir="")
        window.refresh_history_list.assert_called_once()

    def test_build_run_context_marks_chat_only_without_workspace(self):
        window = MainWindow.__new__(MainWindow)
        window.config_manager = MagicMock()
        window.config_manager.get_selected_model_id.return_value = "model"
        window._effective_sop_skill_names = MagicMock(return_value=[])
        state = type(
            "_Session",
            (),
            {
                "workspace_dir": "",
                "persisted_conversation_meta": {},
                "clarify_mode_state": "exploring",
                "pending_clarify_questions": [],
                "sop_run": None,
            },
        )()

        context = window._build_run_context(state, "execution")

        self.assertEqual(context["workspace_mode"], "chat_only")
        self.assertEqual(context["workflow_mode"], "")
        self.assertEqual(context["office_output_profile"], OFFICE_OUTPUT_PROFILE_FREE)

        context = window._build_run_context(
            state,
            "execution",
            workflow_mode=WORKFLOW_MODE_OFFICE_HTML_FIRST,
            office_output_profile=OFFICE_OUTPUT_PROFILE_PPT,
        )
        self.assertEqual(context["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
        self.assertEqual(context["office_output_profile"], OFFICE_OUTPUT_PROFILE_PPT)

        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "draft.html")
            template_path = os.path.join(tmp, "template.pptx")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            with open(template_path, "wb") as handle:
                handle.write(b"pptx")
            state.office_conversion_source_files = [html_path]
            state.office_conversion_template_file = template_path
            state.office_task_target_format = "pptx"

            context = window._build_run_context(
                state,
                "execution",
                workflow_mode=WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
                office_conversion_target="pptx",
            )

        self.assertEqual(context["workflow_mode"], WORKFLOW_MODE_OFFICE_FILE_CONVERSION)
        self.assertEqual(context["office_conversion_target"], "pptx")
        self.assertEqual(context["office_source_files"], [os.path.normpath(html_path)])
        self.assertEqual(context["office_template_file"], template_path)

    def test_session_activity_indicator_runs_only_for_live_runtime_state(self):
        window = MainWindow.__new__(MainWindow)
        state = _HistoryActionState("session-1")
        state.session_status = "running"
        state.live_activity = False
        window.sessions = {state.session_id: state}
        window.current_session_id = ""

        # A persisted status alone must not create a false spinner after restart.
        self.assertFalse(window._session_has_live_activity(state.session_id))

        state.live_activity = True
        self.assertTrue(window._session_has_live_activity(state.session_id))

    def test_session_activity_indicator_is_safe_when_initially_hidden(self):
        app = QApplication.instance() or QApplication([])

        indicator = SessionActivityIndicator()

        self.assertFalse(indicator.isVisible())
        self.assertFalse(indicator._timer.isActive())

    def test_startup_loading_window_starts_indicator_when_shown(self):
        app = QApplication.instance() or QApplication([])
        window = StartupLoadingWindow()

        try:
            self.assertTrue(window.indicator._running)
            self.assertFalse(window.indicator._timer.isActive())

            window.show_centered()
            app.processEvents()

            self.assertTrue(window.isVisible())
            self.assertTrue(window.indicator._timer.isActive())
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()

    def test_schedule_main_window_startup_defers_window_construction(self):
        app = QApplication.instance() or QApplication([])
        old_main_window = getattr(app, "main_window", None)
        startup_window = MagicMock()
        server = MagicMock()
        pending_activation = {"requested": True}

        try:
            with patch("main.QTimer.singleShot") as single_shot, \
                 patch("main.MainWindow") as main_window_cls, \
                 patch("main.finish_startup_loading_window") as finish_startup:
                fake_window = MagicMock()
                main_window_cls.return_value = fake_window

                holder = schedule_main_window_startup(app, startup_window, server, True, pending_activation)

                main_window_cls.assert_not_called()
                self.assertIsNone(holder["window"])
                single_shot.assert_called_once()
                self.assertEqual(single_shot.call_args.args[0], 0)

                callback = single_shot.call_args.args[1]
                callback()

            self.assertIs(holder["window"], fake_window)
            self.assertIs(app.main_window, fake_window)
            fake_window.attach_single_instance_server.assert_called_once_with(server)
            fake_window.showMaximized.assert_called_once()
            finish_startup.assert_called_once_with(startup_window)
            fake_window.activate_existing_window.assert_called_once()
        finally:
            app.main_window = old_main_window

    def test_ui_exception_log_keeps_full_traceback_context(self):
        receiver = QWidget()
        event = QEvent(QEvent.Hide)
        traceback_text = "Traceback (most recent call last):\nAttributeError: timer missing"

        with patch("main.append_background_process_log") as append_log:
            log_ui_exception(receiver, event, traceback_text)

        filename, message = append_log.call_args.args
        self.assertEqual(filename, UI_ERROR_LOG_FILENAME)
        self.assertIn("receiver=QWidget", message)
        self.assertIn("event=", message)
        self.assertIn(traceback_text, message)

    def test_set_session_status_updates_sidebar_activity_without_rebuilding_history(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        state = _HistoryActionState("session-1")
        state.session_status = "draft"
        state.live_activity = False
        window.sessions = {state.session_id: state}
        window.current_session_id = ""
        indicator = MagicMock(spec=SessionActivityIndicator)
        age_label = QLabel("1 天")
        window.history_activity_indicators = {state.session_id: indicator}
        window.history_age_labels = {state.session_id: age_label}
        window._mark_session_automation_completed = MagicMock()
        window.refresh_history_list = MagicMock()

        window.set_session_status("running", state.session_id)

        self.assertTrue(state.live_activity)
        indicator.setRunning.assert_called_with(True)
        self.assertFalse(age_label.isVisible())
        window.refresh_history_list.assert_not_called()

        window.set_session_status("completed", state.session_id)

        self.assertFalse(state.live_activity)
        indicator.setRunning.assert_called_with(False)
        window.refresh_history_list.assert_not_called()

    def test_save_chat_history_enqueue_path_avoids_direct_db_write(self):
        class _Worker:
            def __init__(self):
                self.requests = []

            def enqueue(self, request):
                self.requests.append(request)

        window = MainWindow.__new__(MainWindow)
        state = type(
            "_Session",
            (),
            {
                "session_id": "session-1",
                "messages": [{"id": "m1", "role": "user", "content": "hello"}],
                "clarify_mode_enabled": False,
                "pending_clarify_questions": [],
                "selected_skill_names": [],
                "workflow_mode": "",
                "office_output_profile": OFFICE_OUTPUT_PROFILE_FREE,
                "sop_run": None,
                "persisted_conversation_meta": {},
                "run_phase": "Idle",
                "session_status": "draft",
                "has_file_changes": False,
            },
        )()
        window.workspace_dir = "D:/workspace"
        window.chat_storage = MagicMock()
        window.chat_save_worker = _Worker()
        window.get_session = MagicMock(return_value=state)
        window._compute_session_title = MagicMock(return_value="hello")
        window._session_clarify_meta = MagicMock(return_value={})
        window._session_selected_skills_meta = MagicMock(return_value={})
        window._session_sop_meta = MagicMock(return_value={})
        window.update_skill_capture_button_state = MagicMock()

        result = window.save_chat_history(session_id="session-1")

        self.assertTrue(result)
        self.assertEqual(len(window.chat_save_worker.requests), 1)
        window.chat_storage.save_conversation.assert_not_called()

    def test_prompt_tool_menu_order_helper_matches_spec(self):
        window = MainWindow.__new__(MainWindow)
        entries = window._prompt_tool_menu_entries()
        self.assertEqual(
            [label for _key, label in entries],
            ["添加文件", "添加智能体", "指定能力"],
        )
        self.assertNotIn("反问模式", [label for _key, label in entries])
        self.assertNotIn("添加自动化", [label for _key, label in entries])
        self.assertNotIn("从对话生成 SOP", [label for _key, label in entries])
        self.assertNotIn("能力中心", [label for _key, label in entries])

    def test_agent_bubble_shows_office_draft_action_for_final_content(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("Agent", "")

        try:
            self.assertTrue(bubble.office_draft_btn.isHidden())
            bubble.set_main_content("这是一段可以生成办公稿的回复", final=True)
            app.processEvents()

            self.assertFalse(bubble.copy_result_btn.isHidden())
            self.assertFalse(bubble.office_draft_btn.isHidden())
            self.assertEqual(bubble.office_draft_btn.text(), "生成办公稿")
        finally:
            bubble.deleteLater()
            app.processEvents()

    def test_agent_bubble_office_draft_menu_emits_selected_profile(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("Agent", "")
        emitted = []
        bubble.officeDraftRequested.connect(lambda profile, msg_id, text: emitted.append((profile, msg_id, text)))

        try:
            bubble.set_source_message_id("assistant-1")
            bubble.set_main_content("把这段内容做成演示稿", final=True)
            bubble._emit_office_draft_request(OFFICE_OUTPUT_PROFILE_PPT)

            self.assertEqual(emitted, [(OFFICE_OUTPUT_PROFILE_PPT, "assistant-1", "把这段内容做成演示稿")])
        finally:
            bubble.deleteLater()
            app.processEvents()

    def test_office_draft_task_card_keeps_result_visible_when_process_collapsed(self):
        app = QApplication.instance() or QApplication([])
        card = OfficeDraftTaskCard("自由")
        activated = []
        html_path = os.path.join(tempfile.gettempdir(), "office-draft-result.html")
        card.deliverablePathActivated.connect(activated.append)

        try:
            card.set_completed([html_path])
            app.processEvents()

            self.assertTrue(card.process_container.isHidden())
            self.assertFalse(card.result_container.isHidden())
            label_text = "\n".join(label.text() for label in card.findChildren(QLabel))
            self.assertNotIn("生成过程已折叠", label_text)
            self.assertEqual(card.result_layout.count(), 1)
            result_button = card.result_layout.itemAt(0).widget()
            self.assertIsNotNone(result_button)
            self.assertIn("office-draft-result.html", result_button.text())

            card.toggle_btn.setChecked(True)
            app.processEvents()
            self.assertFalse(card.process_container.isHidden())
            self.assertFalse(card.result_container.isHidden())

            result_button.click()
            self.assertEqual(activated, [html_path])
        finally:
            card.deleteLater()
            app.processEvents()

    def test_office_draft_task_card_syncs_visible_result_paths(self):
        app = QApplication.instance() or QApplication([])
        card = OfficeDraftTaskCard("PPT", target_format="pptx")
        first = os.path.join(tempfile.gettempdir(), "deck.pptx")
        second = os.path.join(tempfile.gettempdir(), "deck-final.pptx")

        try:
            card.set_completed([first])
            card.add_result_paths([first, second])
            app.processEvents()

            self.assertEqual(card.title_label.text(), "已生成 PPTX")
            self.assertFalse(card.result_container.isHidden())
            self.assertEqual(card.result_layout.count(), 2)
            label_text = "\n".join(label.text() for label in card.findChildren(QLabel))
            self.assertNotIn("生成过程已折叠", label_text)
        finally:
            card.deleteLater()
            app.processEvents()

    def test_office_task_finish_collects_paths_from_hidden_bubble(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as workspace:
            html_path = os.path.join(workspace, "draft.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            card = OfficeDraftTaskCard("自由")
            state = type(
                "_Session",
                (),
                {
                    "office_draft_task_card": card,
                    "office_task_result_paths": [],
                    "changed_files": [],
                },
            )()
            bubble = type("_Bubble", (), {"_deliverable_paths": [html_path]})()
            window = MainWindow.__new__(MainWindow)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)

            try:
                window._finish_office_draft_task_card(state, content="生成完成", bubble=bubble)
                app.processEvents()

                self.assertEqual(card.result_layout.count(), 1)
                self.assertFalse(card.result_container.isHidden())
                result_button = card.result_layout.itemAt(0).widget()
                self.assertIn("draft.html", result_button.text())
            finally:
                card.deleteLater()
                app.processEvents()

    def test_render_session_history_spans_keeps_later_office_task_collapsed(self):
        app = QApplication.instance() or QApplication([])
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        deck_path = os.path.join(temp_dir, "deck.pptx")
        with open(deck_path, "wb") as handle:
            handle.write(b"pptx")
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addStretch()
        state = type(
            "_Session",
            (),
            {
                "session_id": "session-1",
                "chat_layout": layout,
                "messages": [
                    {"id": "u0", "role": "user", "content": "普通消息"},
                    {
                        "id": "u1",
                        "role": "user",
                        "content": "生成 PPTX",
                        "meta": {
                            "workflow_mode": WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
                            "office_conversion_target": "pptx",
                        },
                    },
                    {
                        "id": "a1",
                        "role": "assistant",
                        "content": f"已生成 {deck_path}",
                    },
                ],
                "last_agent_bubble": None,
                "tool_cards": {},
                "empty_state": None,
                "office_draft_task_card": None,
            },
        )()
        window = MainWindow.__new__(MainWindow)
        window.sessions = {"session-1": state}
        window.current_session_id = "session-1"
        window.last_message_time = 0
        window.dynamic_message_width = 760
        window.dynamic_user_bubble_width = 620
        window._workspace_dir_for_state = MagicMock(return_value=temp_dir)
        window.process_ui_events = MagicMock()
        window.request_session_scroll_to_bottom = MagicMock()
        window.queue_session_bubble_virtualization = MagicMock()
        window.open_deliverable_from_chat = MagicMock()
        window.handle_chat_deliverable_paths_changed = MagicMock()
        window.handle_office_draft_requested = MagicMock()

        try:
            spans = [
                {"start": 0, "end": 1},
                {"start": 1, "end": 3},
            ]
            window._render_session_history_spans(state, spans)
            app.processEvents()

            office_cards = host.findChildren(OfficeDraftTaskCard)
            self.assertEqual(len(office_cards), 1)
            self.assertEqual(office_cards[0].title_label.text(), "已生成 PPTX")
            self.assertTrue(office_cards[0].process_container.isHidden())
        finally:
            host.deleteLater()
            app.processEvents()

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
        window.hide_context_drawer.assert_not_called()

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
        self.assertIsNotNone(toast.surface.graphicsEffect())

        text_label = toast.findChild(QLabel, "SystemToastText")
        self.assertIsNotNone(text_label)
        self.assertEqual("已绑定自动化，正在等待下一步执行说明", text_label.text())

    def _make_floating_toast_window(self):
        QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window.main_container = QWidget(window)
        window.main_container.resize(1000, 700)
        window.conversation_column = QWidget(window.main_container)
        window.conversation_column.setGeometry(140, 80, 720, 560)
        current_state = type(
            "_ToastState",
            (),
            {"session_id": "current", "chat_layout": MagicMock()},
        )()
        background_state = type(
            "_ToastState",
            (),
            {"session_id": "background", "chat_layout": MagicMock()},
        )()
        window.sessions = {
            current_state.session_id: current_state,
            background_state.session_id: background_state,
        }
        window.current_session_id = current_state.session_id
        window._system_toast_queue = []
        window._active_system_toast = None
        window._system_toast_animation = None
        window._system_toast_position_animation = None
        window._system_toast_animation_phase = None
        window._system_toast_timer = QTimer(window)
        window._system_toast_timer.setSingleShot(True)
        window._system_toast_timer.timeout.connect(window._dismiss_active_system_toast)
        window.request_session_scroll_to_bottom = MagicMock()

        def cleanup():
            animation = getattr(window, "_system_toast_animation", None)
            if animation is not None:
                animation.stop()
            window._system_toast_timer.stop()

        self.addCleanup(cleanup)
        return window, current_state, background_state

    def test_system_toast_uses_type_defaults_and_preserves_explicit_duration(self):
        window, _current_state, _background_state = self._make_floating_toast_window()

        self.assertEqual(DesignTokens.toast_default_duration_ms, window._system_toast_duration("info"))
        self.assertEqual(DesignTokens.toast_default_duration_ms, window._system_toast_duration("warning"))
        self.assertEqual(DesignTokens.toast_error_duration_ms, window._system_toast_duration("error"))
        self.assertEqual(2350, window._system_toast_duration("error", 2350))

    def test_system_toast_floats_above_main_content_without_touching_chat_layout(self):
        window, _current_state, background_state = self._make_floating_toast_window()

        window.add_system_toast(
            "后台任务已完成",
            "success",
            session_id=background_state.session_id,
            auto_close_ms=2400,
        )

        toast = window._active_system_toast
        self.assertIsNotNone(toast)
        self.assertIs(toast.parentWidget(), window.main_container)
        self.assertEqual("后台任务已完成", toast.message_label.text())
        self.assertEqual(2400, toast._display_duration_ms)
        self.assertEqual(DesignTokens.toast_top_margin - DesignTokens.toast_slide_distance, toast.y())
        background_state.chat_layout.insertWidget.assert_not_called()
        window.request_session_scroll_to_bottom.assert_not_called()

    def test_system_toast_queues_one_at_a_time_and_advances_fifo(self):
        window, current_state, background_state = self._make_floating_toast_window()

        window.add_system_toast("第一条", "info", session_id=current_state.session_id)
        first_toast = window._active_system_toast
        window.add_system_toast("第二条", "error", session_id=background_state.session_id)

        self.assertEqual("第一条", first_toast.message_label.text())
        self.assertEqual(1, len(window._system_toast_queue))
        self.assertEqual("第二条", window._system_toast_queue[0]["text"])

        window._complete_system_toast_exit(first_toast)

        self.assertIsNot(first_toast, window._active_system_toast)
        self.assertEqual("第二条", window._active_system_toast.message_label.text())
        self.assertEqual([], window._system_toast_queue)

    def test_system_toast_entry_animation_is_subtle_and_repositions_with_column(self):
        window, current_state, _background_state = self._make_floating_toast_window()

        window.add_system_toast("位置测试", "info", session_id=current_state.session_id)

        animation = window._system_toast_animation
        position_animation = window._system_toast_position_animation
        self.assertEqual(2, animation.animationCount())
        self.assertEqual(DesignTokens.toast_enter_duration_ms, position_animation.duration())
        self.assertEqual(
            DesignTokens.toast_slide_distance,
            position_animation.endValue().y() - position_animation.startValue().y(),
        )

        previous_end_x = position_animation.endValue().x()
        window.conversation_column.setGeometry(220, 80, 700, 560)
        window._position_active_system_toast()

        self.assertGreater(position_animation.endValue().x(), previous_end_x)

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
        window.show_context_drawer.assert_not_called()

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

    def test_chat_bubble_user_shows_edit_and_delete_actions(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("User", "hello", source_message_id="u1")

        self.assertIsNotNone(app)
        self.assertIsNotNone(bubble.edit_btn)
        self.assertIsNotNone(bubble.delete_btn)
        self.assertFalse(bubble.edit_btn.isHidden())
        self.assertFalse(bubble.delete_btn.isHidden())

    def test_chat_bubble_inline_edit_can_cancel_or_submit(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("User", "hello", source_message_id="u1")
        submitted = []
        bubble.editSubmitRequested.connect(lambda message_id, text: submitted.append((message_id, text)))

        bubble.begin_inline_edit()
        self.assertFalse(bubble.user_content_edit.isReadOnly())
        bubble.user_content_edit.setPlainText("changed")
        bubble.submit_inline_edit()
        self.assertEqual(submitted, [("u1", "changed")])

        bubble.cancel_inline_edit()
        self.assertTrue(bubble.user_content_edit.isReadOnly())
        self.assertEqual(bubble.user_content_edit.toPlainText(), "hello")

    def test_conversation_history_row_reveals_reserved_hover_actions(self):
        app = QApplication.instance() or QApplication([])
        row = ConversationHistoryRow()
        action = QPushButton(row)
        row.set_hover_actions([action])

        self.assertTrue(action.isHidden())
        row._set_actions_visible(True)
        self.assertFalse(action.isHidden())

    def test_sidebar_hover_tip_uses_in_window_bubble(self):
        app = QApplication.instance() or QApplication([])
        host = QWidget()
        host.resize(240, 120)
        button = QToolButton(host)
        button.setGeometry(190, 40, 26, 26)
        controller = SidebarHoverTipController(host)

        controller.register(button, "项目操作")
        host.show()
        button.show()
        app.processEvents()
        controller._pending_widget = button
        with patch.object(button, "underMouse", return_value=True):
            controller._show_pending()

        self.assertEqual(button.toolTip(), "")
        self.assertEqual(button.accessibleName(), "项目操作")
        self.assertEqual(controller.bubble.parent(), host)
        self.assertEqual(controller.bubble.text(), "项目操作")
        self.assertFalse(controller.bubble.isHidden())
        self.assertLessEqual(controller.bubble.geometry().right(), host.width())

        controller.hide()
        self.assertTrue(controller.bubble.isHidden())

    def test_sidebar_hover_tip_rejects_empty_text(self):
        app = QApplication.instance() or QApplication([])
        host = QWidget()
        controller = SidebarHoverTipController(host)

        with self.assertRaisesRegex(ValueError, "不能为空"):
            controller.register(QToolButton(host), "")

    def test_chat_bubble_user_wraps_long_hyphenated_text_without_truncation(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("User", "macro-policy-analysis-demo-token", source_message_id="u1")

        bubble.apply_dynamic_widths(760, 220)
        bubble.show()
        app.processEvents()

        self.assertIsInstance(bubble.user_content_edit, AutoResizingTextEdit)
        self.assertEqual(bubble.user_content_edit.toPlainText(), "macro-policy-analysis-demo-token")
        self.assertEqual(
            bubble.user_content_edit.wordWrapMode(),
            QTextOption.WrapAtWordBoundaryOrAnywhere,
        )
        self.assertLessEqual(bubble.user_content_edit.width(), 190)
        self.assertGreater(bubble.user_content_edit.height(), 24)

    def test_chat_bubble_user_wraps_full_chinese_question(self):
        app = QApplication.instance() or QApplication([])
        prompt = (
            "用户提问突然就消失了，应该是要自适应换行，并且展示用户提问的所有文字。"
            "请确认这段较长的问题在蓝色气泡里完整显示，不要被裁剪。"
            "macro-policy-analysis-demo-token"
        )
        bubble = ChatBubble("User", prompt, source_message_id="u1")

        bubble.apply_dynamic_widths(760, 260)
        bubble.show()
        app.processEvents()

        self.assertIsInstance(bubble.user_content_edit, AutoResizingTextEdit)
        self.assertEqual(bubble.user_content_edit.toPlainText(), prompt)
        self.assertEqual(
            bubble.user_content_edit.wordWrapMode(),
            QTextOption.WrapAtWordBoundaryOrAnywhere,
        )
        self.assertLessEqual(bubble.user_content_edit.width(), 230)
        self.assertGreater(bubble.user_content_edit.height(), 24)

    def test_chat_bubble_agent_preserves_markdown_for_long_final_content(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("Agent", "")

        bubble.set_main_content("# Title\n\n" + ("- item\n" * 180), final=True)
        bubble.show()
        app.processEvents()

        self.assertNotIsInstance(bubble.content_edit, AutoResizingPlainTextEdit)
        self.assertIn("Title", bubble.content_edit.toPlainText())
        self.assertEqual(bubble.content_edit.toPlainText().count("item"), 180)

    def test_markdown_render_cache_reuses_same_text(self):
        _MARKDOWN_RENDER_CACHE.clear()
        with patch("main.markdown.markdown", return_value="<p>cached</p>") as markdown_mock:
            first_mode, first_html = render_markdown_or_html_with_cache("**cached**", final=True)
            second_mode, second_html = render_markdown_or_html_with_cache("**cached**", final=True)

        self.assertEqual(first_mode, "html")
        self.assertEqual(first_html, second_html)
        markdown_mock.assert_called_once()

    def test_chat_bubble_virtualization_hides_and_restores_content(self):
        app = QApplication.instance() or QApplication([])
        bubble = ChatBubble("User", "hello", source_message_id="u1")
        bubble.show()
        app.processEvents()

        bubble.set_virtualized(True)
        self.assertTrue(bubble.is_virtualized())
        self.assertFalse(bubble.content_wrapper.isVisible())

        bubble.set_virtualized(False)
        app.processEvents()

        self.assertFalse(bubble.is_virtualized())
        self.assertTrue(bubble.content_wrapper.isVisible())

    def test_virtualize_session_bubbles_skips_active_tail_bubble(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        host = QWidget()
        layout = QVBoxLayout(host)
        scroll = QScrollArea()
        scroll.setWidget(host)
        state = SessionState("session-1", layout, QLabel(), QWidget(), scroll)
        window.sessions = {"session-1": state}
        window.get_session = lambda sid=None: state

        bubbles = [ChatBubble("User", f"msg {index}") for index in range(52)]
        for bubble in bubbles:
            layout.addWidget(bubble)
        state.last_agent_bubble = bubbles[-1]
        for index, bubble in enumerate(bubbles):
            bubble.move(0, index * 60)
            bubble.resize(300, 40)

        scroll.setWidgetResizable(True)
        scroll.resize(320, 160)
        scroll.show()
        app.processEvents()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        window.virtualize_session_bubbles("session-1")

        self.assertFalse(bubbles[-1].is_virtualized())
        self.assertFalse(bubbles[0].is_virtualized())
        self.assertTrue(any(bubble.is_virtualized() for bubble in bubbles[:-10]))

    def test_add_chat_bubble_hides_empty_state_before_hidden_session_page_is_shown(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        host = QWidget()
        layout = QVBoxLayout(host)
        empty_state = QLabel("empty")
        layout.addWidget(empty_state)
        empty_state.show()
        app.processEvents()

        state = SessionState("session-1", layout, QLabel(), host, QScrollArea())
        state.empty_state = empty_state
        window.get_current_session = lambda: state
        window._workspace_dir_for_state = lambda _state=None: ""
        window.dynamic_message_width = 720
        window.dynamic_user_bubble_width = 520
        window.last_message_time = 0
        window.process_ui_events = MagicMock()
        window.request_session_scroll_to_bottom = MagicMock()
        window.queue_session_bubble_virtualization = MagicMock()

        self.assertFalse(empty_state.isVisible())
        self.assertFalse(empty_state.isHidden())

        bubble = window.add_chat_bubble("User", "rewritten first message", source_message_id="u1")
        host.show()
        app.processEvents()

        self.assertTrue(empty_state.isHidden())
        self.assertFalse(empty_state.isVisible())
        self.assertTrue(bubble.isVisible())
        host.deleteLater()

    def test_edit_user_message_inline_truncates_current_session_and_resubmits(self):
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
        window.add_system_toast = MagicMock()
        window._submit_session_request = MagicMock(return_value=True)
        window._render_rewritten_session = MagicMock()
        window.sessions = {}

        parent_state = _HistoryActionState("parent", parent_messages)
        window.get_session = lambda session_id=None: parent_state if session_id == "parent" else None

        ok = window.edit_user_message_inline("parent", "u2", "rewritten second")

        self.assertTrue(ok)
        self.assertEqual(window.save_chat_history.call_count, 2)
        window.save_chat_history.assert_any_call(session_id="parent", flush=True)
        window.refresh_history_list.assert_called_once()
        window._submit_session_request.assert_called_once()
        window._render_rewritten_session.assert_called_once_with(parent_state, parent_messages[:2])
        submit_args = window._submit_session_request.call_args

        self.assertEqual(submit_args.args[0].session_id, "parent")
        self.assertEqual(submit_args.args[1], "rewritten second")
        self.assertEqual(submit_args.args[2], [attachment_path])
        self.assertFalse(submit_args.kwargs["check_duplicates"])

    def test_delete_user_message_in_place_preserves_following_messages(self):
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
        window.add_system_toast = MagicMock()
        window._submit_session_request = MagicMock()
        window._render_rewritten_session = MagicMock()
        window.input_field = MagicMock()
        window.sessions = {}

        parent_state = _HistoryActionState("parent", parent_messages)
        window.get_session = lambda session_id=None: parent_state if session_id == "parent" else None

        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            ok = window.delete_user_message_in_place("parent", "u2")

        self.assertTrue(ok)
        self.assertEqual(window.save_chat_history.call_count, 2)
        window.save_chat_history.assert_any_call(session_id="parent", flush=True)
        window.refresh_history_list.assert_called_once()
        window._submit_session_request.assert_not_called()
        window.input_field.setFocus.assert_called_once()
        remaining = window._render_rewritten_session.call_args.args[1]
        self.assertEqual([msg["content"] for msg in remaining], ["first", "reply", "after second"])

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

    def test_token_usage_formatting_includes_cache_summary(self):
        summary = {
            "input_tokens": 12000,
            "output_tokens": 400,
            "total_tokens": 12400,
            "cached_input_tokens": 7500,
            "uncached_input_tokens": 4500,
            "request_count": 2,
        }

        self.assertEqual(format_token_usage_chip_text(summary), "12.4K tokens · 缓存 7.5K / 62%")
        tooltip = format_token_usage_tooltip(summary)

        self.assertIn("总量：12,400", tooltip)
        self.assertIn("缓存输入：7,500 (62.5%)", tooltip)

    def test_token_usage_chip_uses_app_owned_popover_detail(self):
        chip = TokenUsageChip()

        chip.setText("12.4K tokens · 缓存 7.5K / 62%")
        chip.setDetailText("本对话累计 token 用量\n总量：12,400")

        self.assertEqual(chip.text(), "12.4K tokens · 缓存 7.5K / 62%")
        self.assertEqual(chip._popover.detail_label.text(), "本对话累计 token 用量\n总量：12,400")

    def test_refresh_token_usage_label_updates_detail_without_native_tooltip(self):
        class _TokenLabel:
            def __init__(self):
                self.text_value = ""
                self.detail_text = ""
                self.tooltip_called = False

            def setText(self, text):
                self.text_value = text

            def setDetailText(self, text):
                self.detail_text = text

            def setToolTip(self, text):
                self.tooltip_called = True

        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        state.token_usage_label = _TokenLabel()
        state.token_usage_summary = {
            "input_tokens": 12000,
            "output_tokens": 400,
            "total_tokens": 12400,
            "cached_input_tokens": 7500,
            "uncached_input_tokens": 4500,
            "request_count": 2,
        }
        state.last_token_usage = {}
        window.get_session = lambda session_id=None: state

        window.refresh_token_usage_label(state.session_id)

        self.assertEqual(state.token_usage_label.text_value, "12.4K tokens · 缓存 7.5K / 62%")
        self.assertIn("总量：12,400", state.token_usage_label.detail_text)
        self.assertFalse(state.token_usage_label.tooltip_called)

    def test_llm_usage_event_updates_conversation_token_summary(self):
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        window.current_session_id = state.session_id
        window.get_session = lambda session_id=None: state
        window.set_context_tab_hint = MagicMock()
        window.refresh_observability_view = MagicMock()
        window.refresh_context_badges = MagicMock()

        window.handle_observability_event(
            {
                "type": "llm_usage",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cached_input_tokens": 60,
                    "uncached_input_tokens": 40,
                },
            },
            state.session_id,
        )
        window.handle_observability_event(
            {
                "type": "llm_usage",
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 10,
                    "total_tokens": 40,
                    "cached_input_tokens": 15,
                    "uncached_input_tokens": 15,
                },
            },
            state.session_id,
        )

        self.assertEqual(state.token_usage_summary["input_tokens"], 130)
        self.assertEqual(state.token_usage_summary["output_tokens"], 30)
        self.assertEqual(state.token_usage_summary["total_tokens"], 160)
        self.assertEqual(state.token_usage_summary["cached_input_tokens"], 75)
        self.assertEqual(state.token_usage_summary["request_count"], 2)
        self.assertEqual(state.persisted_conversation_meta["token_usage_summary"]["total_tokens"], 160)
        self.assertEqual(len(state.observability_events), 2)

    def test_compose_session_meta_persists_token_summary(self):
        window = MainWindow.__new__(MainWindow)
        state = _HistoryActionState()
        state.persisted_conversation_meta = {}
        state.run_phase = "Idle"
        state.session_status = "draft"
        state.has_file_changes = False
        state.clarify_mode_enabled = False
        state.clarify_phase = "disabled"
        state.clarify_mode_state = "exploring"
        state.pending_clarify_questions = []
        state.selected_skill_names = []
        state.sop_run = None
        state.token_usage_summary = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cached_input_tokens": 50,
            "request_count": 1,
        }
        state.last_token_usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cached_input_tokens": 50,
        }
        window._workspace_dir_for_state = lambda state=None: ""

        meta = window._compose_session_meta(state)

        self.assertEqual(meta["token_usage_summary"]["total_tokens"], 120)
        self.assertEqual(meta["last_token_usage"]["cached_input_tokens"], 50)

    def test_observability_prompt_preview_prefers_append_content(self):
        window = MainWindow.__new__(MainWindow)
        state = _ObservabilityState()
        state.system_prompt_text = "BASE-" + ("x" * 7000)
        state.system_prompt_appends = [
            {"source": "selected_skills", "content": "APPEND-CONTENT"},
        ]

        full_text, preview_text = window._build_observability_prompt_texts(state, preview_limit=200)

        self.assertIn("# Stable System Prompt\n" + state.system_prompt_text, full_text)
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

    def test_conversation_shell_metrics_center_without_drawer(self):
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = False
        window.main_container = MagicMock()
        window.main_container.width.return_value = 1600
        window.main_layout_default_margins = (12, 20, 12, 20)

        metrics = window._compute_conversation_shell_metrics()

        self.assertEqual(metrics["conversation_width"], 1197)
        self.assertLessEqual(abs(metrics["left_spacer_width"] - metrics["right_spacer_width"]), 1)

    def test_conversation_shell_metrics_shift_left_with_drawer(self):
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.main_container = MagicMock()
        window.main_container.width.return_value = 1600
        window.main_layout_default_margins = (12, 20, 12, 20)
        window.context_drawer_gap = 8

        metrics = window._compute_conversation_shell_metrics({"x": 1151, "width": 441})

        self.assertEqual(metrics["conversation_width"], 1085)
        self.assertLess(metrics["left_spacer_width"], metrics["right_spacer_width"])
        self.assertEqual(metrics["drawer_width"], 441)

    def test_compute_context_drawer_geometry_uses_stable_ratio(self):
        window = MainWindow.__new__(MainWindow)
        parent = MagicMock()
        parent.width.return_value = 1600
        parent.height.return_value = 980
        window.right_sidebar = MagicMock()
        window.right_sidebar.parentWidget.return_value = parent
        window.main_layout_default_margins = (12, 20, 12, 20)
        window.context_drawer_margin = 8
        window.context_drawer_gap = 8
        window.context_drawer_min_width = DesignTokens.drawer_min_width
        window.context_drawer_preferred_min_width = DesignTokens.drawer_preferred_min_width
        window.context_drawer_max_width = DesignTokens.drawer_max_width
        window.context_drawer_min_content_width = DesignTokens.conversation_open_min_width

        geometry = window._compute_context_drawer_geometry()

        self.assertEqual(geometry["width"], 441)
        self.assertEqual(geometry["x"], 1151)
        self.assertEqual(geometry["height"], 964)

    def test_context_drawer_geometry_preserves_manual_width(self):
        window = MainWindow.__new__(MainWindow)
        parent = MagicMock()
        parent.width.return_value = 1600
        parent.height.return_value = 980
        window.right_sidebar = MagicMock()
        window.right_sidebar.parentWidget.return_value = parent
        window.main_layout_default_margins = (12, 20, 12, 20)
        window.context_drawer_margin = 8
        window.context_drawer_gap = 8
        window.context_drawer_min_width = DesignTokens.drawer_min_width
        window.context_drawer_preferred_min_width = DesignTokens.drawer_preferred_min_width
        window.context_drawer_max_width = DesignTokens.drawer_max_width
        window.context_drawer_min_content_width = DesignTokens.conversation_open_min_width
        window.context_drawer_user_width = 470
        window.context_drawer_expanded = False

        geometry = window._compute_context_drawer_geometry()

        self.assertEqual(geometry["width"], 470)
        self.assertEqual(geometry["x"], 1122)

    def test_three_column_layout_compacts_without_overlap_on_narrow_window(self):
        window = MainWindow.__new__(MainWindow)
        parent = MagicMock()
        parent.width.return_value = 1000
        parent.height.return_value = 760
        window.right_sidebar = MagicMock()
        window.right_sidebar.parentWidget.return_value = parent
        window.main_container = parent
        window.main_layout_default_margins = (12, 20, 12, 20)
        window.context_drawer_margin = 8
        window.context_drawer_gap = 8
        window.context_drawer_min_width = DesignTokens.drawer_min_width
        window.context_drawer_preferred_min_width = DesignTokens.drawer_preferred_min_width
        window.context_drawer_max_width = DesignTokens.drawer_max_width
        window.context_drawer_min_content_width = DesignTokens.conversation_open_min_width
        window.context_drawer_user_width = 0
        window.context_drawer_expanded = False
        window.right_drawer_open = True

        geometry = window._compute_context_drawer_geometry()
        metrics = window._compute_conversation_shell_metrics(geometry)

        self.assertEqual(geometry["width"], 260)
        self.assertEqual(geometry["x"], 732)
        self.assertEqual(metrics["conversation_width"], 683)
        self.assertLessEqual(
            metrics["conversation_width"] + metrics["left_spacer_width"] + metrics["right_spacer_width"],
            metrics["shell_width"],
        )

    def test_set_observability_section_resyncs_context_layout(self):
        window = MainWindow.__new__(MainWindow)
        window.current_session_id = "session-1"
        window.OBS_SECTION_PROMPT = 0
        window.OBS_SECTION_LOG = 1
        window.OBS_SECTION_DETAILS = 2
        window.observability_content_stack = MagicMock()
        window.observability_content_stack.count.return_value = 3
        window.refresh_observability_view = MagicMock()
        window.sync_context_drawer_layout = MagicMock()
        window.right_sidebar = MagicMock()
        window.conversation_column = MagicMock()
        window.session_tabs = MagicMock()
        window.input_card = MagicMock()
        window.observability_segment_buttons = [MagicMock(), MagicMock(), MagicMock()]

        window.set_observability_section(window.OBS_SECTION_DETAILS)

        window.observability_content_stack.setCurrentIndex.assert_called_once_with(window.OBS_SECTION_DETAILS)
        window.sync_context_drawer_layout.assert_called_once()

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

    def test_start_background_services_schedules_staged_tasks(self):
        window = MainWindow.__new__(MainWindow)
        window._background_services_started = False

        with patch("main.QTimer.singleShot") as single_shot:
            window.start_background_services()

        self.assertTrue(window._background_services_started)
        self.assertEqual(
            [call.args[0] for call in single_shot.call_args_list],
            [
                BACKGROUND_TRAY_START_DELAY_MS,
                BACKGROUND_DAEMON_PREWARM_DELAY_MS,
                BACKGROUND_DAEMON_MONITOR_DELAY_MS,
                BACKGROUND_AUTOMATION_START_DELAY_MS,
            ],
        )

    def test_run_startup_hydration_loads_workspace_before_single_history_refresh(self):
        window = MainWindow.__new__(MainWindow)
        window._startup_hydration_completed = False
        window.workspace_dir = ""
        window.load_default_workspace = MagicMock(side_effect=lambda refresh_sidebar=True: setattr(window, "workspace_dir", "D:\\demo"))
        window.refresh_history_list = MagicMock()

        window._run_startup_hydration()
        window._run_startup_hydration()

        window.load_default_workspace.assert_called_once_with(refresh_sidebar=False)
        window.refresh_history_list.assert_called_once()

    def test_show_event_schedules_startup_hydration_once(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window._startup_hydration_scheduled = False
        window._background_services_scheduled = False

        try:
            with patch("main.QTimer.singleShot") as single_shot:
                event = QShowEvent()
                window.showEvent(event)
                window.showEvent(event)
        finally:
            window.deleteLater()
            app.processEvents()

        self.assertEqual([call.args[0] for call in single_shot.call_args_list], [0, 0])

    def test_daemon_connect_worker_skips_duplicate_launch_when_lock_busy(self):
        app = QApplication.instance() or QApplication([])
        payloads = []
        worker = DaemonConnectWorker("127.0.0.1", 23333, "sig", allow_start=True, retries=1)
        worker.finished_signal.connect(lambda payload: payloads.append(payload))

        client = MagicMock()
        client.ping.side_effect = [None, {"status": "ok", "signature": "sig"}]

        with patch("main.DaemonClient", return_value=client), \
             patch("main.acquire_process_singleton", return_value=None), \
             patch("main.launch_daemon_subprocess") as launch_daemon:
            worker.run()
            app.processEvents()

        launch_daemon.assert_not_called()
        self.assertEqual(len(payloads), 1)
        self.assertTrue(payloads[0]["connected"])
        self.assertEqual(payloads[0].get("launch_skipped"), "busy")

    def test_queue_daemon_connection_coalesces_pending_request_while_worker_running(self):
        window = MainWindow.__new__(MainWindow)
        window.daemon_connect_worker = MagicMock()
        window.daemon_connect_worker.isRunning.return_value = True
        window.daemon_connect_worker.allow_start = False
        window.daemon_bootstrapping = False
        window._pending_daemon_connect_allow_start = False
        window._pending_daemon_connect_retries = 0
        window.refresh_context_badges = MagicMock()

        window.queue_daemon_connection(allow_start=True, retries=4)
        window.queue_daemon_connection(allow_start=False, retries=6)

        self.assertTrue(window._pending_daemon_connect_allow_start)
        self.assertEqual(window._pending_daemon_connect_retries, 6)
        self.assertTrue(window.daemon_bootstrapping)

    def test_daemon_stream_worker_keeps_deep_copied_message_snapshot(self):
        client = MagicMock()
        messages = [{"role": "user", "content": "remember this"}]

        worker = DaemonStreamWorker(
            client,
            "session-1",
            "continue",
            "D:\\demo",
            run_context={"mode": "execution"},
            messages=messages,
        )
        messages[0]["content"] = "mutated later"

        self.assertEqual(worker.messages[0]["content"], "remember this")
        self.assertIsNot(worker.messages, messages)

    def test_handle_daemon_connect_finished_drains_pending_request(self):
        window = MainWindow.__new__(MainWindow)
        window.daemon_host = "127.0.0.1"
        window.daemon_port = 23333
        window.daemon_runtime_signature = "sig"
        window.daemon_client = None
        window.daemon_process = None
        window.daemon_connect_worker = MagicMock()
        window._pending_daemon_connect_allow_start = True
        window._pending_daemon_connect_retries = 6
        window.refresh_context_badges = MagicMock()
        worker = MagicMock()
        worker.finished_signal = MagicMock()
        worker.finished = MagicMock()

        with patch("main.DaemonConnectWorker", return_value=worker):
            window.handle_daemon_connect_finished({"connected": False})

        self.assertIs(window.daemon_connect_worker, worker)
        self.assertEqual(window._pending_daemon_connect_retries, 0)
        self.assertFalse(window._pending_daemon_connect_allow_start)
        self.assertTrue(window.daemon_bootstrapping)
        worker.start.assert_called_once()

    def test_start_gateway_process_skips_duplicate_start_while_bootstrapping(self):
        window = MainWindow.__new__(MainWindow)
        window.gateway_starting = True
        window.gateway_process = None
        window.gateway_log_file = None

        with patch("main.subprocess.Popen") as popen:
            window.start_gateway_process()

        popen.assert_not_called()

    def test_reveal_in_explorer_selects_windows_file(self):
        window = MainWindow.__new__(MainWindow)
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "draft file.html")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")

            with patch("main.platform.system", return_value="Windows"), \
                 patch("main.subprocess.Popen") as popen:
                window.reveal_in_explorer(file_path)

        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["explorer.exe", f"/select,{os.path.abspath(file_path)}"])
        expected_kwargs = subprocess_kwargs_no_window()
        self.assertEqual(kwargs.get("creationflags"), expected_kwargs.get("creationflags"))
        self.assertIsNotNone(kwargs.get("startupinfo"))

    def test_reveal_in_explorer_opens_windows_directory(self):
        window = MainWindow.__new__(MainWindow)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("main.platform.system", return_value="Windows"), \
                 patch("main.subprocess.Popen") as popen:
                window.reveal_in_explorer(temp_dir)

            popen.assert_called_once()
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ["explorer.exe", os.path.abspath(temp_dir)])
            expected_kwargs = subprocess_kwargs_no_window()
            self.assertEqual(kwargs.get("creationflags"), expected_kwargs.get("creationflags"))
            self.assertIsNotNone(kwargs.get("startupinfo"))

    def test_reveal_in_explorer_warns_for_missing_path(self):
        window = MainWindow.__new__(MainWindow)
        missing_path = os.path.join(tempfile.gettempdir(), "cowork-missing-reveal-path")

        with patch("main.QMessageBox.warning") as warning, \
             patch("main.subprocess.Popen") as popen:
            window.reveal_in_explorer(missing_path)

        popen.assert_not_called()
        warning.assert_called_once()

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
            {"agent_id": "agent-1", "agent_name": "worker", "status": "tool_use", "tool_name": "text_file_read", "tool_args": {"path": "a.txt"}, "content": "call", "ts": 2},
            {"agent_id": "agent-1", "agent_name": "worker", "status": "tool_result", "tool_name": "text_file_read", "tool_result": "ok", "content": "ok", "ts": 3},
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

    def test_context_drawer_has_no_outside_click_auto_hide_handler(self):
        self.assertNotIn("eventFilter", MainWindow.__dict__)
        self.assertFalse(hasattr(MainWindow, "_should_hide_context_drawer_for_click"))

    def test_context_drawer_still_closes_explicitly(self):
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.right_drawer_tab = window.RIGHT_TAB_DELIVERABLES
        window.right_sidebar = MagicMock()
        window.sync_context_drawer_layout = MagicMock()
        window.update_context_rail_badges = MagicMock()

        window.hide_context_drawer(reason="manual")

        self.assertFalse(window.right_drawer_open)
        window.right_sidebar.setVisible.assert_called_once_with(False)
        window.sync_context_drawer_layout.assert_called_once_with()
        window.update_context_rail_badges.assert_called_once_with()

    def test_escape_still_closes_context_drawer(self):
        window = MainWindow.__new__(MainWindow)
        window.right_drawer_open = True
        window.hide_context_drawer = MagicMock()
        event = MagicMock()
        event.key.return_value = Qt.Key_Escape

        window.keyPressEvent(event)

        window.hide_context_drawer.assert_called_once_with()
        event.accept.assert_called_once_with()

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

    def test_busy_steerable_session_routes_send_to_current_turn(self):
        window = MainWindow.__new__(MainWindow)
        state = type(
            "_GuidanceState",
            (),
            {
                "session_id": "session-1",
                "history_loading": False,
                "history_loaded": True,
                "turn_steerable": True,
            },
        )()
        window._session_is_busy = MagicMock(return_value=True)
        window._normalize_prompt_file_paths = lambda paths: list(paths)
        window._submit_turn_guidance = MagicMock(return_value=True)

        result = window._submit_session_request(
            state,
            "focus failing tests",
            ["C:\\work\\trace.png"],
            clear_current_input=True,
        )

        self.assertTrue(result)
        window._submit_turn_guidance.assert_called_once_with(
            state,
            "focus failing tests",
            ["C:\\work\\trace.png"],
            clear_current_input=True,
        )


if __name__ == "__main__":
    unittest.main()

