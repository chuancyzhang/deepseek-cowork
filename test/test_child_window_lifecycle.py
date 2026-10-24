"""Catch transient native windows before layouts adopt their child controls."""
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from core.config_manager import ConfigManager
from core.theme import ThemeRuntimeManager
from core.theme_service import ThemeRepository
from main import (CapabilityWorkbenchDialog, ChatBubble, FavoriteEditorPage, IM_PROVIDER_SPECS,
                  InlineInteractionCard, McpServerEditDialog, SessionSkillPickerPopover,
                  SettingsDialog, SubAgentEventTile, MainWindow)
from core.conversation_agents import builtin_agent_profiles
from ui.conversation_actions import ConversationMentionPopover
from ui_window_cleanup import close_test_window


class NativeShowTracker(QObject):
    def __init__(self):
        super().__init__()
        self.events = []
        self.allowed = set()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                and obj.isWindow() and obj not in self.allowed):
            self.events.append((type(obj).__name__, obj.objectName()))
        return False


class ChildWindowLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            path = Path("C:/Windows/Fonts", name)
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-child-window-test-")
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict(os.environ, {"APPDATA": self.temp.name,
            "LOCALAPPDATA": self.temp.name, "COWORK_EXTERNAL_BACKGROUND_SERVICES": "0"}))
        Path(self.temp.name, "config.json").write_text("{}", encoding="utf-8")
        for name in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir"):
            self.enterContext(patch(name, return_value=self.temp.name))
        self.config = ConfigManager()
        self.manager = ThemeRuntimeManager(self.app, ThemeRepository(self.temp.name))
        self.addCleanup(self.manager.stop)
        self.enterContext(patch.object(self.app, "theme_manager", self.manager, create=True))
        self.assertTrue(self.manager.apply_repository_state(reason="child_window_test"), self.manager.last_error)
        self.parent = QWidget()
        self.tracker = NativeShowTracker()
        self.app.installEventFilter(self.tracker)

    def tearDown(self):
        self.app.removeEventFilter(self.tracker)
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.manager.stop()

    def test_picker_construction_never_shows_native_children(self):
        for options in ({"agents_only": True}, {"conversations_only": True}):
            with self.subTest(options=options):
                picker = ConversationMentionPopover([], [], "", self.parent, **options)
                self.assertFalse(picker.title.isHidden())
                self.assertEqual(self.tracker.events, [])
        picker = SessionSkillPickerPopover([], parent=self.parent)
        cancel = next(button for button in picker.findChildren(QPushButton) if button.text() == "取消")
        self.assertFalse(cancel.isHidden())
        self.assertEqual(self.tracker.events, [])

    def test_editors_never_show_native_action_buttons(self):
        manager = MagicMock()
        manager.is_skill_editable.return_value = True
        manager.list_skill_files.return_value = {"ok": True, "files": []}
        manager.get_skill_config_status.return_value = {
            "missing_required": [], "config_errors": [], "complete": True}
        manager.get_tool_record.return_value = None
        skill = CapabilityWorkbenchDialog({"name": "test-skill", "tools": [], "script_entries": []},
                                          manager, self.config, parent=self.parent)
        favorite = FavoriteEditorPage(favorite={"id": "test-favorite", "name": "测试计划", "prompt": "测试",
            "schedule": {"schedule_type": "daily", "time_of_day": "09:00"}}, parent=self.parent)
        mcp = McpServerEditDialog(parent=self.parent)
        for button in (skill.save_file_btn, favorite.run_schedule_btn, mcp.import_button):
            self.assertFalse(button.isHidden())
            self.assertFalse(button.isWindow())
        self.assertEqual(self.tracker.events, [])

    def test_inline_input_and_task_details_remain_child_controls(self):
        for kind in ("text", "choice", "multi_choice"):
            with self.subTest(kind=kind):
                card = InlineInteractionCard({"kind": kind, "allow_free_text": True,
                    "options": [{"label": "示例", "value": "example"}]}, self.parent)
                self.assertFalse(card.text_input.isHidden())
                self.assertEqual(self.tracker.events, [])
        tile = SubAgentEventTile({"status": "completed", "content": "任务详情"}, self.parent)
        self.assertFalse(tile.toggle_btn.isHidden())
        self.assertEqual(self.tracker.events, [])

    def test_settings_custom_sources_and_expanded_channels_never_show_native_inputs(self):
        self.config.config["download_sources"] = {
            kind: {"selected": "test-custom", "custom": [{"id": "test-custom", "name": "测试源",
                "url": "https://example.com/" + kind + "/"}]}
            for kind in ("python", "node")}
        # Exercise the existing >6 condition even while the product has five providers.
        specs = IM_PROVIDER_SPECS + tuple(replace(IM_PROVIDER_SPECS[-1], provider_id=f"test-{index}")
                                          for index in range(2))
        with patch("main.IM_PROVIDER_SPECS", specs):
            settings = SettingsDialog(self.config, self.parent)
        for field in (settings.python_source_url, settings.node_source_url, settings.im_search_input):
            self.assertFalse(field.isHidden())
            self.assertFalse(field.isWindow())
        self.assertEqual(self.tracker.events, [])

    def test_user_message_text_and_attachments_never_show_native_children(self):
        for text, attachments in (("正文", []), ("", [{"path": "example.txt"}]),
                                  ("参考文件", [{"path": "example.txt"}])):
            with self.subTest(text=text, attachments=bool(attachments)):
                bubble = ChatBubble("User", text, attachments=attachments, parent=self.parent)
                self.assertIs(bubble.user_content_edit.parentWidget(), bubble.user_bubble_frame)
                self.assertIs(bubble.user_context_container.parentWidget(), bubble.user_bubble_frame)
                self.assertEqual(bubble.user_content_edit.isHidden(), not bool(text))
                self.assertEqual(bubble.user_context_container.isHidden(), not bool(attachments))
                self.assertEqual(self.tracker.events, [])

    def test_history_switch_and_details_keep_one_reply_identity_without_native_windows(self):
        window = MainWindow(config_manager=self.config, theme_manager=self.manager)
        self.addCleanup(close_test_window, window)
        window._startup_hydration_scheduled = True
        window._background_services_scheduled = True
        self.tracker.allowed.add(window)
        window.show()
        state = window.get_current_session()
        expert = builtin_agent_profiles()[0]
        # Current selection must not rewrite the identity frozen in older turns.
        state.conversation_agent = expert
        state.active_turn_agent = expert
        state.messages = [
            {"id": "expert-reply", "role": "assistant", "content": "页面已完成",
             "reasoning_content": "检查页面布局", "meta": {
                 "ui_reply_kind": "final", "ui_conversation_agent": expert}},
            {"id": "main-reply", "role": "assistant", "content": "普通助手回复",
             "meta": {"ui_reply_kind": "final", "ui_conversation_agent": {}}},
        ]
        window._render_session_history_spans(state, [{"start": 0, "end": 1}, {"start": 1, "end": 2}])
        summary, normal = state.history_detail_summaries
        summary.detail_button.setChecked(True)
        deadline = time.monotonic() + 5
        while summary.detail_loading and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertFalse(summary.detail_loading)
        self.assertTrue(summary.detail_groups)
        for widget, expected in ((summary, "HTML创作"), (normal, "Cowork")):
            labels = [label.text() for label in widget.findChildren(QLabel, "ConversationAgentName")
                      if label.isVisibleTo(widget)]
            self.assertEqual(labels, [expected])
        other = window.create_new_session(make_current=False)
        for sid in (other, state.session_id, other, state.session_id):
            window.set_current_session(sid)
            self.app.processEvents()
        self.assertEqual(summary.identity_header.name_label.text(), "HTML创作")
        self.assertEqual(normal.identity_header.name_label.text(), "Cowork")
        # Direct/legacy replies also use the message identity, not current selection.
        bubble = window.add_chat_bubble("Agent", "普通回复", source_message_id="main-reply", animate=False)
        self.app.processEvents()
        self.assertEqual(bubble.identity_header.name_label.text(), "Cowork")
        subtask = window._render_subagent_conversation_item(
            {"type": "assistant", "content": "子任务回复"}, self.parent)
        self.assertTrue(subtask.identity_header.isHidden())
        self.assertEqual(self.tracker.events, [])


if __name__ == "__main__":
    unittest.main()
