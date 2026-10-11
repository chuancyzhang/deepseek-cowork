import copy
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from core.config_manager import ConfigManager
from core.long_context import ContextBudget, format_capacity, normalize_policy
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository
from main import MainWindow, ModelEditDialog, SettingsDialog, format_token_usage_overview
from ui.long_context import ContextRolloverNotice, LongContextSettings
from ui.token_usage import TokenUsagePopover


class TestLongContextUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for filename in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "segoeui.ttf"):
                QFontDatabase.addApplicationFont(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", filename))
        cls.app.setFont(QFont("Microsoft YaHei UI", 9))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        for target in ("get_app_data_dir", "get_base_dir"):
            patcher = patch(f"core.config_manager.{target}", return_value=temporary.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = ConfigManager()
        self.widgets = []
        self.addCleanup(self.close_widgets)

    def widget(self, value):
        self.widgets.append(value)
        return value

    def close_widgets(self):
        for widget in self.widgets:
            if isinstance(widget, SettingsDialog):
                widget._allow_close_without_prompt = True
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def capture(self, name, widget):
        directory = os.environ.get("COWORK_CONTEXT_SCREENSHOTS")
        if directory:
            os.makedirs(directory, exist_ok=True)
            widget.show()
            self.app.processEvents()
            self.assertTrue(widget.grab().save(os.path.join(directory, name + ".png")))

    def test_settings_explicit_apply_restart_and_navigation(self):
        with patch("core.llm.factory.LLMFactory.create_provider") as request:
            dialog = self.widget(SettingsDialog(self.config, initial_page_label="实验功能"))
            dialog.resize(860, 600)
            panel = dialog.long_context_panel
            self.assertEqual(panel.target.currentData(), 300000)
            self.assertEqual(panel.apply_button.text(), "启用")
            panel.target.setCurrentIndex(1)
            self.assertTrue(panel.has_pending_input())
            self.assertFalse(dialog._settings_dirty)
            self.assertFalse(self.config.get("experimental_long_context")["enabled"])
            current_row = dialog.nav_list.currentRow()
            with patch("ui.long_context.ProductMessageDialog.exec_result", return_value="stay"):
                dialog.nav_list.setCurrentRow(0)
            self.assertEqual(dialog.nav_list.currentRow(), current_row)
            panel.apply_button.click()
            self.assertEqual(ConfigManager().get("experimental_long_context"), {"enabled": True, "target_tokens": 1000000})
            self.assertFalse(panel.has_pending_input())
            self.assertEqual(panel.apply_button.text(), "应用设置")
            self.capture("settings", dialog)
            panel.disable_button.click()
            self.assertFalse(ConfigManager().get("experimental_long_context")["enabled"])
            request.assert_not_called()

    def test_failed_save_preserves_selection_and_saved_configuration(self):
        panel = self.widget(LongContextSettings(self.config))
        panel.target.setCurrentIndex(1)
        before = copy.deepcopy(self.config.get("experimental_long_context"))
        with patch.object(self.config, "_write_config_or_raise", side_effect=OSError("disk full")):
            self.assertFalse(panel.save(True))
        self.assertEqual(self.config.get("experimental_long_context"), before)
        self.assertEqual(panel.target.currentData(), 1000000)
        self.assertIn("保存失败", panel.result.text())
        self.assertEqual(panel.apply_button.text(), "启用")

    def test_model_capacity_survives_channel_normalization(self):
        dialog = self.widget(ModelEditDialog("openai", model={"id": "test", "model_name": "test"}))
        dialog.context_capacity_input.setText("128000")
        dialog.output_reserve_input.setText("4096")
        channels = self.config.get_model_channels()
        channels[0]["models"] = [dialog.get_model("test")]
        self.config.set_model_channels(channels, "test")
        profile = ConfigManager().get_model_profile("test")
        self.assertEqual(profile["context_window_tokens"], 128000)
        self.assertEqual(profile["max_output_tokens"], 4096)

    def test_capacity_popover_scroll_escape_and_unknown(self):
        anchor = self.widget(QWidget())
        anchor.resize(380, 500)
        anchor.show()
        popover = self.widget(TokenUsagePopover(anchor))
        data = format_token_usage_overview({"total_tokens": 700000, "input_tokens": 650000, "output_tokens": 50000},
                                          {}, {}, {}, {}, {}, False)
        budget = ContextBudget.from_profile({"context_window_tokens": 1000000}, normalize_policy({"enabled": True}))
        data["capacity"] = format_capacity({**budget.snapshot(186000), "run_id": "r"},
            policy={"enabled": True}, active_run_id="r")
        popover.set_overview(data)
        popover.show_for(anchor)
        self.app.processEvents()
        self.assertIn("62%", popover.capacity_value.text())
        self.assertIn("700", popover.metrics["total"][1].text())
        self.assertTrue(popover.capacity_box.isVisible())
        self.assertLessEqual(popover.width(), anchor.screen().availableGeometry().width())
        self.assertGreaterEqual(popover.scroll.verticalScrollBar().maximum(), 0)
        self.capture("usage", popover)
        QTest.keyClick(popover, Qt.Key_Escape)
        self.assertFalse(popover.isVisible())
        data["capacity"] = format_capacity({}, policy={"enabled": True})
        popover.set_overview(data)
        self.assertEqual(popover.capacity_value.text(), "待统计")
        self.assertEqual(popover.capacity_bar._segments, [])

    def test_notice_same_identity_order_and_no_ledger_entry(self):
        host = self.widget(QWidget())
        layout = QVBoxLayout(host)
        layout.addStretch()
        messages = [{"id": "u", "role": "user", "content": "task"}]
        state = SimpleNamespace(session_id="s", chat_layout=layout, messages=copy.deepcopy(messages), context_rollover_notices={})
        calls = []
        harness = SimpleNamespace(
            _retry_context_rollover=lambda *args: calls.append(args),
            flush_session_content=lambda *args, **kwargs: None,
            _close_live_agent_stage=lambda state: None,
            request_session_scroll_to_bottom=lambda *args, **kwargs: None,
            _session_is_busy=lambda state: True,
        )
        event = {"run_id": "r", "rollover_id": "w", "phase": "started"}
        MainWindow._show_context_rollover(harness, state, event)
        notice = state.context_rollover_notices[("r", "w")]
        MainWindow._show_context_rollover(harness, state, {**event, "phase": "failed", "error": "请求暂时失败，请重试。"})
        self.assertIs(state.context_rollover_notices[("r", "w")], notice)
        self.assertEqual(len(state.context_rollover_notices), 1)
        self.assertEqual(state.messages, messages)
        notice.retry_button.click()
        self.assertEqual(calls, [("s", "r", "w")])
        host.resize(380, 240)
        self.capture("compression-failed", host)
        MainWindow._show_context_rollover(harness, state, {**event, "phase": "completed"})
        self.assertTrue(notice.retry_button.isHidden())
        self.capture("compression-completed", host)

    def test_theme_preview_is_reversible_for_new_components(self):
        font, style = self.app.font(), self.app.styleSheet()
        original = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        registry = getattr(self.app, "theme_binding_registry", None)
        repository = ThemeRepository(self.directory)
        runtime = ThemeRuntimeManager(self.app, repository)
        try:
            panel = self.widget(LongContextSettings(self.config))
            panel.resize(440, 370)
            saved = repository.load()
            repository.write_preview(name="上下文主题预览", overrides={"tokens": {"primary": "#265840"}},
                                     default_tokens=default_design_tokens(), session_id="context-test")
            with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                self.assertTrue(runtime.apply_repository_state(reason="test_preview"), runtime.last_error)
            self.assertIn("#265840", panel.styleSheet())
            self.assertEqual(repository.load(), saved)
            self.capture("theme-preview", panel)
            with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                self.assertTrue(runtime.restore_saved_theme(), runtime.last_error)
            self.assertIsNone(repository.load_preview())
            self.assertEqual(repository.load(), saved)
        finally:
            runtime.stop()
            self.app.theme_binding_registry = registry
            for key, value in original.items():
                setattr(DesignTokens, key, value)
            self.app.setFont(font)
            self.app.setStyleSheet(style)


if __name__ == "__main__":
    unittest.main()
