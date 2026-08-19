from dataclasses import replace
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QHelpEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QToolButton, QWidget

from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.memory_store import MemoryStore
from core.theme import DesignTokens
from core.im_gateway_registry import IM_PROVIDER_SPECS
import main as main_module
from main import (
    AutoResizingInputEdit,
    CapabilityWorkbenchDialog,
    ChannelQrDialog,
    ChannelQrWorker,
    FeishuQrDialog,
    FileChip,
    ImagePreviewDialog,
    ImagePreviewError,
    MainWindow,
    QMessageBox,
    SettingsDialog,
    WINDOWS_APP_USER_MODEL_ID,
    skill_center_config_state,
)
from ui.primitives import ProductMasterDetail, ProductTooltipController


class ProductExperienceFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ui_navigation_keeps_event_stage_and_records_phase(self):
        with patch("main.append_background_process_log") as append_log:
            main_module.log_ui_navigation(
                "first_submit_error",
                session_id="session-1",
                turn_id=1,
                phase="run",
                error="provider failed",
            )

        payload = json.loads(append_log.call_args.args[1])
        self.assertEqual(payload["stage"], "first_submit_error")
        self.assertEqual(payload["phase"], "run")

    def test_skill_center_reports_remote_required_config_as_pending(self):
        self.assertEqual(
            skill_center_config_state({
                "config_status": {
                    "has_config": True,
                    "complete": False,
                    "missing_required": ["WIND_API_KEY"],
                }
            }),
            "needs_config",
        )
        self.assertEqual(
            skill_center_config_state({
                "config_status": {
                    "has_config": True,
                    "complete": True,
                    "missing_required": [],
                }
            }),
            "ready",
        )

    def test_settings_dirty_state_ignores_background_logs_and_reverts(self):
        dialog = SettingsDialog(ConfigManager())
        try:
            self.assertFalse(dialog._settings_dirty)
            original = dialog.default_ws_input.text()
            dialog.update_log_edit.append("background update progress")
            self.app.processEvents()
            self.assertFalse(dialog._settings_dirty)

            dialog.default_ws_input.setText(original + "-changed")
            self.app.processEvents()
            self.assertTrue(dialog._settings_dirty)
            self.assertTrue(dialog.save_settings_btn.isEnabled())

            dialog.default_ws_input.setText(original)
            self.app.processEvents()
            self.assertFalse(dialog._settings_dirty)
            self.assertFalse(dialog.save_settings_btn.isEnabled())
        finally:
            dialog._allow_close_without_prompt = True
            dialog.close()

    def test_enterprise_message_has_local_save_boundary_and_single_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            config.set(
                "im_gateway",
                {
                    "enabled_providers": ["feishu"],
                    "providers": {
                        "feishu": {
                            "enabled": True,
                            "app_id": "cli-a",
                            "app_secret": "secret-a",
                        },
                        "dingtalk": {"enabled": False},
                        "wecom": {"enabled": False},
                    },
                },
            )
            host = QWidget()
            host.gateway_process = None
            host.stop_gateway_process = MagicMock()
            host.queue_daemon_connection = MagicMock()
            host.start_gateway_process = MagicMock(return_value=True)
            with patch("main.write_im_gateway_status"), patch(
                "main.read_im_gateway_status",
                return_value={},
            ):
                dialog = SettingsDialog(config, parent=host, initial_page_label="企业消息")
                try:
                    dialog._select_im_provider("wecom")
                    dialog.wecom_fields["bot_id"].setText("bot-a")
                    dialog.wecom_fields["secret"].setText("secret-b")
                    self.app.processEvents()
                    self.assertFalse(dialog._settings_dirty)
                    dialog._connect_form_im_provider("wecom")
                    saved = config.get("im_gateway")
                    self.assertEqual(saved["enabled_providers"], ["wecom"])
                    self.assertEqual(saved["providers"]["wecom"]["bot_id"], "bot-a")
                    self.assertEqual(saved["providers"]["feishu"]["app_id"], "cli-a")
                    self.assertFalse(saved["providers"]["feishu"]["enabled"])
                    host.stop_gateway_process.assert_called_once()
                    host.start_gateway_process.assert_called_once()
                finally:
                    dialog._allow_close_without_prompt = True
                    dialog.close()

    def test_feishu_qr_dialog_renders_local_qr_and_countdown(self):
        with patch.object(FeishuQrDialog, "_start_worker", new=lambda self: None):
            dialog = FeishuQrDialog()
        try:
            dialog._on_qr_ready("https://accounts.feishu.cn/device?code=test", 120)
            self.app.processEvents()
            self.assertFalse(dialog.qr_label.pixmap().isNull())
            self.assertTrue(dialog.open_link_btn.isEnabled())
            self.assertIn("秒", dialog.status_notice.label.text())
            dialog._refresh_theme()
            self.assertFalse(dialog.qr_label.pixmap().isNull())
        finally:
            dialog._timer.stop()
            dialog.close()

    def test_model_edit_delete_channel_and_empty_save_are_semantic_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            dialog = SettingsDialog(config)
            try:
                editor = dialog.model_channel_manager.editors[0]
                current = dict(editor._models()[0])
                edited = dict(current)
                edited["display_name"] = "Edited Model"
                with patch("main.ModelEditDialog") as dialog_type:
                    model_dialog = dialog_type.return_value
                    model_dialog.exec.return_value = QDialog.Accepted
                    model_dialog.get_model.return_value = edited
                    editor.edit_model()
                self.app.processEvents()
                self.assertTrue(dialog._settings_dirty)
                self.assertTrue(dialog.save_settings_btn.isEnabled())

                dialog._clear_settings_dirty()
                editor.test_status_label.setText("测试通过 · 0.2 秒")
                self.app.processEvents()
                self.assertFalse(dialog._settings_dirty)

                with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
                    while dialog.model_channel_manager.editors:
                        dialog.model_channel_manager.delete_selected_channel()
                self.app.processEvents()
                self.assertEqual(dialog.model_channel_manager.get_channels(), [])
                self.assertTrue(dialog._settings_dirty)
                self.assertFalse(dialog.model_channel_manager.delete_selected_channel_btn.isEnabled())

                dialog.save_settings()
                self.assertEqual(config.get_model_channels(), [])
                self.assertEqual(config.get_selected_model_id(), "")
                self.assertFalse(dialog._settings_dirty)
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_enterprise_message_overview_scales_to_registered_channels(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            dialog = SettingsDialog(
                ConfigManager(),
                initial_page_label="企业消息",
            )
            try:
                self.assertEqual(
                    list(dialog.im_provider_rows),
                    ["feishu", "dingtalk", "wecom", "qq", "wechat"],
                )
                self.assertTrue(dialog.im_search_input.isHidden())
                self.assertEqual(dialog.im_provider_fields["qq"], {})
                self.assertEqual(dialog.im_provider_fields["wechat"], {})
                self.assertTrue(
                    dialog.im_provider_advanced_widgets["dingtalk"].isHidden()
                )

                dialog._select_im_provider("dingtalk")
                dialog.dingtalk_fields["client_id"].setText("client")
                dialog.dingtalk_fields["client_secret"].setText("secret")
                dialog._connect_form_im_provider("dingtalk")
                self.assertTrue(
                    dialog.im_provider_advanced_toggles["dingtalk"].isChecked()
                )
                self.assertIn(
                    "Stream / WS URL",
                    dialog.im_provider_statuses["dingtalk"].label.text(),
                )
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_enterprise_message_launch_failure_overrides_stale_connecting_state(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            config.set(
                "im_gateway",
                {
                    "enabled_providers": ["wechat"],
                    "providers": {
                        "wechat": {
                            "enabled": True,
                            "bot_token": "wx-token",
                            "ilink_bot_id": "wx-bot",
                        }
                    },
                },
            )
            host = QWidget()
            host.gateway_process = None
            with patch(
                "main.read_im_gateway_status",
                return_value={"provider": "wechat", "state": "connecting"},
            ):
                dialog = SettingsDialog(
                    config,
                    parent=host,
                    initial_page_label="企业消息",
                )
                try:
                    dialog.im_runtime_errors["wechat"] = "网关进程启动失败"
                    dialog._refresh_im_provider_states()
                    self.assertEqual(
                        dialog.im_provider_row_badges["wechat"].text(),
                        "连接失败",
                    )
                    self.assertIn(
                        "网关进程启动失败",
                        dialog.im_overview_notice.label.text(),
                    )
                finally:
                    dialog._allow_close_without_prompt = True
                    dialog.close()

    def test_enterprise_message_search_appears_for_twelve_channels(self):
        base = IM_PROVIDER_SPECS[0]
        specs = tuple(
            replace(
                base,
                provider_id=f"mock_{index}",
                title=f"渠道 {index + 1}",
                subtitle=f"用途 {index + 1}",
                required_keys=(),
            )
            for index in range(12)
        )
        order = tuple(spec.provider_id for spec in specs)
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch(
            "core.config_manager.get_base_dir", return_value=temp_dir
        ), patch.object(
            main_module, "IM_PROVIDER_SPECS", specs
        ), patch.object(
            main_module, "IM_PROVIDER_ORDER", order
        ):
            dialog = SettingsDialog(
                ConfigManager(),
                initial_page_label="企业消息",
            )
            try:
                self.assertEqual(len(dialog.im_provider_rows), 12)
                self.assertFalse(dialog.im_search_input.isHidden())
                dialog.im_search_input.setText("渠道 12")
                self.app.processEvents()
                self.assertFalse(dialog.im_provider_rows["mock_11"].isHidden())
                self.assertTrue(dialog.im_provider_rows["mock_0"].isHidden())
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_wechat_qr_dialog_exposes_scan_and_verify_states(self):
        with patch.object(ChannelQrDialog, "_start_worker", new=lambda self: None):
            dialog = ChannelQrDialog("wechat")
        try:
            dialog._on_qr_ready("https://weixin.qq.com/q/test", 300)
            self.assertFalse(dialog.qr_label.pixmap().isNull())
            dialog._on_status_changed("scanned")
            dialog._update_countdown()
            self.assertIn("手机", dialog.status_notice.label.text())
            dialog._on_verify_code_required("请输入配对码")
            dialog._update_countdown()
            self.assertFalse(dialog.verify_code_row.isHidden())
            self.assertIn("配对码", dialog.status_notice.label.text())
        finally:
            dialog._timer.stop()
            dialog.close()

    def test_qr_worker_cancellation_reaches_async_task(self):
        class Task:
            cancelled = False

            def done(self):
                return False

            def cancel(self):
                self.cancelled = True

        class Loop:
            def call_soon_threadsafe(self, callback):
                callback()

        worker = ChannelQrWorker("qq")
        task = Task()
        worker._loop = Loop()
        worker._task = task
        worker.cancel()
        self.assertTrue(worker.cancel_event.is_set())
        self.assertTrue(task.cancelled)

    def test_product_master_detail_initial_wide_layout_is_visible(self):
        browse = QWidget()
        detail = QWidget()
        master = ProductMasterDetail(browse, detail, threshold=700)
        try:
            master.resize(1000, 420)
            master.show()
            self.app.processEvents()
            self.assertFalse(master._compact)
            self.assertTrue(browse.isVisible())
            self.assertTrue(detail.isVisible())
            self.assertTrue(all(size > 0 for size in master.splitter.sizes()))
        finally:
            master.close()

    def test_product_master_detail_compact_switch_keeps_single_owner(self):
        browse = QWidget()
        detail = QWidget()
        master = ProductMasterDetail(browse, detail, threshold=700)
        try:
            master.resize(620, 420)
            master.show()
            self.app.processEvents()
            self.assertTrue(master._compact)
            self.assertTrue(browse.isVisible())
            self.assertFalse(detail.isVisible())
            self.assertIs(browse.parentWidget(), master.splitter)
            self.assertIs(detail.parentWidget(), master.splitter)

            master.show_detail()
            self.app.processEvents()
            self.assertFalse(browse.isVisible())
            self.assertTrue(detail.isVisible())
            master.show_browse()
            self.app.processEvents()
            self.assertTrue(browse.isVisible())
            self.assertFalse(detail.isVisible())
        finally:
            master.close()

    def test_settings_mixed_save_failure_rolls_back_touched_config_and_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            original_workspace = config.get("default_workspace", "")
            dialog = SettingsDialog(config)
            try:
                original_soul = dialog.memory_store.read_soul()
                dialog.default_ws_input.setText(os.path.join(temp_dir, "changed-workspace"))
                dialog.memory_soul_edit.setPlainText("尚未成功保存的记忆")
                dialog.theme_settings_panel._new_theme()
                self.app.processEvents()

                with patch.object(
                    dialog.theme_settings_panel,
                    "commit",
                    side_effect=RuntimeError("主题提交失败"),
                ), patch.object(
                    dialog.theme_settings_panel,
                    "restore_saved_theme",
                ) as restore_theme, patch.object(
                    QMessageBox,
                    "critical",
                ) as critical:
                    dialog.save_settings()

                self.assertEqual(config.get("default_workspace", ""), original_workspace)
                self.assertEqual(dialog.memory_store.read_soul(), original_soul)
                restore_theme.assert_called_once_with()
                critical.assert_called_once()
                self.assertTrue(dialog._settings_dirty)
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_settings_config_only_save_skips_memory_and_theme_side_effects(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            dialog = SettingsDialog(config)
            try:
                dialog.default_ws_input.setText(os.path.join(temp_dir, "workspace"))
                self.app.processEvents()
                with patch.object(
                    dialog.memory_store,
                    "save_soul",
                    wraps=dialog.memory_store.save_soul,
                ) as save_soul, patch.object(
                    dialog.memory_store,
                    "save_summary",
                    wraps=dialog.memory_store.save_summary,
                ) as save_summary, patch.object(
                    dialog.theme_settings_panel,
                    "commit",
                    wraps=dialog.theme_settings_panel.commit,
                ) as theme_commit, patch.object(
                    config,
                    "_write_config",
                    wraps=config._write_config,
                ) as write_config:
                    dialog.save_settings()

                write_config.assert_called_once()
                save_soul.assert_not_called()
                save_summary.assert_not_called()
                theme_commit.assert_not_called()
                self.assertFalse(dialog._settings_dirty)
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_settings_memory_only_save_writes_only_changed_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            dialog = SettingsDialog(config)
            try:
                dialog.memory_soul_edit.setPlainText("只更新灵魂记忆")
                self.app.processEvents()
                with patch.object(
                    dialog.memory_store,
                    "save_soul",
                    wraps=dialog.memory_store.save_soul,
                ) as save_soul, patch.object(
                    dialog.memory_store,
                    "save_summary",
                    wraps=dialog.memory_store.save_summary,
                ) as save_summary, patch.object(
                    dialog.theme_settings_panel,
                    "commit",
                    wraps=dialog.theme_settings_panel.commit,
                ) as theme_commit, patch.object(
                    config,
                    "_write_config",
                    wraps=config._write_config,
                ) as write_config:
                    dialog.save_settings()

                save_soul.assert_called_once_with("只更新灵魂记忆")
                save_summary.assert_not_called()
                theme_commit.assert_not_called()
                write_config.assert_not_called()
                self.assertFalse(dialog._settings_dirty)
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_settings_history_directory_change_migrates_all_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            source_store = MemoryStore(config.get_chat_history_dir())
            source_store.save_soul("灵魂记忆")
            source_store.save_summary("全局摘要", "global", "")
            dialog = SettingsDialog(config)
            try:
                target_history_dir = os.path.join(temp_dir, "new-history")
                dialog.history_dir_input.setText(target_history_dir)
                self.app.processEvents()
                with patch.object(
                    dialog.theme_settings_panel,
                    "commit",
                    wraps=dialog.theme_settings_panel.commit,
                ) as theme_commit:
                    dialog.save_settings()

                target_store = MemoryStore(target_history_dir)
                self.assertEqual(target_store.read_soul(), "灵魂记忆\n")
                self.assertEqual(target_store.read_summary("global", ""), "全局摘要\n")
                self.assertEqual(config.get_chat_history_dir(), target_history_dir)
                theme_commit.assert_not_called()
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_skill_config_save_auto_enables_mcp_and_keeps_separate_test_action(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            manager = MagicMock()
            manager.is_skill_editable.return_value = False
            manager.list_skill_files.return_value = {"ok": False, "error": "read only"}
            manager.get_tool_record.return_value = None
            manager.get_skill_config_status.return_value = {
                "missing_required": [],
                "config_errors": [],
                "complete": True,
            }
            manager.build_skill_mcp_server_configs.return_value = {
                "ok": True,
                "error": "",
                "servers": [
                    {
                        "id": "superset-mcp",
                        "name": "Superset MCP",
                        "enabled": True,
                        "transport": "streamable_http",
                        "url": "https://superset.example/mcp",
                        "source_skill": "superset-mcp",
                        "managed_by_skill": True,
                    }
                ],
            }
            skill = {
                "name": "superset-mcp",
                "display_name": "Superset MCP",
                "config_fields": [
                    {"name": "SUPERSET_MCP_URL", "label": "Superset MCP URL", "required": True}
                ],
                "mcp_server_presets": [{"id": "superset-mcp", "name": "Superset MCP"}],
                "tools": [],
                "script_entries": [],
            }
            dialog = CapabilityWorkbenchDialog(skill, manager, config)
            try:
                dialog.config_editors["SUPERSET_MCP_URL"].setText("https://superset.example/mcp")
                button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
                self.assertIn("保存配置", button_texts)
                self.assertIn("测试连接", button_texts)
                self.assertNotIn("生成 / 更新 MCP 配置", button_texts)

                with patch("main.QMessageBox.information"), patch.object(config, "_write_config") as write_config:
                    dialog.save_skill_config()

                server = config.get_mcp_servers()[0]
                self.assertEqual(write_config.call_count, 1)
                self.assertTrue(server["enabled"])
                self.assertEqual(server["source_skill"], "superset-mcp")
                self.assertIn("连接待测试", dialog.managed_mcp_status.text())
            finally:
                dialog.close()

    def test_simple_capability_settings_save_and_enable_without_debug_tabs(self):
        config = MagicMock()
        config.get_skill_config.return_value = {}
        manager = MagicMock()
        manager.is_skill_editable.return_value = False
        manager.get_skill_config_status.return_value = {
            "missing_required": ["服务地址"],
            "config_errors": [],
            "complete": False,
        }
        skill = {
            "name": "sample-capability",
            "display_name": "示例能力",
            "enabled": False,
            "source_type": "bundled_plugin",
            "presentation": {
                "category": "data_analysis",
                "short_name": "示例",
                "summary": "分析示例数据。",
                "examples": ["查看指标", "整理结果"],
                "access_note": "会访问你配置的示例服务。",
            },
            "config_fields": [
                {"name": "SERVICE_URL", "label": "服务地址", "required": True}
            ],
            "tools": ["sample_tool"],
            "script_entries": [{"name": "sample_script", "path": "sample.py"}],
        }
        dialog = CapabilityWorkbenchDialog(skill, manager, config, simple_mode=True)
        try:
            button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
            tab_texts = {
                dialog.tabs.tabText(index)
                for index in range(dialog.tabs.count())
            }
            self.assertIn("保存并开启", button_texts)
            self.assertEqual(tab_texts, {"配置"})
            self.assertNotIn("Tool 调试", tab_texts)
            self.assertNotIn("Script 调试", tab_texts)
            with patch("main.QMessageBox.warning"):
                self.assertFalse(dialog._save_skill_config_values(show_message=False))
            config.set_skill_config.assert_not_called()
            dialog.config_editors["SERVICE_URL"].setText("https://service.example")
            with patch.object(dialog, "_save_skill_config_values", return_value=True), patch.object(
                dialog,
                "_set_simple_enabled",
                return_value=True,
            ) as enable:
                self.assertTrue(dialog.save_skill_config())
            enable.assert_called_once_with(True)
        finally:
            dialog.close()

    def test_web_search_config_renders_secure_provider_links_and_keyless_notice(self):
        config = MagicMock()
        config.get_skill_config.return_value = {"SEARCH_PROVIDER": "anysearch"}
        manager = MagicMock()
        manager.is_skill_editable.return_value = False
        manager.list_skill_files.return_value = {"ok": False, "error": "read only"}
        manager.get_tool_record.return_value = None
        manager.get_skill_config_status.return_value = {
            "missing_required": [],
            "config_errors": [],
            "complete": True,
        }
        skill = {
            "name": "web-search",
            "display_name": "网页搜索",
            "config_fields": [
                {
                    "name": "SEARCH_PROVIDER",
                    "label": "默认搜索服务",
                    "kind": "select",
                    "default": "anysearch",
                    "options": [
                        {"value": "anysearch", "label": "AnySearch"},
                        {"value": "tavily", "label": "Tavily"},
                    ],
                },
                {
                    "name": "ANYSEARCH_API_KEY",
                    "label": "AnySearch API Key",
                    "kind": "secret",
                    "action_label": "获取 API Key",
                    "action_url": "https://anysearch.com/console/api-keys",
                },
                {
                    "name": "TAVILY_API_KEY",
                    "label": "Tavily API Key",
                    "kind": "secret",
                    "action_label": "前往 Tavily 注册",
                    "action_url": "https://www.tavily.com/",
                },
            ],
            "tools": [],
            "script_entries": [],
        }
        dialog = CapabilityWorkbenchDialog(skill, manager, config)
        try:
            button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
            label_texts = [label.text() for label in dialog.findChildren(QLabel)]
            self.assertIn("获取 API Key", button_texts)
            self.assertIn("前往 Tavily 注册", button_texts)
            self.assertEqual(
                dialog.config_editors["ANYSEARCH_API_KEY"].echoMode(),
                QLineEdit.Password,
            )
            self.assertTrue(any("Keyless" in text for text in label_texts))
            with patch("main.QDesktopServices.openUrl", return_value=True) as open_url:
                self.assertTrue(
                    dialog._open_skill_config_action_url(
                        "https://www.tavily.com/"
                    )
                )
            self.assertEqual(open_url.call_args.args[0].scheme(), "https")
            with patch("main.QMessageBox.warning") as warning:
                self.assertFalse(
                    dialog._open_skill_config_action_url(
                        "https://user:secret@example.com/path"
                    )
                )
            warning.assert_called_once()
        finally:
            dialog.close()

    def test_deliverable_registry_only_returns_registered_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "history.sqlite"))
            workspace = os.path.join(temp_dir, "workspace")
            os.makedirs(workspace)
            result_path = os.path.join(workspace, "result.html")
            source_path = os.path.join(workspace, "notes.md")
            with open(result_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("# source")

            storage.register_deliverable(
                workspace, result_path, conversation_id="not-yet-persisted", source="generated"
            )
            records = storage.list_deliverables(workspace)

            self.assertEqual([item["path"] for item in records], [result_path])
            self.assertIsNone(records[0]["conversation_id"])
            self.assertTrue(storage.is_deliverable(workspace, result_path))
            self.assertFalse(storage.is_deliverable(workspace, source_path))
            self.assertTrue(storage.unregister_deliverable(workspace, result_path))
            self.assertEqual(storage.list_deliverables(workspace), [])

    def test_product_tooltip_is_a_child_surface(self):
        host = QWidget()
        host.resize(320, 180)
        button = QPushButton("Target", host)
        button.setToolTip("Readable tooltip")
        host.show()
        self.app.processEvents()
        controller = ProductTooltipController(self.app)
        event = QHelpEvent(QHelpEvent.ToolTip, QPoint(4, 4), button.mapToGlobal(QPoint(4, 4)))
        controller.show_for_event(button, event, button.toolTip())
        self.app.processEvents()
        try:
            self.assertTrue(controller.bubble.isVisible())
            self.assertFalse(controller.bubble.isWindow())
            self.assertEqual(controller.bubble.parentWidget(), host)
            self.assertEqual(controller.bubble.text(), "Readable tooltip")
        finally:
            controller.hide()
            host.close()
            controller.dispose()

    def test_input_paste_routes_clipboard_image_to_window(self):
        host = QWidget()
        captured = []
        host._add_clipboard_image = captured.append
        editor = AutoResizingInputEdit(host)
        mime = QMimeData()
        image = QImage(12, 8, QImage.Format_ARGB32)
        image.fill(Qt.red)
        mime.setImageData(image)

        editor.insertFromMimeData(mime)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].size(), image.size())

    def test_input_paste_routes_local_file_urls_before_image_and_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = os.path.join(temp_dir, "资料 一.txt")
            second = os.path.join(temp_dir, "截图 二.png")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
            editor = AutoResizingInputEdit()
            captured = []
            editor.clipboardFilesPasted.connect(captured.append)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(first), QUrl.fromLocalFile(second)])
            mime.setText("\n".join((QUrl.fromLocalFile(first).toString(), QUrl.fromLocalFile(second).toString())))
            image = QImage(12, 8, QImage.Format_ARGB32)
            image.fill(Qt.red)
            mime.setImageData(image)

            editor.insertFromMimeData(mime)

            self.assertEqual(
                [[os.path.normpath(path) for path in group] for group in captured],
                [[os.path.normpath(first), os.path.normpath(second)]],
            )
            self.assertEqual(editor.toPlainText(), "")

    def test_input_paste_keeps_nonlocal_url_as_text(self):
        editor = AutoResizingInputEdit()
        captured = []
        editor.clipboardFilesPasted.connect(captured.append)
        mime = QMimeData()
        mime.setUrls([QUrl("https://example.com/reference")])
        mime.setText("https://example.com/reference")

        editor.insertFromMimeData(mime)

        self.assertEqual(captured, [])
        self.assertEqual(editor.toPlainText(), "https://example.com/reference")

    def test_clipboard_files_use_attachment_pipeline_and_report_rejections(self):
        class Stub:
            _add_clipboard_files = MainWindow._add_clipboard_files

            def __init__(self):
                self.prompt_files = []
                self.add_system_toast = MagicMock()

            def _current_prompt_files(self):
                return list(self.prompt_files)

            def _add_prompt_files(self, paths):
                accepted = []
                seen = set()
                for path in paths:
                    key = os.path.normcase(os.path.normpath(path))
                    if key in seen:
                        continue
                    seen.add(key)
                    accepted.append(os.path.normpath(path))
                existing = {
                    os.path.normcase(os.path.normpath(path))
                    for path in self.prompt_files
                }
                self.prompt_files.extend(
                    path
                    for path in accepted
                    if os.path.normcase(os.path.normpath(path)) not in existing
                )
                return accepted

        with tempfile.TemporaryDirectory() as temp_dir:
            valid_path = os.path.join(temp_dir, "有效资料.txt")
            missing_path = os.path.join(temp_dir, "已移动.txt")
            with open(valid_path, "w", encoding="utf-8") as handle:
                handle.write("fixture")
            stub = Stub()

            with patch("main.log_attachment_event") as attachment_log:
                accepted = stub._add_clipboard_files(
                    [valid_path, valid_path, temp_dir, missing_path]
                )

            self.assertEqual(accepted, [valid_path])
            self.assertEqual(stub.prompt_files, [valid_path])
            stub.add_system_toast.assert_called_once()
            toast_text = stub.add_system_toast.call_args.args[0]
            self.assertIn("已添加 1 个文件", toast_text)
            self.assertIn("1 个文件夹", toast_text)
            self.assertIn("1 个失效路径", toast_text)
            stages = [call.args[0] for call in attachment_log.call_args_list]
            self.assertEqual(stages[0], "clipboard_files_paste_begin")
            self.assertIn("clipboard_files_paste_rejected", stages)
            self.assertEqual(stages[-1], "clipboard_files_paste_completed")

            stub.add_system_toast.reset_mock()
            with patch("main.log_attachment_event") as duplicate_log:
                accepted_again = stub._add_clipboard_files([valid_path])
            self.assertEqual(accepted_again, [valid_path])
            self.assertEqual(stub.prompt_files, [valid_path])
            stub.add_system_toast.assert_not_called()
            completed = next(
                call
                for call in duplicate_log.call_args_list
                if call.args[0] == "clipboard_files_paste_completed"
            )
            self.assertEqual(completed.kwargs["duplicate_count"], 1)

    def test_input_height_animation_is_owned_by_editor(self):
        host = QWidget()
        editor = AutoResizingInputEdit(host)
        editor.setFixedHeight(120)
        editor.setPlainText("触发输入框高度调整")

        editor.adjustHeight()

        self.assertIsNotNone(editor.anim)
        self.assertIs(editor.anim.parent(), editor)

    def test_clipboard_image_is_saved_in_session_attachment_directory(self):
        class Stub:
            _managed_attachment_root = MainWindow._managed_attachment_root
            _session_attachment_dir = MainWindow._session_attachment_dir
            _add_clipboard_image = MainWindow._add_clipboard_image

        with tempfile.TemporaryDirectory() as temp_dir:
            stub = Stub()
            stub.chat_history_dir = temp_dir
            state = SimpleNamespace(session_id="session-1")
            stub.get_current_session = lambda: state
            added = []
            stub._add_prompt_files = lambda paths: added.extend(paths)
            stub.add_system_toast = MagicMock()
            image = QImage(18, 12, QImage.Format_ARGB32)
            image.fill(Qt.green)

            path = stub._add_clipboard_image(image)

            self.assertTrue(os.path.isfile(path))
            self.assertEqual(added, [path])
            self.assertIn(os.path.join("attachments", "session-1"), path)

    def test_image_file_chip_renders_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "shot.png")
            image = QImage(20, 10, QImage.Format_ARGB32)
            image.fill(Qt.blue)
            self.assertTrue(image.save(path))
            chip = FileChip(path, removable=True)
            try:
                thumbnail = next(label for label in chip.findChildren(QLabel) if label.pixmap() is not None)
                self.assertGreaterEqual(chip.sizeHint().height(), 40)
                self.assertEqual(thumbnail.size(), thumbnail.maximumSize())
            finally:
                chip.deleteLater()

    def test_image_file_chip_thumbnail_supports_mouse_and_keyboard_activation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "shot.png")
            image = QImage(20, 10, QImage.Format_ARGB32)
            image.fill(Qt.blue)
            self.assertTrue(image.save(path))
            chip = FileChip(path)
            chip.previewRequested.disconnect(chip._open_image_preview)
            captured = []
            chip.previewRequested.connect(captured.append)
            chip.show()
            self.app.processEvents()
            try:
                QTest.mouseClick(chip.icon_label, Qt.LeftButton)
                chip.icon_label.setFocus()
                QTest.keyClick(chip.icon_label, Qt.Key_Return)
                self.assertEqual(captured, [path, path])
            finally:
                chip.close()
                chip.deleteLater()

    def test_image_preview_dialog_supports_fit_and_bounded_manual_zoom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "large-shot.png")
            image = QImage(640, 320, QImage.Format_ARGB32)
            image.fill(Qt.green)
            self.assertTrue(image.save(path))
            dialog = ImagePreviewDialog(path)
            dialog.resize(520, 420)
            dialog.show()
            self.app.processEvents()
            try:
                self.assertTrue(dialog._fit_mode)
                self.assertFalse(dialog.image_label.pixmap().isNull())
                self.assertLessEqual(dialog._zoom_percent, 100)

                dialog.set_zoom_percent(999)
                self.assertEqual(dialog._zoom_percent, 400)
                self.assertEqual(dialog.zoom_label.text(), "400%")
                dialog.set_zoom_percent(1)
                self.assertEqual(dialog._zoom_percent, 25)
                self.assertEqual(dialog.zoom_label.text(), "25%")
                dialog.set_zoom_percent(100)
                self.assertEqual(dialog.image_label.pixmap().size(), image.size())

                dialog._render_zoom(18, fit_mode=True)
                dialog.zoom_in()
                self.assertEqual(dialog._zoom_percent, 25)

                dialog.fit_to_window()
                self.assertTrue(dialog._fit_mode)
                self.assertLessEqual(dialog._zoom_percent, 100)

                dialog.setAttribute(Qt.WA_DeleteOnClose, False)
                QTest.keyClick(dialog, Qt.Key_Escape)
                self.app.processEvents()
                self.assertFalse(dialog.isVisible())
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_image_preview_dialog_rejects_missing_and_invalid_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = os.path.join(temp_dir, "missing.png")
            with self.assertRaisesRegex(ImagePreviewError, "不存在或已被移动"):
                ImagePreviewDialog(missing_path)

            invalid_path = os.path.join(temp_dir, "invalid.png")
            with open(invalid_path, "w", encoding="utf-8") as handle:
                handle.write("not an image")
            with self.assertRaisesRegex(ImagePreviewError, "无法解码"):
                ImagePreviewDialog(invalid_path)

            valid_path = os.path.join(temp_dir, "too-large.png")
            image = QImage(10, 10, QImage.Format_ARGB32)
            image.fill(Qt.red)
            self.assertTrue(image.save(valid_path))
            with patch(
                "main.os.path.getsize",
                return_value=ImagePreviewDialog.MAX_FILE_BYTES + 1,
            ):
                with self.assertRaisesRegex(ImagePreviewError, "文件过大"):
                    ImagePreviewDialog(valid_path)

    def test_vision_preflight_blocks_unsupported_model(self):
        class Stub:
            _ensure_vision_attachment_support = MainWindow._ensure_vision_attachment_support

            def _normalize_prompt_file_paths(self, paths):
                return list(paths)

            def _is_supported_image_attachment(self, path):
                return path.endswith(".png")

            def _selected_model_supports_vision(self, state):
                return False

            def _model_id_for_state(self, state):
                return "text-only"

        stub = Stub()
        stub.current_session_id = "session-1"
        stub.add_system_toast = MagicMock()
        stub.model_select_btn = QToolButton()
        stub.model_select_btn.showMenu = MagicMock()
        state = SimpleNamespace(session_id="session-1")

        result = stub._ensure_vision_attachment_support(state, ["shot.png"])

        self.assertFalse(result)
        stub.add_system_toast.assert_called_once()

    def test_system_toasts_stack_queue_and_deduplicate_without_session(self):
        class Stub:
            _system_toast_duration = MainWindow._system_toast_duration
            _system_toast_target_position = MainWindow._system_toast_target_position
            _position_active_system_toast = MainWindow._position_active_system_toast
            _show_next_system_toast = MainWindow._show_next_system_toast
            _dismiss_system_toast = MainWindow._dismiss_system_toast
            add_system_toast = MainWindow.add_system_toast

        stub = Stub()
        stub.main_container = QWidget()
        stub.main_container.resize(900, 600)
        stub._system_toast_queue = []
        stub._visible_system_toasts = []
        stub._active_system_toast = None

        stub.add_system_toast("已保存", "success", auto_close_ms=0)
        stub.add_system_toast("已保存", "success", auto_close_ms=0)
        stub.add_system_toast("第二条", "info", auto_close_ms=0)
        stub.add_system_toast("第三条", "warning", auto_close_ms=0)
        stub.add_system_toast("第四条", "error", auto_close_ms=0)
        self.app.processEvents()

        self.assertEqual(len(stub._visible_system_toasts), 3)
        self.assertEqual(len(stub._system_toast_queue), 1)
        self.assertEqual(stub._visible_system_toasts[0].repeat_count, 2)
        positions = [toast.pos().y() for toast in stub._visible_system_toasts]
        self.assertEqual(
            positions[0],
            max(DesignTokens.toast_top_margin, DesignTokens.toast_edge_margin),
        )
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])
        for toast in stub._visible_system_toasts:
            self.assertEqual(
                toast.pos().x(),
                stub.main_container.width() - toast.width() - DesignTokens.toast_edge_margin,
            )

        stub._dismiss_system_toast(stub._visible_system_toasts[-1])
        self.app.processEvents()
        self.assertEqual(len(stub._visible_system_toasts), 3)
        self.assertEqual(len(stub._system_toast_queue), 0)
        for toast in list(stub._visible_system_toasts):
            stub._dismiss_system_toast(toast)

    def test_windows_notification_identity_omits_version_and_device_suffix(self):
        self.assertEqual(WINDOWS_APP_USER_MODEL_ID, "deepseek.cowork")


if __name__ == "__main__":
    unittest.main()
