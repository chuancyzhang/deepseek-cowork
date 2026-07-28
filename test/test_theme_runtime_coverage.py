import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid as is_qt_object_valid

from core.theme import ThemeRuntimeManager
from core.theme_service import ThemeRepository
from core.conversation_render import build_conversation_render_spans
from main import (
    ChatBubble,
    FileChip,
    GuidanceTimelineEvent,
    InlineInteractionCard,
    MainWindow,
    SessionSkillPickerPopover,
    SessionContextChip,
)
from ui.theme_workspace import WorkspaceSceneCanvas


class TopLevelShowTracker(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Show
            and isinstance(obj, QWidget)
            and obj.isWindow()
            and obj.parentWidget() is None
        ):
            self.events.append((type(obj).__name__, obj.objectName()))
        return False


class ThemeRuntimeCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_manager = getattr(self.app, "theme_manager", None)
        self.manager = ThemeRuntimeManager(
            self.app,
            ThemeRepository(self.temp_dir.name),
        )
        self.app.theme_manager = self.manager
        self.window = MainWindow(theme_manager=self.manager)

    def tearDown(self):
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.manager.restore_saved_theme(reason="test_cleanup")
        self.window.close()
        self.window.deleteLater()
        self.app.theme_manager = self.previous_manager
        self.temp_dir.cleanup()
        self.app.processEvents()

    def test_all_primary_regions_refresh_and_new_widgets_inherit_theme(self):
        state = self.window.get_current_session()
        session_id = state.session_id
        self.window.input_field.setPlainText("不要丢失的输入")
        profile = {
            "id": "runtime-coverage",
            "name": "Runtime coverage",
            "overrides": {
                "tokens": {
                    "bg_sidebar": "#111111",
                    "sidebar_text": "#eeeeee",
                    "bg_chat": "#121212",
                    "chat_text": "#f0f0f0",
                    "composer_bg": "#181818",
                    "composer_text": "#f4f4f4",
                    "right_sidebar_bg": "#161616",
                    "right_sidebar_text": "#f2f2f2",
                    "management_bg": "#101010",
                    "overlay_bg": "#171717",
                    "preview_shell_bg": "#151515",
                    "sidebar_width": 260,
                    "conversation_preferred_width": 980,
                }
            },
        }
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(
                self.manager.apply_profile(profile, preview=True, reason="coverage"),
                self.manager.last_error,
            )
        self.assertIn("#111111", self.window.sidebar.styleSheet())
        self.assertIn("#121212", self.window.conversation_page.styleSheet())
        self.assertIn("#181818", self.window.input_card.styleSheet())
        self.assertIn("#161616", self.window.right_sidebar.styleSheet())
        self.assertNotIn("#111111", self.window.theme_preview_bar.styleSheet())
        self.assertNotIn(
            id(self.window.theme_preview_bar),
            self.manager.binding_registry._bindings,
        )
        self.assertEqual(self.window.theme_preview_bar.minimumHeight(), 44)
        self.assertEqual(self.window.theme_preview_bar.maximumHeight(), 44)
        self.assertEqual(self.window.input_field.toPlainText(), "不要丢失的输入")
        self.assertEqual(self.window.get_current_session().session_id, session_id)

        late_bubble = ChatBubble("User", "后创建消息")
        late_file = FileChip("report.md")
        late_guidance = GuidanceTimelineEvent(
            "guidance-1",
            "继续验证后创建的对话控件。",
        )
        late_interaction = InlineInteractionCard(
            {
                "kind": "approval",
                "title": "需要确认",
                "message": "验证弹层反馈主题。",
            }
        )
        late_chip = SessionContextChip("已选能力")
        try:
            self.assertIn("#eef0ff", late_bubble.user_bubble_frame.styleSheet())
            self.assertIn("#f0f0f0", late_bubble.user_content_edit.styleSheet())
            self.assertIn("#f0f0f0", late_file.text_label.styleSheet())
            self.assertIn("#f0f0f0", late_guidance.content_label.styleSheet())
            self.assertIn("#171717", late_interaction.styleSheet())
            self.assertIn("#181818", late_chip.styleSheet())
        finally:
            for widget in (
                late_bubble,
                late_file,
                late_guidance,
                late_interaction,
                late_chip,
            ):
                widget.deleteLater()

    def test_main_window_uses_native_frame(self):
        self.assertFalse(bool(self.window.windowFlags() & Qt.FramelessWindowHint))
        self.assertFalse(hasattr(self.window, "app_title_bar"))

    def test_native_close_keeps_existing_tray_behavior(self):
        tray = MagicMock()
        event = QCloseEvent()
        self.window.tray_icon = tray
        self.window.show()
        self.app.processEvents()
        try:
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(self.window.isVisible())
            tray.showMessage.assert_called_once()
        finally:
            self.window.tray_icon = None

    def test_closed_skill_picker_is_removed_before_theme_refresh(self):
        picker = SessionSkillPickerPopover([], parent=self.window)
        self.window.session_skill_popover = picker
        binding_id = id(picker)
        self.assertIn(binding_id, self.manager.binding_registry._bindings)

        picker.show()
        picker.close()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

        self.assertFalse(is_qt_object_valid(picker))
        self.assertNotIn(binding_id, self.manager.binding_registry._bindings)
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(
                self.manager.apply_repository_state(reason="closed_popover_regression"),
                self.manager.last_error,
            )

    def test_saved_theme_refresh_failure_tells_user_to_restart(self):
        self.manager.last_failure = {"saved_requires_restart": True}
        with patch.object(self.window, "add_system_toast") as add_toast:
            self.window._handle_theme_apply_failed("测试刷新错误")

        message, level = add_toast.call_args.args
        self.assertEqual(level, "error")
        self.assertIn("主题已保存", message)
        self.assertIn("请重启应用", message)
        self.assertIn("测试刷新错误", message)

    def test_settings_save_uses_page_warning_without_duplicate_toast(self):
        self.manager.last_failure = {
            "reason": "settings_save",
            "saved_requires_restart": True,
        }
        with patch.object(self.window, "add_system_toast") as add_toast:
            self.window._handle_theme_apply_failed("设置页刷新错误")

        add_toast.assert_not_called()

    def test_acknowledged_local_theme_write_does_not_trigger_external_refresh(self):
        self.manager.repository.replace_state(
            themes=[],
            active_theme_id="default",
            default_tokens={},
        )
        self.manager.acknowledge_repository_state()
        with patch.object(self.manager, "apply_repository_state") as apply_state:
            self.manager.poll_external_changes()
            apply_state.assert_not_called()

            with open(self.manager.repository.store_path, "a", encoding="utf-8") as stream:
                stream.write(" ")
            self.manager.poll_external_changes()
            apply_state.assert_called_once_with(
                reason="external_change",
                persisted_on_failure=True,
            )

    def test_preview_bar_save_recommends_restart_after_success(self):
        self.manager.repository.write_preview(
            name="AI 主题",
            overrides={},
            default_tokens={},
            session_id="ai-session",
        )
        with patch.object(
            self.manager,
            "commit_current_preview",
            return_value={"theme": {"name": "AI 主题"}},
        ), patch.object(self.window, "add_system_toast") as add_toast:
            self.window._save_theme_preview_from_bar()

        message, level = add_toast.call_args.args
        self.assertEqual(level, "warning")
        self.assertIn("已保存并启用", message)
        self.assertIn("建议重启应用", message)

    def test_declarative_workspace_theme_changes_presentation_not_actions(self):
        original_session_count = len(self.window.sessions)
        profile = {
            "id": "workspace-manifest",
            "name": "Workspace manifest",
            "schema_version": 2,
            "overrides": {},
            "assets": {},
            "workspace_scene": {
                "attachment": "fixed",
                "layers": [
                    {"type": "solid", "color": "#e9efea"},
                    {
                        "type": "grid",
                        "color": "rgba(102,119,170,0.18)",
                        "spacing": 18,
                        "line_width": 1,
                    },
                ],
            },
            "surfaces": {
                "shell.left_sidebar": {
                    "material": {"kind": "transparent"}
                }
            },
            "components": {
                "left.capabilities": {"visible": False},
                "left.automation": {"icon": {"source": "builtin", "name": "fa5s.clock"}},
            },
            "content": {
                "brand.title": "My Cowork",
                "home.title": "选择一个起点",
            },
        }
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(self.manager.apply_profile(profile, preview=True, reason="manifest"))
        self.assertEqual(self.window.windowTitle(), "My Cowork")
        self.assertTrue(self.window.product_nav_buttons[self.window.PAGE_CAPABILITIES].isHidden())
        self.assertFalse(self.window.new_chat_btn.isHidden())
        self.assertFalse(self.window.action_btn.isHidden())
        self.window.new_chat_btn.click()
        self.assertEqual(len(self.window.sessions), original_session_count + 1)
        canvas = self.window.workspace_theme_controller.scene_canvas
        self.assertIsNotNone(canvas)
        self.assertFalse(canvas.isHidden())
        self.assertEqual(canvas.layers[1]["type"], "grid")
        self.assertEqual(len(self.window.findChildren(type(canvas))), 1)
        state = self.window.get_current_session()
        self.assertEqual(state.empty_state.title_label.text(), "选择一个起点")
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(self.manager.restore_saved_theme(reason="brand_title_restore"))
        self.assertEqual(self.window.windowTitle(), "DeepSeek Cowork")

    def test_workspace_scene_canvas_uses_one_global_grid_and_revision_cache(self):
        host = QWidget()
        host.resize(130, 98)
        canvas = WorkspaceSceneCanvas(host)
        canvas.set_scene(
            {
                "attachment": "fixed",
                "layers": [
                    {"type": "solid", "color": "#ffffff", "opacity": 1, "blend": "source_over"},
                    {
                        "type": "grid",
                        "color": "#d8d8d8",
                        "opacity": 1,
                        "blend": "source_over",
                        "spacing": 32,
                        "line_width": 1,
                        "major_every": 4,
                        "major_color": "#ff0000",
                        "major_line_width": 1,
                    },
                ],
            },
            {},
            {},
            revision="7",
        )
        host.show()
        self.app.processEvents()
        try:
            self.assertEqual(canvas.geometry(), host.rect())
            first = canvas._rendered_scene()
            second = canvas._rendered_scene()
            self.assertEqual(first.cacheKey(), second.cacheKey())
            image = first.toImage()
            self.assertGreater(image.pixelColor(0, 20).red(), 240)
            self.assertLess(image.pixelColor(32, 20).red(), 245)
            host.resize(162, 98)
            self.app.processEvents()
            resized = canvas._rendered_scene()
            self.assertEqual(resized.width() / resized.devicePixelRatio(), 162)
            self.assertNotEqual(first.cacheKey(), resized.cacheKey())
        finally:
            host.close()
            host.deleteLater()

    def test_workspace_scene_decode_failure_restores_previous_scene_and_objects(self):
        state = self.window.get_current_session()
        original_input = self.window.input_field
        valid = {
            "id": "scene-before-failure",
            "name": "Scene before failure",
            "schema_version": 2,
            "overrides": {},
            "assets": {},
            "workspace_scene": {
                "attachment": "fixed",
                "layers": [{"type": "solid", "color": "#e9efea"}],
            },
            "surfaces": {},
            "components": {},
            "content": {},
        }
        broken = {
            **valid,
            "id": "scene-broken-asset",
            "name": "Broken asset",
            "assets": {
                "missing": {
                    "path": "assets/missing.png",
                    "media_type": "image/png",
                    "sha256": "0" * 64,
                    "width": 32,
                    "height": 32,
                }
            },
            "workspace_scene": {
                "attachment": "fixed",
                "layers": [{"type": "image", "asset": "missing", "fit": "cover"}],
            },
        }
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(self.manager.apply_profile(valid, preview=True, reason="scene_valid"))
            self.assertFalse(self.manager.apply_profile(broken, preview=True, reason="scene_broken"))
        self.assertIn("无法解码", self.manager.last_error)
        self.assertEqual(
            self.window.workspace_theme_controller.scene_canvas.layers[0]["type"],
            "solid",
        )
        self.assertIs(self.window.input_field, original_input)
        self.assertEqual(self.window.get_current_session().session_id, state.session_id)
        self.window._set_theme_brand_title("")
        self.assertEqual(self.window.windowTitle(), "DeepSeek Cowork")

    def test_active_theme_does_not_flash_windows_for_new_or_historical_sessions(self):
        with patch(
            "core.theme.QFontDatabase.families",
            return_value=["Microsoft YaHei UI", "Consolas"],
        ):
            self.assertTrue(
                self.manager.apply_profile(
                    {
                        "id": "active-theme",
                        "name": "Active theme",
                        "schema_version": 2,
                        "overrides": {},
                        "assets": {},
                        "workspace_scene": {"attachment": "fixed", "layers": []},
                        "surfaces": {},
                        "components": {},
                        "content": {},
                    },
                    preview=True,
                    reason="active_theme_window_regression",
                )
            )
        tracker = TopLevelShowTracker()
        self.app.installEventFilter(tracker)
        try:
            session_id = self.window.create_new_session()
            state = self.window.get_session(session_id)
            cards = list(state.empty_state.action_cards)
            for card in cards:
                self.assertIs(card.parentWidget(), state.empty_state.grid_widget)
                self.assertFalse(card.isWindow())
            self.window.clear_chat_layout(state.chat_layout)
            state.empty_state = None
            state.messages = [
                {"id": "u1", "role": "user", "content": "历史问题"},
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "历史回答",
                    "meta": {
                        "ui_turn_group_id": "history-turn",
                        "ui_stage_id": "history-turn:final",
                        "ui_reply_kind": "final",
                    },
                },
            ]
            state.render_items = build_conversation_render_spans(state.messages)
            self.window._render_session_history_spans(state, state.render_items)
            self.app.processEvents()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
        finally:
            self.app.removeEventFilter(tracker)

        self.assertEqual(tracker.events, [])

    def test_detached_message_theme_visibility_waits_for_layout_parent(self):
        tracker = TopLevelShowTracker()
        self.app.installEventFilter(tracker)
        try:
            bubble = ChatBubble("User", "待挂载消息")
            self.app.processEvents()
            self.assertFalse(bubble.isVisible())

            state = self.window.get_current_session()
            state.chat_layout.insertWidget(0, bubble)
            self.app.processEvents()
            self.assertFalse(bubble.isHidden())

            hidden_profile = {
                "id": "hidden-message",
                "name": "Hidden message",
                "schema_version": 2,
                "overrides": {},
                "assets": {},
                "workspace_scene": {"attachment": "fixed", "layers": []},
                "surfaces": {},
                "components": {"conversation.user_message": {"visible": False}},
                "content": {},
            }
            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                self.assertTrue(
                    self.manager.apply_profile(hidden_profile, preview=True, reason="hide_message")
                )
            self.assertTrue(bubble.isHidden())
        finally:
            self.app.removeEventFilter(tracker)

        self.assertEqual(tracker.events, [])

    def test_visible_chat_layout_keeps_multiple_user_messages_materialized(self):
        with patch.object(self.window, "start_background_services", return_value=None):
            self.window.resize(1200, 800)
            self.window.show()
            self.app.processEvents()
        bubbles = [
            self.window.add_chat_bubble(
                "User",
                f"连续用户消息 {index}",
                animate=False,
                source_message_id=f"visible-user-{index}",
            )
            for index in range(3)
        ]
        self.app.processEvents()
        state = self.window.get_current_session()
        chat_width = state.chat_layout.parentWidget().width()
        for bubble in bubbles:
            item_index = state.chat_layout.indexOf(bubble)
            self.assertGreaterEqual(item_index, 0)
            self.assertFalse(state.chat_layout.itemAt(item_index).isEmpty())
            self.assertFalse(bubble.isHidden())
            self.assertTrue(bubble.user_bubble_frame.isVisible())
            self.assertTrue(bubble.user_content_edit.isVisible())
            self.assertLessEqual(bubble.geometry().width(), chat_width)
            self.assertLess(bubble.geometry().height(), 200)


if __name__ == "__main__":
    unittest.main()
