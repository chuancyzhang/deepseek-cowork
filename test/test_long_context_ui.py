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
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from core.config_manager import ConfigManager
from core.long_context import ContextBudget, format_capacity, normalize_policy
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository
from main import MainWindow, ModelEditDialog, SettingsDialog, format_token_usage_overview
from ui.long_context import ContextLimitControl, ContextRolloverNotice, LongContextSettings
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
            self.assertEqual(panel.apply_button.text(), "保存设置")
            self.assertFalse(panel.enabled.isChecked())
            self.assertFalse(panel.target.isEnabled())
            self.assertFalse(panel.apply_button.isEnabled())
            self.capture("settings-disabled", dialog)
            panel.enabled.click()
            self.assertTrue(panel.target.isEnabled())
            panel.target.setCurrentIndex(1)
            self.assertTrue(panel.has_pending_input())
            self.assertFalse(dialog._settings_dirty)
            self.assertFalse(self.config.get("experimental_long_context")["enabled"])
            current_row = dialog.nav_list.currentRow()
            with patch("ui.long_context.ProductMessageDialog.exec_result", side_effect=AssertionError("cross-page draft should stay")):
                dialog.nav_list.setCurrentRow(0)
            self.assertEqual(dialog.nav_list.currentRow(), 0)
            self.assertTrue(panel.has_pending_input())
            dialog.nav_list.setCurrentRow(current_row)
            self.capture("settings-pending", dialog)
            panel.apply_button.click()
            self.assertEqual(ConfigManager().get("experimental_long_context"), {"enabled": True, "target_tokens": 1000000})
            self.assertFalse(panel.has_pending_input())
            self.assertFalse(panel.apply_button.isEnabled())
            self.assertIn("已启用 · 100 万", panel.status.text())
            self.capture("settings", dialog)
            # Changing capacity does not change the saved setting until Apply.
            panel.target.setCurrentIndex(0)
            self.assertEqual(ConfigManager().get("experimental_long_context")["target_tokens"], 1000000)
            panel.reset_button.click()
            self.assertEqual(panel.target.currentData(), 1000000)
            panel.enabled.click()
            self.assertFalse(panel.target.isEnabled())
            self.assertTrue(ConfigManager().get("experimental_long_context")["enabled"])
            panel.apply_button.click()
            self.assertFalse(ConfigManager().get("experimental_long_context")["enabled"])
            self.assertEqual(ConfigManager().get("experimental_long_context")["target_tokens"], 1000000)
            request.assert_not_called()

    def test_failed_save_preserves_selection_and_saved_configuration(self):
        panel = self.widget(LongContextSettings(self.config))
        panel.enabled.click()
        panel.target.setCurrentIndex(1)
        before = copy.deepcopy(self.config.get("experimental_long_context"))
        with patch.object(self.config, "_write_config_or_raise", side_effect=OSError("disk full")), \
             self.assertLogs("ui.long_context", level="INFO") as logs:
            self.assertFalse(panel.save())
        self.assertEqual(self.config.get("experimental_long_context"), before)
        self.assertEqual(panel.target.currentData(), 1000000)
        self.assertIn("保存失败", panel.result.text())
        self.assertTrue(panel.enabled.isChecked())
        self.assertTrue(panel.apply_button.isEnabled())
        self.assertIn("已关闭", panel.status.text())
        self.assertIn("save_failed error_type=OSError", " ".join(logs.output))
        panel.resize(380, 600)
        self.capture("settings-save-failed", panel)
        self.assertTrue(panel.save())
        self.assertFalse(panel.has_pending_input())
        self.assertEqual(ConfigManager().get("experimental_long_context"), {"enabled": True, "target_tokens": 1000000})

    def test_leave_saves_actual_toggle_and_discard_restores_both_fields(self):
        self.config.set_experimental_long_context({"enabled": True, "target_tokens": 300000})
        panel = self.widget(LongContextSettings(self.config))
        panel.target.setCurrentIndex(1)
        panel.enabled.click()
        with patch("ui.long_context.ProductMessageDialog.exec_result", return_value="save"):
            self.assertTrue(panel.confirm_leave())
        self.assertEqual(ConfigManager().get("experimental_long_context"), {"enabled": False, "target_tokens": 1000000})
        panel.enabled.click()
        panel.target.setCurrentIndex(0)
        with patch("ui.long_context.ProductMessageDialog.exec_result", return_value="discard"):
            self.assertTrue(panel.confirm_leave())
        self.assertFalse(panel.enabled.isChecked())
        self.assertFalse(panel.target.isEnabled())
        self.assertEqual(panel.target.currentData(), 1000000)
        self.assertFalse(panel.has_pending_input())
        panel.enabled.click()
        with patch("ui.long_context.ProductMessageDialog.exec_result", return_value="save"), \
             patch.object(self.config, "_write_config_or_raise", side_effect=OSError("disk full")):
            self.assertFalse(panel.confirm_leave())
        self.assertTrue(panel.has_pending_input())
        self.assertFalse(ConfigManager().get("experimental_long_context")["enabled"])

    def test_small_settings_page_keyboard_and_controls_remain_reachable(self):
        dialog = self.widget(SettingsDialog(self.config, initial_page_label="实验功能"))
        # Routed settings pages remove the standalone dialog's minimum size.
        dialog.setMinimumSize(0, 0)
        dialog.resize(560, 620)
        dialog.show()
        panel = dialog.long_context_panel
        self.app.processEvents()
        self.assertEqual(dialog.width(), 560)
        panel.enabled.setFocus()
        QTest.keyClick(panel.enabled, Qt.Key_Space)
        QTest.keyClick(panel.enabled, Qt.Key_Tab)
        self.assertTrue(panel.target.hasFocus())
        QTest.keyClick(panel.target, Qt.Key_Down)
        QTest.keyClick(panel.target, Qt.Key_Tab)
        self.assertTrue(panel.apply_button.hasFocus())
        QTest.keyClick(panel.apply_button, Qt.Key_Space)
        self.assertEqual(ConfigManager().get("experimental_long_context"), {"enabled": True, "target_tokens": 1000000})
        self.app.processEvents()
        self.assertLessEqual(panel.target.geometry().right(), panel.width())
        self.assertLessEqual(panel.reset_button.geometry().right(), panel.width())
        for label in (panel.status, panel.target_hint, panel.result):
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.capture("settings-small", dialog)

    def test_model_editor_needs_no_capacity_and_preserves_existing_metadata(self):
        dialog = self.widget(ModelEditDialog("openai", model={"id": "test", "model_name": "test"}))
        item = dialog.get_model("test")
        self.assertNotIn("context_window_tokens", item)
        self.assertNotIn("max_output_tokens", item)
        self.assertFalse(any("上下文容量" in label.text() or "输出预留" in label.text()
                             for label in dialog.findChildren(QLabel)))
        self.capture("model-editor", dialog)
        existing = self.widget(ModelEditDialog("openai", model={**item,
            "context_window_tokens": 128000, "max_output_tokens": 4096}))
        existing.display_name_input.setText("Renamed model")
        channels = self.config.get_model_channels()
        channels[0]["models"] = [existing.get_model("test")]
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
        budget = ContextBudget.from_profile({"model_name": "custom-model"}, normalize_policy({"enabled": True}))
        data["capacity"] = format_capacity({**budget.snapshot(186000), "run_id": "r"},
            policy={"enabled": True}, active_run_id="r")
        popover.set_overview(data)
        popover.show_for(anchor)
        self.app.processEvents()
        self.assertEqual(popover.capacity_value.text(), "≈ 114.0K")
        self.assertIn("62%", popover.capacity_used.text())
        self.assertIn("沿用设置", data["capacity"]["note"])
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

    def test_unestimated_images_note_remains_readable(self):
        anchor = self.widget(QWidget())
        anchor.resize(380, 500)
        anchor.show()
        popover = self.widget(TokenUsagePopover(anchor))
        data = format_token_usage_overview({}, {}, {}, {}, {}, {}, False)
        budget = ContextBudget.from_profile({"context_window_tokens": 128000}, normalize_policy({"enabled": True}))
        data["capacity"] = format_capacity({**budget.snapshot(18000), "run_id": "r", "unestimated_images": 2},
            policy={"enabled": True}, active_run_id="r")
        popover.set_overview(data)
        popover.show_for(anchor)
        self.app.processEvents()
        self.assertIn("2 张图片未计入", data["capacity"]["note"])
        self.assertNotIn("暂缓", data["capacity"]["note"])
        self.assertTrue(popover.capacity_box.isVisible())
        self.capture("image-budget", popover)

    def test_inline_limit_explicit_save_failure_and_reopen_preserve_draft(self):
        self.config.set_experimental_long_context({"enabled": True, "target_tokens": 1000000})
        anchor = self.widget(QWidget())
        anchor.resize(440, 700)
        anchor.show()
        popover = self.widget(TokenUsagePopover(anchor, config_manager=self.config))
        panel = popover.limit_control
        snapshot = ContextBudget.from_profile({"context_window_tokens": 500000},
            normalize_policy(self.config.get("experimental_long_context"))).snapshot(150775)
        before = copy.deepcopy(snapshot)
        data = format_token_usage_overview({}, {}, {}, {}, {}, {}, False)
        data["capacity"] = format_capacity(snapshot, policy=self.config.get("experimental_long_context"))
        popover.set_overview(data)
        with patch("core.llm.factory.LLMFactory.create_provider") as request:
            popover.show_for(anchor)
            panel.target.setCurrentIndex(panel.target.findData(300000))
            self.assertEqual(self.config.get("experimental_long_context")["target_tokens"], 1000000)
            popover.hide()
            popover.show_for(anchor)
            self.assertEqual(panel.target.currentData(), 300000)
            self.assertTrue(panel.actions.isVisible())
            with patch.object(self.config, "_write_config_or_raise", side_effect=OSError("disk full")):
                self.assertFalse(panel.save())
            self.assertIn("选择已保留", panel.result.text())
            self.assertEqual(ConfigManager().get("experimental_long_context")["target_tokens"], 1000000)
            self.assertEqual(panel.target.currentData(), 300000)
            self.assertTrue(panel.save())
            self.assertEqual(ConfigManager().get("experimental_long_context")["target_tokens"], 300000)
            self.assertIn("下次开始或重试", panel.result.text())
            self.assertEqual(snapshot, before)
            self.assertIn("466.8K", popover.capacity_budget.text())
            panel.target.setCurrentIndex(panel.target.findData(1000000))
            panel.reset_button.click()
            self.assertEqual(panel.target.currentData(), 300000)
            request.assert_not_called()

    def test_inline_and_settings_reload_the_same_saved_policy(self):
        panel = self.widget(ContextLimitControl(self.config))
        settings = self.widget(LongContextSettings(self.config))
        panel.target.setCurrentIndex(panel.target.findData(1000000))
        self.assertTrue(panel.save())
        self.assertTrue(ConfigManager().get("experimental_long_context")["enabled"])
        settings.show()
        self.app.processEvents()
        self.assertTrue(settings.enabled.isChecked())
        self.assertEqual(settings.target.currentData(), 1000000)
        settings.target.setCurrentIndex(settings.target.findData(300000))
        settings.save()
        panel.sync_saved()
        self.assertEqual(panel.target.currentData(), 300000)
        panel.target.setCurrentIndex(panel.target.findData(0))
        panel.save()
        self.assertEqual(self.config.get("experimental_long_context"), {"enabled": False, "target_tokens": 300000})

    def test_overview_grouping_and_copy_keep_complete_statistics(self):
        counts = {"system": 29, "tools": 115, "user": 4, "assistant": 403, "results": 304, "injected": 145}
        context = {"status": "available", "counts": counts, "total": 1000, "has_media": True}
        data = format_token_usage_overview({"input_tokens": 100, "cached_input_tokens": 40, "uncached_input_tokens": 10},
                                          {}, {}, {}, context, {}, False)
        self.assertEqual(data["context"]["values"], {"dialogue": "40.7%", "results": "30.4%", "instructions": "17.4%", "tools": "11.5%"})
        self.assertEqual(sum(value for value, _ in data["context"]["segments"]), 1000)
        self.assertEqual(data["hit"]["primary_note"], "仅已知输入")
        popover = self.widget(TokenUsagePopover())
        popover.set_overview(data)
        popover.set_detail_text("生成速度：58.1 tok/s")
        self.assertTrue(popover.balance_box.isHidden())
        self.assertTrue(popover.copy_button.isHidden())
        popover.details_button.click()
        popover._copy_details()
        copied = self.app.clipboard().text()
        for text in ("系统提示词：约 29", "用户消息：约 4", "助手消息：约 403", "58.1 tok/s", "缓存明细尚不完整"):
            self.assertIn(text, copied)
        data["balance"] = {"visible": True, "value": "¥0.00", "note": "最近查询结果"}
        popover.set_overview(data)
        self.assertFalse(popover.balance_box.isHidden())
        self.assertEqual(popover.balance_value.text(), "¥0.00")

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

    def test_confirmed_theme_is_reversible_for_new_components(self):
        font, style = self.app.font(), self.app.styleSheet()
        original = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        registry = getattr(self.app, "theme_binding_registry", None)
        repository = ThemeRepository(self.directory)
        runtime = ThemeRuntimeManager(self.app, repository)
        try:
            panel = self.widget(LongContextSettings(self.config))
            panel.resize(440, 370)
            saved = repository.load()
            preview = repository.write_preview(name="上下文主题预览", overrides={"tokens": {"primary": "#265840"}},
                                     default_tokens=default_design_tokens(), session_id="context-test")
            with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                self.assertTrue(runtime.apply_repository_state(reason="test_preview"), runtime.last_error)
                self.assertNotIn("#265840", panel.styleSheet())
                self.assertEqual(repository.load(), saved)
                runtime.commit_current_preview(expected_id=preview["preview_id"], expected_revision=preview["revision"])
                self.assertIn("#265840", panel.styleSheet())
            self.capture("theme-preview", panel)
            with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                self.assertTrue(runtime.switch_theme(undo=True), runtime.last_error)
            self.assertIsNone(repository.load_preview())
            self.assertEqual(repository.load().active_theme_id, saved.active_theme_id)
        finally:
            runtime.stop()
            self.app.theme_binding_registry = registry
            for key, value in original.items():
                setattr(DesignTokens, key, value)
            self.app.setFont(font)
            self.app.setStyleSheet(style)


if __name__ == "__main__":
    unittest.main()
