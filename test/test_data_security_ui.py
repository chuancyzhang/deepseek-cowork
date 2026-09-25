import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QScrollArea, QMessageBox, QLabel
from core.config_manager import ConfigManager
from core.data_security import normalize_config
from main import SettingsDialog


class DataSecurityUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            cls.app.setFont(QFont("Microsoft YaHei UI", 9))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        events = patch("bundled_plugins.data_security.panel.recent_events", return_value=[])
        events.start()
        self.addCleanup(events.stop)
        for target in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir", "core.env_utils.get_app_data_dir"):
            patcher = patch(target, return_value=self.temp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = ConfigManager()
        self.dialog = SettingsDialog(self.config, initial_page_label="数据安全")
        self.panel = self.dialog.data_security_panel
        self.addCleanup(self.close_dialog)

    def close_dialog(self):
        self.dialog._allow_close_without_prompt = True
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def capture(self, name):
        root = os.environ.get("COWORK_SECURITY_SCREENSHOTS")
        if root:
            Path(root).mkdir(parents=True, exist_ok=True)
            self.dialog.resize(820, 740)
            # Suppress unrelated updater IO in screenshot-only showEvent.
            self.dialog._automatic_update_check_started = True
            self.dialog.show()
            self.app.processEvents()
            self.assertTrue(self.dialog.grab().save(str(Path(root) / (name + ".png"))))

    def test_default_off_enable_does_not_select_features_and_persistence(self):
        self.assertEqual(self.panel.state(), normalize_config())
        self.assertFalse(self.dialog._settings_dirty)
        self.capture("data-security-disabled")
        self.panel.enabled.setChecked(True)
        self.assertFalse(any(c.isChecked() for c in self.panel.checks.values()))
        self.assertFalse(any(c.isChecked() for c in self.panel.categories.values()))
        self.panel.checks["tokenize"].setChecked(True)
        self.dialog.save_settings()
        self.assertIn("至少", self.panel.validation.text())
        self.assertFalse(self.config.get_data_security()["enabled"])
        self.capture("data-security-validation")
        self.panel.categories["email"].setChecked(True)
        self.dialog.save_settings()
        self.assertTrue(ConfigManager().get_data_security()["categories"]["email"])
        self.assertFalse(self.dialog._settings_dirty)
        self.capture("data-security-enabled")

    def test_save_failure_preserves_draft_and_rollback(self):
        self.panel.enabled.setChecked(True)
        self.panel.checks["skill_check"].setChecked(True)
        with patch.object(self.config, "_write_config", side_effect=[OSError("disk full"), None]), patch("main.QMessageBox.critical") as error:
            self.dialog.save_settings()
        self.assertTrue(error.called)
        self.assertFalse(ConfigManager().get_data_security()["enabled"])
        self.assertTrue(self.panel.enabled.isChecked())
        self.assertTrue(self.dialog._settings_dirty)
        self.dialog.discard_page_changes()
        self.assertFalse(self.panel.enabled.isChecked())

    def test_missing_plugin_does_not_break_settings_and_can_be_disabled(self):
        from ui.data_security import create_data_security_panel, UnavailableSecurityPanel
        with patch("bundled_plugins.data_security.panel.DataSecurityPanel", side_effect=ImportError("missing")):
            panel = create_data_security_panel({"enabled": True})
        self.assertIsInstance(panel, UnavailableSecurityPanel)
        panel.disable.click()
        self.assertFalse(panel.state()["enabled"])
        panel.deleteLater()

    def test_small_scrollable_page_keeps_controls_reachable(self):
        from bundled_plugins.data_security.panel import DataSecurityPanel
        panel = DataSecurityPanel({"enabled": True})
        panel.refresh_button.click()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.resize(460, 640)
        scroll.show()
        self.app.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        scroll.ensureWidgetVisible(panel.events)
        self.app.processEvents()
        self.assertGreater(scroll.verticalScrollBar().value(), 0)
        root = os.environ.get("COWORK_SECURITY_SCREENSHOTS")
        if root:
            self.assertTrue(scroll.grab().save(str(Path(root) / "data-security-small.png")))
        scroll.close()
        scroll.deleteLater()

    def test_invalid_save_reveals_error_above_categories(self):
        from bundled_plugins.data_security.panel import DataSecurityPanel
        panel = DataSecurityPanel({"enabled": True, "tokenize": True})
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.resize(460, 420)
        scroll.show()
        self.app.processEvents()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        self.assertFalse(panel.validate())
        self.app.processEvents()
        position = panel.validation.mapTo(scroll.viewport(), panel.validation.rect().topLeft())
        self.assertGreaterEqual(position.y(), 0)
        self.assertLess(position.y(), scroll.viewport().height())
        self.assertLess(panel.validation.y(), panel.categories["email"].parentWidget().y())
        scroll.close()
        scroll.deleteLater()

    def test_restored_terminal_matches_raw_message_and_survives_database_reopen(self):
        import copy
        from types import SimpleNamespace
        from main import MainWindow
        from core.chat_storage import ChatStorage
        from core.conversation_render import project_visible_messages
        from core.message_persistence import project_provider_messages
        token = "[[CW:111111111111:email:1111111111111111]]"
        original = {"id": "answer", "role": "assistant", "content": token,
                    "meta": {"security_display_content": "alice@example.org", "data_security_tokens": True}}
        messages = [{"id": "user", "role": "user", "content": "alice@example.org"}, copy.deepcopy(original)]
        state = SimpleNamespace(session_id="security-reopen", messages=messages, active_turn_id="1")
        host = SimpleNamespace(_final_assistant_for_content=MainWindow._final_assistant_for_content)
        message_id = MainWindow._ensure_terminal_assistant_message(host, state,
            generated_messages=[messages[-1]], generated_messages_raw=[messages[-1]], content="alice@example.org",
            reasoning="", content_parts=[], turn_id="1", run_id="run", bubble=SimpleNamespace())
        self.assertEqual(message_id, "answer")
        path = str(Path(self.temp.name) / "history.sqlite")
        ChatStorage(path).save_conversation(state.session_id, messages)
        restored = ChatStorage(path).get_messages(state.session_id)
        self.assertEqual(project_visible_messages(restored)[-1]["content"], "alice@example.org")
        self.assertEqual(project_provider_messages(restored)[0][-1]["content"], token)
        self.assertEqual(messages[-1], original)

    def test_restored_reply_save_queue_and_unacknowledged_restart_recovery(self):
        from core.chat_storage import ChatStorage
        from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker
        from core.chat_recovery_journal import ChatRecoveryJournal
        from core.conversation_render import project_visible_messages
        token = "[[CW:111111111111:email:1111111111111111]]"
        path = str(Path(self.temp.name) / "queue-history.sqlite")
        messages = [{"id": "u-queue", "role": "user", "content": "alice@example.org"},
                    {"id": "a-queue", "role": "assistant", "content": token,
                     "meta": {"security_display_content": "alice@example.org", "data_security_tokens": True}}]
        request = ChatSaveRequest("queue-session", messages, "test", "completed", {}, 0, revision=1)
        journal = ChatRecoveryJournal(self.temp.name)
        journal.record(request)
        worker = ChatSaveWorker(path, debounce_ms=0)
        worker.start()
        try:
            self.assertTrue(worker.enqueue(request))
            self.assertTrue(worker.wait_for_revision("queue-session", 1, timeout_ms=3000))
        finally:
            self.assertTrue(worker.stop_worker())
        storage = ChatStorage(path)
        # Simulate restart before the UI received the successful write ack.
        _, errors = ChatRecoveryJournal(self.temp.name).recover_into(storage)
        self.assertFalse(errors)
        reopened = storage.get_messages("queue-session")
        self.assertEqual(len(reopened), 2)
        self.assertEqual(reopened[-1]["content"], token)
        self.assertEqual(project_visible_messages(reopened)[-1]["content"], "alice@example.org")

    def test_credential_notice_is_visible_and_separate_from_save_errors(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from main import MainWindow
        container = QWidget()
        state = SimpleNamespace(chat_layout=QVBoxLayout(container))
        state.chat_layout.addStretch()
        MainWindow._show_conversation_notice(None, state, "保存失败", "error")
        persistence_notice = state.conversation_notice
        event = {"run_id": "run", "event": "credential", "status": "complete", "message": "发现疑似凭证"}
        security_notice = MainWindow._show_data_security_notice(None, state, event)
        self.assertIsNot(security_notice, persistence_notice)
        MainWindow._show_data_security_notice(None, state, {**event, "event": "tokenize", "message": "已令牌化"})
        self.assertIn("发现疑似凭证", " ".join(label.text() for label in security_notice.findChildren(QLabel)))
        self.assertIs(state.conversation_notice, persistence_notice)
        container.deleteLater()


if __name__ == "__main__":
    unittest.main()
