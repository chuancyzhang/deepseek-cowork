import os
import hashlib
import inspect
import subprocess
import tempfile
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from core.chat_storage import ChatStorage
from core.clarify_mode import GRILL_MODE_ARMED, GRILL_MODE_DISABLED
from core.conversation_render import is_legacy_skill_change_notice_message
from core.runtime_journal import RuntimeJournal
from core.theme import DesignTokens

from main import (
    AssistantTurnGroup,
    SummonedAgentProcessBlock,
    ChatBubble,
    ComposerActionPopover,
    ConversationSkillOptionsDialog,
    ConversationSkillPreviewDialog,
    ConversationSkillRangeDialog,
    InlineInteractionCard,
    InteractionChoiceButton,
    MainWindow,
    ModelSelectorPopover,
    ModelSwitchInlineNotice,
    SearchableSkillPickerButton,
    SessionSkillPickerPopover,
    GuidanceTimelineEvent,
    ToolCallCard,
    build_sub_agent_history_events,
    extract_related_paths,
    launch_daemon_subprocess,
    summarize_tool_action,
)
from ui.primitives import ProductActionRow, ProductEmptyState, ProductPopover


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


class ConversationLinearInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._save_chat_patch = patch.object(
            MainWindow,
            "save_chat_history",
            new=lambda self, session_id=None, flush=False: True,
        )
        self._checkpoint_patch = patch.object(
            MainWindow,
            "_checkpoint_live_chat",
            new=lambda self, state: False,
        )
        self._save_chat_patch.start()
        self._checkpoint_patch.start()

    def tearDown(self):
        self.app.processEvents()
        self._checkpoint_patch.stop()
        self._save_chat_patch.stop()

    def test_skill_picker_searches_description_and_preserves_hidden_selection(self):
        skills = [
            {
                "name": "company-one-page",
                "display_name": "Company One Page",
                "description": "企业单页分析",
                "type": "ai_generated",
            },
            {
                "name": "market-brief",
                "display_name": "Market Brief",
                "description": "市场日报",
                "type": "ai_generated",
            },
        ]
        picker = SessionSkillPickerPopover(skills, ["company-one-page"])
        self.assertFalse(picker.apply_btn.isEnabled())
        self.assertFalse(picker.selection_chip_scroll.isHidden())
        self.assertEqual(picker.selection_chip_layout.count(), 2)
        picker._filter_items("市场日报")
        self.assertTrue(picker._items[0].isHidden())
        self.assertIn("company-one-page", picker.selected_names)
        picker.clear_selection()
        self.assertTrue(picker.apply_btn.isEnabled())
        self.assertTrue(picker.selection_chip_scroll.isHidden())
        picker.deleteLater()

    def test_skill_picker_excludes_builtin_abilities_and_cleans_legacy_selection(self):
        skills = [
            {"name": "skill-importer", "display_name": "能力导入", "type": "system"},
            {
                "name": "browser-automation",
                "display_name": "浏览器自动化",
                "source_type": "bundled_plugin",
            },
            {
                "name": "custom-research",
                "display_name": "自定义研究",
                "type": "ai_generated",
            },
        ]

        picker = SessionSkillPickerPopover(
            skills,
            ["skill-importer", "browser-automation"],
        )

        self.assertEqual(
            [item.data(Qt.UserRole) for item in picker._items],
            ["browser-automation", "custom-research"],
        )
        self.assertNotIn("skill-importer", picker.selected_names)
        self.assertEqual(picker.selected_values(), ["browser-automation"])
        self.assertTrue(picker.apply_btn.isEnabled())
        picker.deleteLater()

    def test_skill_picker_shows_an_explicit_empty_state_when_only_builtins_exist(self):
        picker = SessionSkillPickerPopover(
            [{"name": "skill-importer", "display_name": "能力导入", "type": "system"}],
        )

        self.assertTrue(picker.skill_list.isHidden())
        self.assertFalse(picker.empty_label.isHidden())
        self.assertEqual(picker.empty_label.text(), "暂无可指定的非内置能力")
        picker.deleteLater()

    def test_model_picker_keeps_channel_model_and_effort(self):
        profiles = [
            {
                "id": "grok-45",
                "channel_display_name": "grok",
                "display_name": "grok-4.5",
                "model_name": "grok-4.5",
                "reasoning_efforts": ["low", "high"],
                "reasoning_effort": "high",
            }
        ]
        picker = ModelSelectorPopover(profiles, "grok-45")
        self.assertEqual(picker.model_list.item(0).text(), "grok / grok-4.5")
        effort_buttons = [picker.effort_row.itemAt(i).widget() for i in range(picker.effort_row.count())]
        self.assertTrue(any(button and button.text() == "高" and button.isChecked() for button in effort_buttons))
        picker.deleteLater()

    def test_model_picker_viewport_click_emits_selected_model(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("模型", host)
        anchor.setGeometry(300, 420, 80, 32)
        profiles = [
            {"id": "model-a", "display_name": "A", "model_name": "a"},
            {"id": "model-b", "display_name": "B", "model_name": "b"},
        ]
        picker = ModelSelectorPopover(profiles, "model-a", host)
        selected = []
        picker.modelSelected.connect(selected.append)
        host.show()
        self.app.processEvents()
        self.assertTrue(picker.show_for(anchor, prefer_above=True))

        item = picker.model_list.item(1)
        QTest.mouseClick(
            picker.model_list.viewport(),
            Qt.LeftButton,
            pos=picker.model_list.visualItemRect(item).center(),
        )
        self.app.processEvents()

        self.assertEqual(selected, ["model-b"])
        self.assertEqual(picker.selected_id, "model-b")
        host.deleteLater()

    def test_composer_add_popover_has_no_duplicate_agent_entry(self):
        source = inspect.getsource(ComposerActionPopover.__init__)
        for title in ("添加文件", "指定能力", "拷问模式", "沉淀为 Skill"):
            self.assertIn(title, source)
        self.assertNotIn("添加智能体", source)

    def test_composer_add_popover_clicks_enabled_action_and_keeps_disabled_action_inert(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+", host)
        anchor.setGeometry(300, 420, 32, 32)
        hits = []
        state = SimpleNamespace(selected_skill_names=[], messages=[], grill_mode_state="disabled")
        window = SimpleNamespace(
            pending_conversation_skill_result=None,
            skill_manager_ready=True,
            skill_load_error="",
            conversation_skill_worker=None,
            get_current_session=lambda: state,
            _session_is_busy=lambda _state: False,
            select_files_for_prompt=lambda: hits.append("file"),
            open_session_skill_picker=lambda: hits.append("skill"),
            toggle_grill_mode=lambda: hits.append("grill"),
            start_conversation_skill_flow=lambda: hits.append("capture"),
            input_field=SimpleNamespace(toPlainText=lambda: ""),
            save_composer_as_favorite=lambda: hits.append("favorite"),
        )
        popover = ComposerActionPopover(window, host)
        rows = popover.findChildren(ProductActionRow)
        host.show()
        self.app.processEvents()
        self.assertTrue(popover.show_for(anchor, prefer_above=True))

        QTest.mouseClick(rows[0].title_label, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(hits, ["file"])
        self.assertTrue(popover.isHidden())

        second_popover = ComposerActionPopover(window, host)
        second_rows = second_popover.findChildren(ProductActionRow)
        second_popover.show_for(anchor, prefer_above=True)
        QTest.mouseClick(second_rows[3].title_label, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(hits, ["file", "grill"])

        third_popover = ComposerActionPopover(window, host)
        third_rows = third_popover.findChildren(ProductActionRow)
        self.assertFalse(third_rows[-1].isEnabled())
        third_popover.show_for(anchor, prefer_above=True)
        QTest.mouseClick(third_rows[-1].title_label, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(hits, ["file", "grill"])
        host.deleteLater()

    def test_inline_choice_request_resolves_without_dialog(self):
        card = InlineInteractionCard(
            {
                "kind": "choice",
                "title": "选择环境",
                "message": "请选择要查看的环境",
                "options": [{"label": "运行环境", "value": "runtime"}],
                "timeout_seconds": 120,
            }
        )
        values = []
        card.resolved.connect(values.append)
        card.option_checks[0].setChecked(True)
        card._submit()
        self.assertEqual(values, ["runtime"])
        self.assertFalse(card.submit_btn.isEnabled())
        card.deleteLater()

    def test_inline_questionnaire_uses_theme_owned_choice_rows(self):
        card = InlineInteractionCard(
            {
                "kind": "questionnaire",
                "questions": [{
                    "id": "priority",
                    "question": "优先处理什么？",
                    "options": [
                        {"label": "质量", "value": "quality", "description": "先保证正确"},
                        {"label": "自定义", "value": "__custom__"},
                    ],
                }],
            }
        )
        self.assertFalse(card.findChildren(QComboBox))
        choices = card.findChildren(InteractionChoiceButton)
        self.assertEqual(len(choices), 2)
        choices[1].click()
        self.assertTrue(card.question_controls[0]["input"].isEnabled())
        card.deleteLater()

    def test_skill_preview_constructs_with_product_action_bar(self):
        dialog = ConversationSkillPreviewDialog(
            {"skill_name": "demo-skill", "experience_items": ["lesson"]},
            mode="create",
        )
        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertIn("保存 Skill", labels)
        dialog.deleteLater()

    def test_resolved_inline_request_is_removed_from_conversation(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            values = []
            card = window._show_inline_interaction_request(
                {
                    "request_id": "remove-after-submit",
                    "kind": "choice",
                    "options": [{"label": "继续", "value": "continue"}],
                },
                state.session_id,
                values.append,
            )
            card.option_checks[0].setChecked(True)
            card._submit()
            self.app.processEvents()
            self.assertEqual(values, ["continue"])
            self.assertNotIn("remove-after-submit", state.pending_interactions)
            self.assertEqual(state.chat_layout.indexOf(card), -1)
            self.assertTrue(card.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_background_inline_request_keeps_waiting_input_toast(self):
        window = MainWindow()
        try:
            background_id = window.create_new_session("background-interaction", make_current=False)
            background = window.get_session(background_id)
            window.add_system_toast = MagicMock()

            window._show_inline_interaction_request(
                {
                    "request_id": "background-waiting",
                    "kind": "choice",
                    "options": [{"label": "继续", "value": "continue"}],
                },
                background.session_id,
                lambda _value: None,
            )

            window.add_system_toast.assert_called_once_with(
                "后台对话正在等待你的输入",
                "info",
                session_id=background.session_id,
                auto_close_ms=0,
            )
        finally:
            window.close()
            window.deleteLater()

    def test_legacy_interaction_dialog_cannot_create_top_level_surface(self):
        with self.assertRaisesRegex(RuntimeError, "InlineInteractionCard"):
            MainWindow.show_interaction_dialog(object(), {"kind": "text"})

    def test_retiring_first_send_empty_state_does_not_create_window(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        empty_state = QWidget(host)
        layout.addWidget(empty_state)
        state = SimpleNamespace(session_id="first-submit", empty_state=empty_state, chat_layout=layout)
        before = {id(widget) for widget in QApplication.topLevelWidgets() if widget.isVisible()}
        self.assertTrue(MainWindow._retire_session_empty_state(object(), state, reason="test_first_submit"))
        self.app.processEvents()
        after = {id(widget) for widget in QApplication.topLevelWidgets() if widget.isVisible()}
        self.assertIsNone(state.empty_state)
        self.assertEqual(layout.indexOf(empty_state), -1)
        self.assertEqual(after - before, set())
        host.deleteLater()

    def test_daemon_launch_keeps_windows_console_hidden(self):
        with patch("main.subprocess.Popen") as popen:
            launch_daemon_subprocess(23333)
        kwargs = popen.call_args.kwargs
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW)

    def test_inline_request_resolution_failure_preserves_input_for_retry(self):
        window = MainWindow()
        try:
            state = window.get_current_session()

            def fail(_value):
                raise RuntimeError("bridge unavailable")

            card = window._show_inline_interaction_request(
                {"request_id": "retry-submit", "kind": "text"},
                state.session_id,
                fail,
            )
            card.text_input.setText("保留这段输入")
            card._submit()
            self.app.processEvents()
            self.assertIn("retry-submit", state.pending_interactions)
            self.assertEqual(card.text_input.text(), "保留这段输入")
            self.assertTrue(card.submit_btn.isEnabled())
            self.assertIn("提交失败", card.validation_label.text())
        finally:
            window.close()
            window.deleteLater()

    def test_product_popover_is_single_in_window_overlay(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+​", host)
        anchor.setGeometry(300, 420, 32, 32)
        popover = ProductPopover(host, width=260)
        layout = QVBoxLayout(popover)
        layout.addWidget(QLabel("单一操作列表"))
        host.show()
        self.app.processEvents()

        self.assertTrue(popover.show_for(anchor, prefer_above=True))
        self.assertFalse(popover.isWindow())
        self.assertIs(popover.parentWidget(), host)
        self.assertTrue(host.rect().contains(popover.geometry()))
        QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(8, 8))
        self.app.processEvents()
        self.assertTrue(popover.isHidden())
        host.deleteLater()

    def test_product_popover_closes_sibling_and_is_deleted_after_close(self):
        host = QWidget()
        host.resize(640, 480)
        first_anchor = QPushButton("+", host)
        first_anchor.setGeometry(260, 420, 32, 32)
        second_anchor = QPushButton("Agent", host)
        second_anchor.setGeometry(300, 420, 72, 32)
        first = ProductPopover(host, width=260)
        QVBoxLayout(first).addWidget(QLabel("添加上下文"))
        second = ProductPopover(host, width=260)
        QVBoxLayout(second).addWidget(QLabel("选择 Agent"))
        host.show()
        self.app.processEvents()

        self.assertTrue(first.show_for(first_anchor, prefer_above=True))
        self.assertTrue(second.show_for(second_anchor, prefer_above=True))
        self.assertTrue(first.isHidden())
        self.assertTrue(second.isVisible())
        self.assertEqual(
            len([item for item in host.findChildren(ProductPopover) if item.isVisible()]),
            1,
        )

        second.close()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(host.findChildren(ProductPopover), [])
        host.deleteLater()

    def test_composer_context_button_reopens_without_accumulating_popovers(self):
        window = MainWindow()
        window.show()
        self.app.processEvents()
        tracker = TopLevelShowTracker()
        self.app.installEventFilter(tracker)
        try:
            QTest.mouseClick(window.tool_menu_btn, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(
                len([item for item in window.findChildren(ProductPopover) if item.isVisible()]),
                1,
            )

            QTest.mouseClick(window.tool_menu_btn, Qt.LeftButton)
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertEqual(window.findChildren(ProductPopover), [])
            self.assertIsNone(window.composer_action_popover)

            QTest.mouseClick(window.tool_menu_btn, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(len(window.findChildren(ProductPopover)), 1)
            self.assertTrue(window.composer_action_popover.isVisible())
            self.assertEqual(tracker.events, [])
            for row in window.composer_action_popover.findChildren(ProductActionRow):
                for label in (row.icon_label, row.title_label, row.detail_label):
                    self.assertIs(label.parentWidget(), row)
                    self.assertFalse(label.isWindow())
        finally:
            self.app.removeEventFilter(tracker)
            window.close()
            window.deleteLater()

    def test_grill_mode_is_one_shot_composer_context(self):
        state = SimpleNamespace(
            session_id="grill-session",
            grill_mode_state=GRILL_MODE_DISABLED,
            grill_round_count=0,
            grill_cycle_count=0,
            grill_execution_confirmed=False,
        )
        window = MainWindow.__new__(MainWindow)
        window.get_current_session = lambda: state
        window._session_is_busy = lambda _state: False
        window.refresh_grill_mode_controls = MagicMock()
        window.refresh_composer_action_state = MagicMock()
        window.add_system_toast = MagicMock()

        MainWindow.toggle_grill_mode(window)
        self.assertEqual(state.grill_mode_state, GRILL_MODE_ARMED)
        window.refresh_grill_mode_controls.assert_called_with(state.session_id)
        window.refresh_composer_action_state.assert_called_once()

        MainWindow.toggle_grill_mode(window)
        self.assertEqual(state.grill_mode_state, GRILL_MODE_DISABLED)
        self.assertEqual(window.refresh_composer_action_state.call_count, 2)

    def test_product_popover_keeps_native_window_target_click_inside(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+", host)
        anchor.setGeometry(300, 420, 32, 32)
        popover = ProductPopover(host, width=260)
        layout = QVBoxLayout(popover)
        layout.addWidget(QLabel("单一操作列表"))
        host.show()
        self.app.processEvents()
        self.assertTrue(popover.show_for(anchor, prefer_above=True))

        global_position = popover.mapToGlobal(popover.rect().center())
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(popover.mapFromGlobal(global_position)),
            QPointF(global_position),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        popover.eventFilter(host.windowHandle(), event)

        self.assertTrue(popover.isVisible())
        host.deleteLater()

    def test_product_action_row_child_label_click_triggers_action(self):
        row = ProductActionRow("添加文件", "图片、文档或其他工作资料")
        hits = []
        row.clicked.connect(lambda: hits.append("file"))
        row.show()
        self.app.processEvents()

        QTest.mouseClick(row.title_label, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(hits, ["file"])
        row.deleteLater()

    def test_product_action_row_never_shows_detail_as_top_level_window(self):
        tracker = TopLevelShowTracker()
        self.app.installEventFilter(tracker)
        try:
            row = ProductActionRow("添加文件", "图片、文档或其他工作资料")
        finally:
            self.app.removeEventFilter(tracker)

        self.assertEqual(tracker.events, [])
        self.assertIs(row.icon_label.parentWidget(), row)
        self.assertIs(row.title_label.parentWidget(), row)
        self.assertIs(row.detail_label.parentWidget(), row)
        for label in (row.icon_label, row.title_label, row.detail_label):
            self.assertFalse(label.isWindow())
            self.assertFalse(label.windowFlags() & Qt.Window)
        row.deleteLater()

    def test_product_empty_state_never_shows_children_as_top_level_windows(self):
        tracker = TopLevelShowTracker()
        self.app.installEventFilter(tracker)
        try:
            empty_state = ProductEmptyState(
                "还没有文件",
                "选择工作区后，可以在这里查找文件并预览交付物。",
                action_text="选择工作区",
            )
        finally:
            self.app.removeEventFilter(tracker)

        self.assertEqual(tracker.events, [])
        children = [
            empty_state.title_label,
            empty_state.description_label,
            empty_state.action_button,
        ]
        for child in children:
            self.assertIs(child.parentWidget(), empty_state)
            self.assertFalse(child.isWindow())
        empty_state.deleteLater()

    def test_model_selection_updates_session_meta_and_refreshes_controls(self):
        state = SimpleNamespace(selected_model_id="model-a", persisted_conversation_meta={})

        class WindowStub:
            _set_session_model_id = MainWindow._set_session_model_id
            _model_id_for_state = MainWindow._model_id_for_state
            _session_has_visible_conversation = staticmethod(MainWindow._session_has_visible_conversation)

            def __init__(self):
                self.saved_session_ids = []
                self.model_refreshes = 0
                self.badge_refreshes = 0

            def get_current_session(self):
                return state

            def _model_profile_for_state(self, _state=None, model_id=None):
                if model_id == "model-b":
                    return {"id": "model-b", "display_name": "B"}
                return {}

            def save_chat_history(self, session_id=None):
                self.saved_session_ids.append(session_id)

            def refresh_model_selector(self):
                self.model_refreshes += 1

            def refresh_context_badges(self):
                self.badge_refreshes += 1

            def add_system_toast(self, *_args, **_kwargs):
                raise AssertionError("successful selection must not show an error toast")

        state.session_id = "session-1"
        window = WindowStub()

        self.assertTrue(MainWindow.on_model_selection_changed(window, "model-b"))
        self.assertEqual(state.selected_model_id, "model-b")
        self.assertEqual(state.persisted_conversation_meta["selected_model_id"], "model-b")
        self.assertEqual(window.saved_session_ids, ["session-1"])
        self.assertEqual(window.model_refreshes, 1)
        self.assertEqual(window.badge_refreshes, 1)

    def test_model_switch_notice_is_runtime_only_and_uses_divider_copy(self):
        container = QWidget()
        chat_layout = QVBoxLayout(container)
        chat_layout.addStretch()
        messages = [
            {"id": "user-1", "role": "user", "content": "继续处理"},
            {"id": "assistant-1", "role": "assistant", "content": "好的"},
        ]
        state = SimpleNamespace(
            session_id="session-model-switch",
            selected_model_id="model-a",
            persisted_conversation_meta={},
            messages=messages,
            chat_layout=chat_layout,
        )

        class WindowStub:
            _set_session_model_id = MainWindow._set_session_model_id
            _model_id_for_state = MainWindow._model_id_for_state
            _session_has_visible_conversation = staticmethod(MainWindow._session_has_visible_conversation)
            _append_model_switch_notice = MainWindow._append_model_switch_notice

            def __init__(self):
                self.saved_session_ids = []
                self.scroll_requests = []

            def get_current_session(self):
                return state

            def _model_profile_for_state(self, _state=None, model_id=None):
                if model_id == "model-b":
                    return {"id": "model-b", "display_name": "B"}
                return {}

            def save_chat_history(self, session_id=None):
                self.saved_session_ids.append(session_id)

            def refresh_model_selector(self):
                return None

            def refresh_context_badges(self):
                return None

            def request_session_scroll_to_bottom(self, session_id, force=False):
                self.scroll_requests.append((session_id, force))

            def add_system_toast(self, *_args, **_kwargs):
                raise AssertionError("successful selection must not show an error toast")

        original_messages = list(messages)
        window = WindowStub()
        with patch("main.log_ui_navigation") as log_mock:
            self.assertTrue(MainWindow.on_model_selection_changed(window, "model-b"))

        self.assertEqual(state.messages, original_messages)
        self.assertEqual(state.selected_model_id, "model-b")
        self.assertEqual(window.saved_session_ids, ["session-model-switch"])
        self.assertEqual(window.scroll_requests, [("session-model-switch", False)])
        self.assertEqual(chat_layout.count(), 2)
        notice = chat_layout.itemAt(0).widget()
        self.assertIsInstance(notice, ModelSwitchInlineNotice)
        self.assertEqual(notice.label.text(), "模型已切换，下一轮可能变慢，缓存将重新建立")
        self.assertEqual(notice.left_line.height(), 1)
        self.assertEqual(notice.right_line.height(), 1)
        log_mock.assert_called_once_with(
            "model_switch",
            session_id="session-model-switch",
            previous_model_id="model-a",
            selected_model_id="model-b",
            outcome="changed",
            notice_shown=True,
        )
        container.deleteLater()

    def test_model_selection_skips_notice_for_empty_or_unchanged_session(self):
        state = SimpleNamespace(
            session_id="session-empty",
            selected_model_id="model-a",
            persisted_conversation_meta={},
            messages=[],
        )

        class WindowStub:
            _set_session_model_id = MainWindow._set_session_model_id
            _model_id_for_state = MainWindow._model_id_for_state
            _session_has_visible_conversation = staticmethod(MainWindow._session_has_visible_conversation)

            def __init__(self):
                self.saved = 0
                self.notices = 0

            def get_current_session(self):
                return state

            def _model_profile_for_state(self, _state=None, model_id=None):
                return {"id": model_id} if model_id in {"model-a", "model-b"} else {}

            def save_chat_history(self, session_id=None):
                self.saved += 1

            def refresh_model_selector(self):
                return None

            def refresh_context_badges(self):
                return None

            def _append_model_switch_notice(self, _state):
                self.notices += 1

            def add_system_toast(self, *_args, **_kwargs):
                raise AssertionError("valid selection must not show an error toast")

        window = WindowStub()
        with patch("main.log_ui_navigation"):
            self.assertTrue(MainWindow.on_model_selection_changed(window, "model-a"))
            self.assertEqual(window.saved, 0)
            self.assertTrue(MainWindow.on_model_selection_changed(window, "model-b"))

        self.assertEqual(window.saved, 1)
        self.assertEqual(window.notices, 0)

    def test_thinking_stays_expanded_when_finalized(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        bubble.think_toggle_btn.setChecked(True)
        bubble.update_thinking(duration=2.5, is_final=True)
        self.assertTrue(bubble.think_toggle_btn.isChecked())
        self.assertIn("2.5 秒", bubble.think_toggle_btn.text())
        bubble.deleteLater()

    def test_completed_live_turn_folds_process_and_can_expand_it_again(self):
        group = AssistantTurnGroup("turn-1-group-1")
        first = ChatBubble("Agent", "")
        first.update_thinking("先检查环境")
        first.set_main_content("阶段结果", final=True)
        tool = ToolCallCard("run_command", {"command": "pytest"}, "tool-1")
        first.add_tool_card(tool)
        final = ChatBubble("Agent", "")
        final.update_thinking("汇总结论")
        final.set_main_content("检查完成。", final=True)
        group.add_stage(first)
        group.add_stage(final)

        self.assertTrue(group.process_disclosure.isHidden())
        self.assertTrue(first.isVisibleTo(group))
        group.finalize_process(final)

        self.assertFalse(group.process_disclosure.isHidden())
        self.assertIn("2 个阶段", group.process_disclosure.text())
        self.assertIn("1 次 Tool 调用", group.process_disclosure.text())
        self.assertTrue(first.isHidden())
        self.assertFalse(final.isHidden())
        self.assertTrue(final.thinking_widget.isHidden())
        self.assertEqual(final.main_content_text, "检查完成。")

        group.process_disclosure.setChecked(True)
        self.assertFalse(first.isHidden())
        self.assertFalse(final.thinking_widget.isHidden())
        self.assertFalse(first.think_container.isHidden())
        self.assertFalse(final.think_container.isHidden())

        group.process_disclosure.setChecked(False)
        self.assertTrue(first.isHidden())
        self.assertTrue(final.thinking_widget.isHidden())
        group.deleteLater()

    def test_live_process_waits_for_terminal_result_render_before_folding(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_live_process_fold_boundary")
            state.active_turn_id = 1
            window.set_session_status("running", state.session_id)
            bubble = window._append_live_thinking_segment(state)
            group = state.active_agent_turn_group
            bubble.update_thinking("仍在分析")
            bubble.set_main_content("正在输出", final=False)

            self.assertTrue(group.process_disclosure.isHidden())
            self.assertFalse(bubble.isHidden())
            window.set_session_status("completed", state.session_id)
            self.assertTrue(group.process_finalization_pending)
            self.assertTrue(group.process_disclosure.isHidden())
            self.assertFalse(bubble.isHidden())

            bubble.set_main_content("最终结果", final=True)
            self.assertFalse(group.process_finalization_pending)
            self.assertFalse(group.process_disclosure.isHidden())
            self.assertFalse(group.process_disclosure.isChecked())
            self.assertTrue(bubble.thinking_widget.isHidden())
            self.assertEqual(bubble.main_content_text, "最终结果")
        finally:
            window.close()
            window.deleteLater()

    def test_completed_lifecycle_clears_sidebar_activity_without_worker_state(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.messages = [{"id": "u1", "role": "user", "content": "hello"}]
            window.set_session_status("running", state.session_id)
            self.assertTrue(window._session_has_live_activity(state.session_id))

            window.set_session_status("finalizing", state.session_id)
            self.assertTrue(window._session_has_live_activity(state.session_id))

            window.set_session_status("completed", state.session_id)
            self.assertFalse(window._session_has_live_activity(state.session_id))
            self.assertFalse(state.live_activity)
        finally:
            window.close()
            window.deleteLater()

    def test_completed_lifecycle_ignores_stale_worker_and_daemon_flags(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            stale_worker = SimpleNamespace(
                isRunning=lambda: True,
                is_paused=False,
            )
            state.llm_worker = stale_worker
            state.daemon_running = True
            state.turn_steerable = True

            window.set_session_status("completed", state.session_id)
            window.normalize_session_ui(state)

            self.assertFalse(state.daemon_running)
            self.assertFalse(state.turn_steerable)
            self.assertFalse(window.stop_btn.isVisible())
            self.assertEqual(window.action_btn.text(), "开始")
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()

    def test_provider_retry_is_runtime_only_and_does_not_change_messages(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.messages = [{"id": "u1", "role": "user", "content": "hello"}]
            state.active_turn_id = 1
            window.set_session_status("running", state.session_id)
            before_messages = list(state.messages)
            before_phase = state.run_phase

            window.handle_observability_event(
                {
                    "type": "provider_retry",
                    "attempt": 3,
                    "max_retries": 5,
                    "reason": "connection reset",
                    "delay_seconds": 1.0,
                },
                state.session_id,
            )

            self.assertEqual(state.messages, before_messages)
            self.assertEqual(state.run_phase, before_phase)
            self.assertEqual(state.provider_retry_attempt, 3)
            self.assertEqual(state.provider_retry_max, 5)
            window.normalize_session_ui(state)
            self.assertEqual(window.loop_hint.text(), "正在重试 3/5")
            self.assertEqual(state.observability_events[-1]["type"], "provider_retry")
            session_meta = str(window._compose_session_meta(state))
            self.assertNotIn("provider_retry", session_meta)
            self.assertNotIn("正在重试", session_meta)
        finally:
            window.close()
            window.deleteLater()

    def test_normal_completion_has_no_interruption_warning_and_clears_activity(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_normal_terminal")
            state.messages = [{
                "id": "u-normal",
                "role": "user",
                "content": "你好",
                "meta": {"turn_id": "1", "request_id": "request-normal"},
            }]
            state.active_turn_id = 1
            state.active_turn_request_id = "request-normal"
            window.set_session_status("running", state.session_id)
            bubble = window._append_live_thinking_segment(state)

            window.handle_llm_response(
                {
                    "request_id": "request-normal",
                    "role": "assistant",
                    "content": "正常完成。",
                    "generated_messages": [{
                        "id": "a-normal",
                        "role": "assistant",
                        "content": "正常完成。",
                        "meta": {"turn_id": "1", "request_id": "request-normal"},
                    }],
                },
                state.session_id,
                turn_id=1,
            )

            self.assertEqual(state.session_status, "completed")
            self.assertFalse(window._session_has_live_activity(state.session_id))
            self.assertEqual(bubble.main_content_text, "正常完成。")
            self.assertNotIn("异常中断", bubble.main_content_text)
            self.assertFalse(
                any(
                    (message.get("meta") or {}).get("context_visible_interruption")
                    for message in state.messages
                    if isinstance(message, dict)
                )
            )
        finally:
            window.close()
            window.deleteLater()

    def test_terminal_runtime_result_recovers_once_after_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow()
            try:
                state = window.get_current_session()
                state.messages = [{
                    "id": "u-runtime",
                    "role": "user",
                    "content": "恢复结果",
                    "meta": {"turn_id": "1", "request_id": "request-runtime"},
                }]
                state.history_loaded = False
                window.runtime_journal = RuntimeJournal(root)
                window.runtime_journal.begin_run(
                    state.session_id,
                    "request-runtime",
                    turn_id="1",
                    writer_owner="ui:test",
                    base_messages=state.messages,
                )
                final_result = {
                    "request_id": "request-runtime",
                    "role": "assistant",
                    "content": "恢复后的完整回答。",
                    "generated_messages": [{
                        "id": "a-runtime",
                        "role": "assistant",
                        "content": "恢复后的完整回答。",
                        "meta": {"turn_id": "1", "request_id": "request-runtime"},
                    }],
                }
                window.runtime_journal.update_run(
                    state.session_id,
                    "request-runtime",
                    {"status": "finalizing", "final_result": final_result},
                )
                window.runtime_journal.update_run(
                    state.session_id,
                    "request-runtime",
                    {"status": "completed", "final_result": final_result},
                )

                self.assertTrue(window._restore_terminal_runtime_run_if_needed(state))
                self.assertEqual(state.session_status, "completed")
                self.assertEqual(
                    [message["content"] for message in state.messages if message.get("role") == "assistant"],
                    ["恢复后的完整回答。"],
                )
                self.assertFalse(window._restore_terminal_runtime_run_if_needed(state))
            finally:
                window.close()
                window.deleteLater()

    def test_failed_terminal_runtime_recovers_partial_content_without_error_message(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow()
            try:
                state = window.get_current_session()
                state.messages = [{
                    "id": "u-runtime-failed",
                    "role": "user",
                    "content": "恢复失败轮次",
                    "meta": {"turn_id": "1", "request_id": "request-runtime-failed"},
                }]
                state.history_loaded = False
                window.runtime_journal = RuntimeJournal(root)
                window.runtime_journal.begin_run(
                    state.session_id,
                    "request-runtime-failed",
                    turn_id="1",
                    writer_owner="ui:test",
                    base_messages=state.messages,
                )
                final_result = {
                    "request_id": "request-runtime-failed",
                    "error": "provider failed",
                    "generated_messages": [],
                }
                window.runtime_journal.update_run(
                    state.session_id,
                    "request-runtime-failed",
                    {
                        "status": "failed",
                        "draft_content": "已经生成的部分内容。",
                        "final_result": final_result,
                    },
                )

                self.assertTrue(window._restore_terminal_runtime_run_if_needed(state))
                assistants = [
                    message for message in state.messages
                    if message.get("role") == "assistant"
                ]
                self.assertEqual([message["content"] for message in assistants], ["已经生成的部分内容。"])
                self.assertFalse(
                    any("以上内容可能不完整" in message["content"] for message in assistants)
                )
                self.assertFalse(
                    any((message.get("meta") or {}).get("context_visible_interruption") for message in assistants)
                )
                self.assertEqual(state.conversation_notice.label.text(), "本轮执行失败")
            finally:
                window.close()
                window.deleteLater()

    def test_render_failure_after_provider_success_keeps_completed_terminal(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.active_turn_id = 1
            state.active_turn_request_id = "request-render-failure"
            window.set_session_status("running", state.session_id)
            with (
                patch.object(
                    window,
                    "_handle_llm_response_impl",
                    side_effect=RuntimeError("visualization render failed"),
                ),
                patch.object(window, "_show_conversation_notice") as notice,
            ):
                window.handle_llm_response(
                    {
                        "request_id": "request-render-failure",
                        "role": "assistant",
                        "content": "模型已经正常完成。",
                    },
                    state.session_id,
                    turn_id=1,
                )

            self.assertEqual(state.session_status, "completed")
            self.assertFalse(window._session_has_live_activity(state.session_id))
            notice.assert_called()
            self.assertIn("界面收尾失败", notice.call_args.args[1])
        finally:
            window.close()
            window.deleteLater()

    def test_agent_bubble_has_no_sub_agent_pip_or_raw_log_widgets(self):
        bubble = ChatBubble("Agent", "")
        self.assertFalse(hasattr(bubble, "sub_agent_indicators"))
        self.assertFalse(hasattr(bubble, "sub_agent_logs"))
        self.assertFalse(hasattr(bubble, "update_sub_agent_log"))
        bubble.deleteLater()

    def test_summoned_agent_process_blocks_scope_same_tool_id_per_agent(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addStretch()
        state = SimpleNamespace(
            session_id="session-1",
            chat_layout=layout,
            summoned_agent_projections={},
            workspace_dir="",
        )
        window = MainWindow.__new__(MainWindow)
        window.dynamic_message_width = 760
        window.dynamic_user_bubble_width = 760
        window.chat_storage = None
        window.config_manager = SimpleNamespace(
            is_skill_enabled=lambda *_args, **_kwargs: False,
        )
        window._workspace_dir_for_state = lambda _state: ""
        window._connect_chat_bubble_actions = MagicMock()
        window.request_session_scroll_to_bottom = MagicMock()
        window.show_tool_details = MagicMock()

        first = MainWindow._create_summoned_agent_process(
            window,
            state,
            {"agent_id": "agent-1", "agent_profile_name": "分析一"},
        )
        second = MainWindow._create_summoned_agent_process(
            window,
            state,
            {"agent_id": "agent-2", "agent_profile_name": "分析二"},
        )
        for agent_id in ("agent-1", "agent-2"):
            MainWindow._project_summoned_agent_event(
                window,
                state,
                {
                    "agent_id": agent_id,
                    "status": "thinking",
                    "reasoning_delta": f"{agent_id} 正在分析",
                },
            )
            MainWindow._project_summoned_agent_event(
                window,
                state,
                {
                    "agent_id": agent_id,
                    "status": "tool_use",
                    "tool_call_id": "shared-tool-id",
                    "tool_name": "workspace_list_files",
                    "tool_args": {"path": "."},
                },
            )
            MainWindow._project_summoned_agent_event(
                window,
                state,
                {
                    "agent_id": agent_id,
                    "status": "tool_result",
                    "tool_call_id": "shared-tool-id",
                    "tool_result": f"{agent_id} ok",
                },
            )

        self.assertIsInstance(first["block"], SummonedAgentProcessBlock)
        self.assertIsNot(first["tool_cards"]["shared-tool-id"], second["tool_cards"]["shared-tool-id"])
        self.assertEqual(first["tool_cards"]["shared-tool-id"].tool_id, "agent-1:shared-tool-id")
        self.assertEqual(second["tool_cards"]["shared-tool-id"].tool_id, "agent-2:shared-tool-id")
        self.assertEqual(first["tool_cards"]["shared-tool-id"].result, "agent-1 ok")
        self.assertEqual(second["tool_cards"]["shared-tool-id"].result, "agent-2 ok")
        host.deleteLater()

    def test_sub_agent_history_events_restore_reasoning_before_tools_and_final(self):
        events = build_sub_agent_history_events(
            {"id": "agent-1", "name": "审查助手", "status": "completed", "updated_at": 10},
            [
                {"role": "user", "content": "检查方案", "created_at": 1},
                {
                    "role": "assistant",
                    "reasoning": "先检查结构",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "function": {"name": "text_file_read", "arguments": {"path": "a.md"}},
                        }
                    ],
                    "created_at": 2,
                },
                {"role": "tool", "tool_call_id": "tool-1", "content": "内容", "created_at": 3},
                {"role": "assistant", "reasoning": "整理结论", "content": "检查完成", "created_at": 4},
            ],
        )

        self.assertEqual(
            [event["status"] for event in events],
            ["input", "thinking", "tool_use", "tool_result", "thinking", "completed"],
        )
        self.assertEqual(events[1]["reasoning_delta"], "先检查结构")
        self.assertEqual(events[-1]["output_text"], "检查完成")

    def test_summoned_agent_history_restore_preserves_declared_order(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addStretch()
        state = SimpleNamespace(
            session_id="session-history",
            chat_layout=layout,
            summoned_agent_projections={},
            summoned_agent_pending_events={},
            workspace_dir="",
        )
        rows = {
            "agent-1": {"id": "agent-1", "name": "实例一", "status": "completed", "updated_at": 2},
            "agent-2": {"id": "agent-2", "name": "实例二", "status": "failed", "last_error": "失败原因", "updated_at": 3},
        }
        messages = {
            "agent-1": [
                {"role": "user", "content": "任务一", "created_at": 1},
                {"role": "assistant", "reasoning": "分析一", "content": "结果一", "created_at": 2},
            ],
            "agent-2": [],
        }
        window = MainWindow.__new__(MainWindow)
        window.dynamic_message_width = 760
        window.dynamic_user_bubble_width = 760
        window.chat_storage = SimpleNamespace(
            get_agent=lambda agent_id: rows[agent_id],
            get_agent_messages=lambda agent_id: messages[agent_id],
        )
        window.config_manager = SimpleNamespace(is_skill_enabled=lambda *_args, **_kwargs: False)
        window._workspace_dir_for_state = lambda _state: ""
        window._connect_chat_bubble_actions = MagicMock()
        window.request_session_scroll_to_bottom = MagicMock()
        window.show_tool_details = MagicMock()
        message = {
            "role": "user",
            "meta": {
                "summoned_agents": [
                    {"agent_id": "agent-1", "agent_profile_name": "分析一"},
                    {"agent_id": "agent-2", "agent_profile_name": "分析二"},
                ]
            },
        }

        inserted = MainWindow._render_summoned_agent_processes(window, state, message, insert_index=0)

        self.assertEqual(inserted, 2)
        self.assertEqual(
            [layout.itemAt(index).widget().agent_id for index in range(2)],
            ["agent-1", "agent-2"],
        )
        self.assertEqual(state.summoned_agent_projections["agent-1"]["block"].status, "completed")
        self.assertEqual(state.summoned_agent_projections["agent-2"]["block"].status, "failed")
        host.deleteLater()

    def test_summoned_agent_result_stays_in_context_without_duplicate_bubble(self):
        state = SimpleNamespace(
            session_id="session-1",
            messages=[],
            completed_agent_result_ids=set(),
            displayed_count=0,
            displayed_render_count=0,
            render_items=[],
        )
        window = MainWindow.__new__(MainWindow)
        window.chat_storage = SimpleNamespace(
            get_agent=lambda _agent_id: {"parent_message_id": "user-1"},
            normalize_messages=lambda messages, **kwargs: messages,
        )
        window._new_message_id = lambda: "result-1"
        window._rebuild_session_render_spans = lambda current: setattr(current, "render_items", [])
        window.add_chat_bubble = MagicMock()

        MainWindow._append_summoned_agent_result(
            window,
            state,
            {
                "agent_id": "agent-1",
                "agent_profile_name": "审查助手",
                "summon_source": "mention",
                "status": "completed",
                "content": "检查完成",
            },
        )

        self.assertEqual(len(state.messages), 1)
        self.assertEqual(state.messages[0]["content"], "[审查助手] 检查完成")
        self.assertTrue(state.messages[0]["meta"]["embedded_agent_result"])
        self.assertEqual(state.messages[0]["meta"]["agent_parent_message_id"], "user-1")
        window.add_chat_bubble.assert_not_called()

    def test_legacy_skill_change_messages_are_hidden_and_new_events_use_toasts(self):
        enabled = {
            "role": "assistant",
            "content": "◈ 已启用能力：visualize，可被 AI 发现",
            "meta": {
                "ui_only": True,
                "skill_change": {"source": "ui", "action": "enabled"},
            },
        }
        created = {
            "role": "assistant",
            "content": "◈ AI 已创建能力：demo，现已可用",
            "meta": {
                "ui_only": True,
                "skill_change": {"source": "ai", "action": "created"},
            },
        }
        event_id_only = {
            "role": "assistant",
            "content": "◈ 能力已更新：demo",
            "meta": {
                "ui_only": True,
                "skill_change_event_id": "legacy-event",
            },
        }
        self.assertTrue(is_legacy_skill_change_notice_message(enabled))
        self.assertTrue(is_legacy_skill_change_notice_message(created))
        self.assertTrue(is_legacy_skill_change_notice_message(event_id_only))
        self.assertFalse(
            is_legacy_skill_change_notice_message(
                {"role": "assistant", "content": "普通回复", "meta": {"ui_only": True}}
            )
        )

        class WindowStub:
            def __init__(self):
                self._skill_change_toast_event_ids = OrderedDict()
                self.toasts = []

            def add_system_toast(self, text, tone):
                self.toasts.append((text, tone))

        window = WindowStub()
        cases = [
            ("created", "能力已创建：demo", "success"),
            ("updated", "能力已更新：demo", "success"),
            ("enabled", "已启用能力：demo，可被 AI 发现", "success"),
            ("disabled", "已关闭能力：demo", "info"),
            ("deleted", "已删除能力：demo", "info"),
            ("dependency_changed", "能力依赖已就绪：demo", "success"),
        ]
        for index, (action, expected_text, expected_tone) in enumerate(cases):
            event = {
                "event_id": f"event-{index}",
                "source": ("ai", "ui", "filesystem")[index % 3],
                "action": action,
                "skill_names": ["demo"],
            }
            self.assertTrue(MainWindow._show_skill_change_system_notice(window, event))
            self.assertEqual(window.toasts[-1], (expected_text, expected_tone))

        self.assertFalse(
            MainWindow._show_skill_change_system_notice(
                window,
                {
                    "event_id": "event-0",
                    "source": "ai",
                    "action": "created",
                    "skill_names": ["demo"],
                },
            )
        )
        self.assertEqual(len(window.toasts), len(cases))

        original_messages = [enabled, created]
        render_stub = SimpleNamespace(get_session=lambda _session_id: SimpleNamespace())
        self.assertEqual(
            MainWindow.render_message_batch(
                render_stub,
                original_messages,
                "session-1",
                animate=False,
            ),
            0,
        )
        self.assertEqual(original_messages, [enabled, created])

    def test_skill_change_notice_event_cache_is_bounded(self):
        window = SimpleNamespace(
            _skill_change_toast_event_ids=OrderedDict(),
            add_system_toast=lambda *_args: None,
        )
        for index in range(2050):
            self.assertTrue(
                MainWindow._show_skill_change_system_notice(
                    window,
                    {
                        "event_id": f"event-{index}",
                        "source": "ui",
                        "action": "updated",
                        "skill_names": ["demo"],
                    },
                )
            )
        self.assertEqual(len(window._skill_change_toast_event_ids), 2048)
        self.assertNotIn("event-0", window._skill_change_toast_event_ids)
        self.assertIn("event-2049", window._skill_change_toast_event_ids)

    def test_local_catalog_handler_only_notifies_filesystem_changes(self):
        notices = []
        page = SimpleNamespace(skill_manager=None, refresh_list=lambda: None)
        window = SimpleNamespace(
            PAGE_CAPABILITIES="capabilities",
            product_pages={"capabilities": page},
            _show_skill_change_system_notice=notices.append,
        )
        snapshot = SimpleNamespace(manager=object())
        filesystem_event = {
            "event_id": "filesystem-1",
            "source": "filesystem",
            "action": "updated",
            "skill_names": ["demo"],
        }
        ui_event = {
            "event_id": "ui-1",
            "source": "ui",
            "action": "updated",
            "skill_names": ["demo"],
        }

        MainWindow._handle_local_skill_catalog_changed(window, ui_event, snapshot)
        MainWindow._handle_local_skill_catalog_changed(window, filesystem_event, snapshot)

        self.assertIs(window.skill_manager, snapshot.manager)
        self.assertTrue(window.skill_manager_ready)
        self.assertEqual(notices, [filesystem_event])

    def test_deep_thinking_fold_hides_reasoning_and_tools_together(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        tool = ToolCallCard("run_command", {"command": "pytest"}, "tool-1")
        bubble.add_tool_card(tool)
        self.assertFalse(bubble.think_toggle_btn.isChecked())
        self.assertTrue(bubble.think_container.isHidden())
        bubble.think_toggle_btn.setChecked(True)
        self.assertFalse(bubble.think_container.isHidden())
        self.assertIn(tool, bubble.timeline_events)
        bubble.think_toggle_btn.setChecked(False)
        self.assertTrue(bubble.think_container.isHidden())
        bubble.deleteLater()

    def test_apply_patch_card_uses_multi_path_summary_and_error_state(self):
        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: src/old.py\n"
            "*** Move to: src/new.py\n"
            "@@\n-old\n+new\n"
            "*** Add File: docs/readme.md\n+text\n"
            "*** End Patch"
        )
        args = {"patch": patch_text}

        title, summary = summarize_tool_action("apply_patch", args)
        self.assertEqual(title, "应用文本补丁")
        self.assertEqual(summary, "处理 3 个文本路径")
        self.assertEqual(
            extract_related_paths("apply_patch", args),
            ["src/old.py", "src/new.py", "docs/readme.md"],
        )

        card = ToolCallCard("apply_patch", args, "patch-tool")
        card.show()
        self.app.processEvents()
        card.set_result(
            '{"ok": false}',
            {"ok": False, "error": {"code": "ambiguous_hunk", "message": "context is ambiguous"}},
        )
        self.app.processEvents()

        self.assertTrue(card.failed)
        self.assertFalse(card.status_icon.pixmap().isNull())
        self.assertFalse(card.grab().isNull())
        card.deleteLater()

    def test_empty_thinking_expansion_does_not_create_timeline_height(self):
        bubble = ChatBubble("Agent", "", thinking="...")
        bubble.think_toggle_btn.setChecked(True)
        self.app.processEvents()
        self.assertTrue(bubble.think_container.isHidden())

        bubble.update_thinking("正在检查")
        self.app.processEvents()
        self.assertFalse(bubble.think_container.isHidden())

        expanded_height = bubble.sizeHint().height()
        bubble.think_toggle_btn.setChecked(False)
        self.app.processEvents()
        self.assertTrue(bubble.think_container.isHidden())
        self.assertLess(bubble.sizeHint().height(), expanded_height)
        bubble.deleteLater()

    def test_turn_group_keeps_multiple_compact_thinking_stages_and_separators(self):
        group = AssistantTurnGroup("compact-turn")
        for index in range(5):
            bubble = ChatBubble("Agent", "")
            group.add_stage(bubble)
            bubble.update_thinking(f"阶段 {index + 1}", duration=index + 1, is_final=True)

        self.assertEqual(len(group.stage_bubbles), 5)
        self.assertEqual(len(group.stage_separators), 4)
        self.assertTrue(all(not bubble.isHidden() for bubble in group.stage_bubbles))
        self.assertTrue(all(not separator.isHidden() for separator in group.stage_separators))
        margins = group.stage_separators[0].layout().contentsMargins()
        self.assertEqual(margins.left(), DesignTokens.assistant_stage_separator_indent)
        self.assertEqual(margins.top(), DesignTokens.assistant_stage_separator_vertical_margin)
        self.assertEqual(margins.bottom(), DesignTokens.assistant_stage_separator_vertical_margin)
        group.deleteLater()

    def test_first_turn_keeps_surplus_height_below_content(self):
        host = QWidget()
        host.resize(1200, 900)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        user = ChatBubble("User", "测试下这个技能")
        group = AssistantTurnGroup("first-turn")
        agent = ChatBubble("Agent", "我会生成一个小型交互图表来验证完整链路。", thinking="正在分析")
        group.add_stage(agent)
        layout.addWidget(user)
        layout.addWidget(group)
        layout.addStretch()

        host.show()
        self.app.processEvents()

        self.assertEqual(user.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        self.assertEqual(group.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        self.assertEqual(agent.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        self.assertLessEqual(group.height(), group.sizeHint().height() + 2)
        self.assertLessEqual(agent.height(), agent.sizeHint().height() + 2)
        self.assertLessEqual(agent.thinking_widget.height(), agent.thinking_widget.sizeHint().height() + 2)
        self.assertLess(agent.think_toggle_btn.y(), 8)
        stretch_geometry = layout.itemAt(2).geometry()
        self.assertGreater(stretch_geometry.height(), 500)
        self.assertGreaterEqual(stretch_geometry.y(), group.geometry().bottom())
        host.deleteLater()

    def test_live_stage_growth_stays_compact_and_emits_geometry_updates(self):
        host = QWidget()
        host.resize(1200, 900)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        group = AssistantTurnGroup("streaming-turn")
        agent = ChatBubble("Agent", "", thinking="正在分析")
        group.add_stage(agent)
        layout.addWidget(group)
        layout.addStretch()
        geometry_updates = []
        agent.geometryChanged.connect(lambda: geometry_updates.append(agent.sizeHint().height()))

        host.show()
        agent.think_toggle_btn.setChecked(True)
        first_tool = ToolCallCard("run_command", {"command": "pytest"}, "tool-1")
        second_tool = ToolCallCard("search_codebase", {"query": "layout"}, "tool-2")
        agent.add_tool_card(first_tool)
        agent.add_tool_card(second_tool)
        agent.set_main_content("阶段回复", final=False)
        self.app.processEvents()

        first_tool.setFocus()
        self.app.processEvents()
        initial_top = group.y()
        first_tool_height = first_tool.height()
        first_row_height = first_tool.main_row.height()
        first_tool.update_agent_state({
            "agent_id": "layout-agent",
            "agent_name": "布局检查",
            "status": "running",
            "task": "检查工具卡动态内容高度",
        })
        self.app.processEvents()
        first_tool_with_agent_height = first_tool.height()
        first_tool.set_result("ok")
        second_tool.set_result("ok")
        agent.set_main_content("任务完成。", final=True)
        self.app.processEvents()

        self.assertEqual(group.y(), initial_top)
        self.assertGreater(len(geometry_updates), 0)
        self.assertEqual(first_tool.sizePolicy().verticalPolicy(), QSizePolicy.Maximum)
        self.assertEqual(first_tool.main_row.sizePolicy().verticalPolicy(), QSizePolicy.Fixed)
        self.assertLessEqual(first_tool_height, first_tool.sizeHint().height() + 2)
        self.assertLessEqual(first_row_height, first_tool.main_row.sizeHint().height() + 2)
        self.assertGreater(first_tool_with_agent_height, first_tool_height)
        self.assertLessEqual(first_tool_with_agent_height, first_tool.sizeHint().height() + 2)
        self.assertLessEqual(group.height(), group.sizeHint().height() + 2)
        self.assertLess(agent.content_edit.y(), agent.height())
        host.deleteLater()

    def test_turn_group_hides_empty_stage_and_its_separator(self):
        group = AssistantTurnGroup("empty-stage-turn")
        first = ChatBubble("Agent", "", thinking="先分析")
        empty = ChatBubble("Agent", "")
        group.add_stage(first)
        group.add_stage(empty)

        self.assertFalse(first.isHidden())
        self.assertTrue(empty.isHidden())
        self.assertTrue(group.stage_separators[0].isHidden())

        empty.set_main_content("阶段回复", final=True)
        self.app.processEvents()
        self.assertFalse(empty.isHidden())
        self.assertFalse(group.stage_separators[0].isHidden())
        group.deleteLater()

    def test_stage_without_reply_does_not_reserve_text_editor_height(self):
        bubble = ChatBubble("Agent", "", thinking="只显示思考标题")
        self.assertTrue(bubble.content_rich_edit.isHidden())
        self.assertTrue(bubble.content_plain_edit.isHidden())

        bubble.set_main_content("阶段回复", final=True)
        self.app.processEvents()
        self.assertFalse(bubble.content_edit.isHidden())

        bubble.set_main_content("", final=True)
        self.app.processEvents()
        self.assertTrue(bubble.content_edit.isHidden())
        bubble.deleteLater()

    def test_stage_reply_remains_visible_when_thinking_segment_finishes(self):
        bubble = ChatBubble("Agent", "")
        bubble.set_main_content("阶段性文字", final=True)
        self.assertEqual(bubble.freeze_content_fragment(), "阶段性文字")
        self.assertEqual(bubble.main_content_text, "阶段性文字")
        self.assertIn("阶段性文字", bubble.content_edit.toPlainText())
        bubble.deleteLater()

    def test_guidance_status_updates_outside_thinking_segment(self):
        class TimelineStub:
            _timeline_find_event = MainWindow._timeline_find_event
            _timeline_set_guidance_status = MainWindow._timeline_set_guidance_status

        checkpoint = GuidanceTimelineEvent("guide-3", "先跑单测", status="waiting_tool")
        state = SimpleNamespace(
            ui_timeline_events=[{
                "kind": "guidance", "message_id": "guide-3", "status": "waiting_tool", "finished_at": None,
            }],
            guidance_widgets={"guide-3": checkpoint},
        )
        self.assertTrue(TimelineStub()._timeline_set_guidance_status(state, "guide-3", "applied"))
        self.assertEqual(checkpoint.status, "applied")
        checkpoint.deleteLater()

    def test_pending_guidance_card_edits_inline_and_terminal_status_hides_actions(self):
        checkpoint = GuidanceTimelineEvent(
            "guide-editable",
            "先检查测试",
            status="queued",
            mutation_ready=True,
        )
        try:
            self.assertFalse(checkpoint.edit_btn.isHidden())
            self.assertFalse(checkpoint.delete_btn.isHidden())

            checkpoint.begin_inline_edit()
            self.assertFalse(checkpoint.content_editor.isHidden())
            self.assertTrue(checkpoint.edit_btn.isHidden())
            checkpoint.content_editor.setPlainText("先检查缓存测试")

            checkpoint.set_status("applied")
            self.assertTrue(checkpoint.content_editor.isHidden())
            self.assertTrue(checkpoint.edit_btn.isHidden())
            self.assertTrue(checkpoint.delete_btn.isHidden())
            self.assertEqual(checkpoint.content_label.text(), "先检查测试")
        finally:
            checkpoint.deleteLater()

    def test_local_pending_guidance_edit_updates_worker_state_timeline_and_card(self):
        class _Worker:
            def __init__(self):
                self.replacement = None

            def isRunning(self):
                return True

            def update_guidance(self, message_id, message, expected_turn_id=None):
                self.replacement = message
                return {
                    "updated": message_id == "guide-edit" and expected_turn_id == 1,
                    "message_id": message_id,
                    "turn_id": str(expected_turn_id),
                }

        window = MainWindow()
        try:
            state = window.get_current_session()
            worker = _Worker()
            state.llm_worker = worker
            state.active_turn_id = 1
            message = {
                "id": "guide-edit",
                "role": "user",
                "content": "原引导",
                "meta": {
                    "display_content": "原引导",
                    "same_turn_guidance": True,
                    "turn_id": "1",
                },
            }
            state.pending_guidance_messages = [message]
            state.messages = []
            state.ui_timeline_events = [{
                "kind": "guidance",
                "message_id": "guide-edit",
                "status": "queued",
                "finished_at": None,
                "text": "原引导",
            }]
            window.add_turn_guidance_inline(
                message,
                display_content="原引导",
                status="queued",
                session_id=state.session_id,
                mutation_ready=True,
            )
            card = state.guidance_widgets["guide-edit"]
            card.begin_inline_edit()
            card.content_editor.setPlainText("新引导")
            card.submit_inline_edit()

            self.assertEqual(worker.replacement["content"], "新引导")
            self.assertEqual(worker.replacement["meta"]["turn_id"], "1")
            self.assertEqual(state.pending_guidance_messages[0]["content"], "新引导")
            self.assertEqual(state.messages, [])
            self.assertEqual(state.ui_timeline_events[0]["text"], "新引导")
            self.assertEqual(card.content_label.text(), "新引导")
            self.assertFalse(card.edit_btn.isHidden())
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()

    def test_pending_guidance_delete_restores_text_and_attachments_to_composer(self):
        class _Worker:
            def isRunning(self):
                return True

            def delete_guidance(self, message_id, expected_turn_id=None):
                return {
                    "deleted": message_id == "guide-delete" and expected_turn_id == 2,
                    "message_id": message_id,
                    "turn_id": str(expected_turn_id),
                }

        attachment = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        attachment.close()
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.llm_worker = _Worker()
            state.active_turn_id = 2
            window.input_field.setPlainText("已有草稿")
            state.composer_draft = "已有草稿"
            message = {
                "id": "guide-delete",
                "role": "user",
                "content": "撤回这条",
                "content_parts": [
                    {"type": "text", "text": "撤回这条"},
                    {"type": "input_file", "path": attachment.name},
                ],
                "meta": {
                    "display_content": "撤回这条",
                    "user_added_files": [attachment.name],
                    "same_turn_guidance": True,
                    "turn_id": "2",
                },
            }
            state.pending_guidance_messages = [message]
            state.ui_timeline_events = [{
                "kind": "guidance",
                "message_id": "guide-delete",
                "status": "waiting_tool",
                "finished_at": None,
                "text": "撤回这条",
            }]
            window.add_turn_guidance_inline(
                message,
                display_content="撤回这条",
                attachments=window._message_user_attachments(message),
                status="waiting_tool",
                session_id=state.session_id,
                mutation_ready=True,
            )

            self.assertTrue(window.delete_pending_guidance(state.session_id, "guide-delete"))
            self.assertEqual(window.input_field.toPlainText(), "撤回这条\n\n已有草稿")
            self.assertEqual(state.composer_draft, "撤回这条\n\n已有草稿")
            self.assertIn(os.path.normpath(attachment.name), state.prompt_files)
            self.assertEqual(state.pending_guidance_messages, [])
            self.assertEqual(state.ui_timeline_events, [])
            self.assertNotIn("guide-delete", state.guidance_widgets)
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()
            os.unlink(attachment.name)

    def test_pending_guidance_mutation_never_rewrites_ledger_message(self):
        class _Worker:
            def __init__(self):
                self.calls = []

            def isRunning(self):
                return True

            def update_guidance(self, *_args, **_kwargs):
                self.calls.append("update")
                return {"updated": True, "turn_id": "9"}

            def delete_guidance(self, *_args, **_kwargs):
                self.calls.append("delete")
                return {"deleted": True, "turn_id": "9"}

        window = MainWindow()
        try:
            state = window.get_current_session()
            worker = _Worker()
            state.llm_worker = worker
            state.active_turn_id = 9
            pending = {
                "id": "guide-ledger",
                "role": "user",
                "content": "待应用内容",
                "meta": {
                    "display_content": "待应用内容",
                    "same_turn_guidance": True,
                    "turn_id": "9",
                },
            }
            ledger_message = {
                **pending,
                "content": "已入账内容",
                "meta": {**pending["meta"], "sequence": 7, "request_id": "request-1"},
            }
            state.pending_guidance_messages = [pending]
            state.messages = [ledger_message]
            state.ui_timeline_events = [{
                "kind": "guidance",
                "message_id": "guide-ledger",
                "status": "queued",
                "finished_at": None,
                "text": "待应用内容",
            }]
            window.add_turn_guidance_inline(
                pending,
                display_content="待应用内容",
                status="queued",
                session_id=state.session_id,
                mutation_ready=True,
            )

            with patch.object(window, "add_system_toast"):
                self.assertFalse(
                    window.edit_pending_guidance_inline(
                        state.session_id,
                        "guide-ledger",
                        "不能改写账本",
                    )
                )
                self.assertFalse(window.delete_pending_guidance(state.session_id, "guide-ledger"))

            self.assertEqual(worker.calls, [])
            self.assertIs(state.messages[0], ledger_message)
            self.assertEqual(state.messages[0]["content"], "已入账内容")
            self.assertEqual(state.messages[0]["meta"]["sequence"], 7)
            self.assertEqual(state.messages[0]["meta"]["request_id"], "request-1")
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()

    def test_guidance_restore_targets_background_session_draft(self):
        window = MainWindow()
        try:
            foreground = window.get_current_session()
            window.input_field.setPlainText("前台草稿")
            foreground.composer_draft = "前台草稿"
            background_id = window.create_new_session(make_current=False)
            background = window.get_session(background_id)
            background.composer_draft = "后台原草稿"
            with patch.object(window, "add_system_toast"):
                self.assertTrue(
                    window._restore_guidance_to_composer(
                        background,
                        "撤回内容",
                        [],
                        toast_text="已恢复",
                        tone="success",
                    )
                )

            self.assertEqual(window.input_field.toPlainText(), "前台草稿")
            self.assertEqual(background.composer_draft, "撤回内容\n\n后台原草稿")
        finally:
            window.close()
            window.deleteLater()

    def test_pending_guidance_edit_failure_keeps_unsaved_text_actionable(self):
        class _Worker:
            def isRunning(self):
                return True

            def update_guidance(self, *_args, **_kwargs):
                return {"updated": False, "error": "daemon_unavailable", "turn_id": "3"}

        window = MainWindow()
        try:
            state = window.get_current_session()
            state.llm_worker = _Worker()
            state.active_turn_id = 3
            message = {
                "id": "guide-failed-edit",
                "role": "user",
                "content": "原内容",
                "meta": {"display_content": "原内容", "same_turn_guidance": True, "turn_id": "3"},
            }
            state.pending_guidance_messages = [message]
            state.ui_timeline_events = [{
                "kind": "guidance",
                "message_id": "guide-failed-edit",
                "status": "queued",
                "finished_at": None,
                "text": "原内容",
            }]
            window.add_turn_guidance_inline(
                message,
                display_content="原内容",
                status="queued",
                session_id=state.session_id,
                mutation_ready=True,
            )
            card = state.guidance_widgets["guide-failed-edit"]
            card.begin_inline_edit()
            card.content_editor.setPlainText("尚未保存的新内容")
            card.submit_inline_edit()

            self.assertTrue(card.editing)
            self.assertFalse(card.content_editor.isHidden())
            self.assertTrue(card.content_editor.isEnabled())
            self.assertEqual(card.content_editor.toPlainText(), "尚未保存的新内容")
            self.assertEqual(state.pending_guidance_messages[0]["content"], "原内容")
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()

    def test_safe_point_race_marks_guidance_applied_without_restoring_composer(self):
        class _Worker:
            def isRunning(self):
                return True

            def delete_guidance(self, *_args, **_kwargs):
                return {"deleted": False, "error": "guidance_not_pending", "turn_id": "4"}

        window = MainWindow()
        try:
            state = window.get_current_session()
            state.llm_worker = _Worker()
            state.active_turn_id = 4
            window.input_field.setPlainText("保留草稿")
            message = {
                "id": "guide-race",
                "role": "user",
                "content": "来不及撤回",
                "meta": {"display_content": "来不及撤回", "same_turn_guidance": True, "turn_id": "4"},
            }
            state.pending_guidance_messages = [message]
            state.ui_timeline_events = [{
                "kind": "guidance",
                "message_id": "guide-race",
                "status": "queued",
                "finished_at": None,
                "text": "来不及撤回",
            }]
            window.add_turn_guidance_inline(
                message,
                display_content="来不及撤回",
                status="queued",
                session_id=state.session_id,
                mutation_ready=True,
            )

            self.assertFalse(window.delete_pending_guidance(state.session_id, "guide-race"))
            card = state.guidance_widgets["guide-race"]
            self.assertEqual(card.status, "applied")
            self.assertTrue(card.edit_btn.isHidden())
            self.assertTrue(card.delete_btn.isHidden())
            self.assertEqual(window.input_field.toPlainText(), "保留草稿")
        finally:
            state.llm_worker = None
            window.close()
            window.deleteLater()

    def test_live_guidance_splits_thinking_into_conversational_segments(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_guidance")
            state.live_activity = True
            state.active_turn_id = 1
            first = window._append_live_thinking_segment(state)
            first_group = state.active_agent_turn_group
            first.update_thinking("先分析")
            first.set_main_content("阶段回复", final=False)
            state.current_content_buffer = "阶段回复"
            state.current_thinking_buffer = "先分析"

            message = {
                "id": "guide-live",
                "role": "user",
                "content": "调整方向",
                "meta": {"same_turn_guidance": True, "turn_id": "1"},
            }
            window._render_turn_guidance_checkpoint(state, message, "调整方向", [])
            guidance = state.guidance_widgets["guide-live"]
            self.assertTrue(guidance.edit_btn.isHidden())
            window._accept_turn_guidance(state, message, "调整方向", [])
            self.assertFalse(guidance.edit_btn.isHidden())
            continuation = state.temp_thinking_bubble
            guidance_wrapper = guidance.parentWidget()

            self.assertEqual(first.main_content_text, "阶段回复")
            self.assertIsNot(first, continuation)
            self.assertLess(state.chat_layout.indexOf(first_group), state.chat_layout.indexOf(guidance_wrapper))
            continuation_group = continuation.parentWidget()
            self.assertIsInstance(continuation_group, AssistantTurnGroup)
            self.assertLess(state.chat_layout.indexOf(guidance_wrapper), state.chat_layout.indexOf(continuation_group))
            self.assertIn("深度思考", first.think_toggle_btn.text())
            self.assertNotIn("深度思考中", first.think_toggle_btn.text())
            self.assertIn("深度思考", continuation.think_toggle_btn.text())
            self.assertTrue(first_group.process_finalized)
            self.assertFalse(first_group.process_disclosure.isChecked())
            self.assertFalse(first.think_timer.isActive())
            self.assertEqual(continuation.session_id, state.session_id)
            self.assertIs(continuation.chat_storage, window.chat_storage)
            self.assertFalse(first.copy_result_btn.isVisible())
            self.assertFalse(first.office_draft_btn.isVisible())
        finally:
            window.close()
            window.deleteLater()

    def test_guidance_boundary_routes_reentrant_thinking_to_new_group(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_guidance_reentrant")
            state.live_activity = True
            state.active_turn_id = 1
            first = window._append_live_thinking_segment(state)
            first_group = state.active_agent_turn_group
            first.update_thinking("旧组分析")
            first.set_main_content("旧组阶段回复", final=False)
            state.current_content_buffer = "旧组阶段回复"
            state.current_thinking_buffer = "旧组分析"

            message = {
                "id": "guide-reentrant",
                "role": "user",
                "content": "切换方向",
                "meta": {"same_turn_guidance": True, "turn_id": "1"},
            }
            reentrant_timer = QTimer(window)
            reentrant_timer.setSingleShot(True)
            reentrant_timer.timeout.connect(
                lambda: window.handle_thinking_signal(
                    "新组分析",
                    state.session_id,
                    turn_id=1,
                )
            )
            reentrant_timer.start(0)

            window._render_turn_guidance_checkpoint(state, message, "切换方向", [])
            self.assertTrue(reentrant_timer.isActive())
            reentrant_timer.timeout.emit()
            reentrant_timer.stop()
            continuation_group = state.active_agent_turn_group

            self.assertIsNot(first_group, continuation_group)
            self.assertEqual(len(first_group.stage_bubbles), 1)
            self.assertTrue(first_group.process_finalized)
            self.assertFalse(first_group.process_disclosure.isChecked())
            self.assertFalse(first.think_timer.isActive())
            self.assertNotIn("深度思考中", first.think_toggle_btn.text())
            routed_event = next(
                event
                for event in reversed(state.ui_timeline_events)
                if event.get("kind") == "thinking" and event.get("text") == "新组分析"
            )
            self.assertEqual(routed_event["group_id"], continuation_group.group_id)
            self.assertEqual(
                routed_event["stage_id"],
                state.temp_thinking_bubble.ui_stage_id,
            )

            first_group.process_disclosure.setChecked(True)
            self.assertFalse(first.isHidden())
            self.assertNotIn("深度思考中", first.think_toggle_btn.text())
        finally:
            window.close()
            window.deleteLater()

    def test_terminal_state_folds_process_only_group_and_waits_for_final_result(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_terminal_process_only")
            state.live_activity = True
            state.active_turn_id = 1

            orphan = window._append_live_thinking_segment(state)
            orphan_group = state.active_agent_turn_group
            orphan.update_thinking("边界前的孤立思考")

            state.active_agent_turn_group = None
            state.agent_stage_closed = False
            result = window._append_live_thinking_segment(state)
            result_group = state.active_agent_turn_group
            result.update_thinking("最终分析")
            result.set_main_content("正在输出", final=False)

            window.set_session_status("completed", state.session_id)

            self.assertTrue(orphan_group.process_finalized)
            self.assertFalse(orphan_group.process_disclosure.isChecked())
            self.assertFalse(orphan.think_timer.isActive())
            self.assertNotIn("深度思考中", orphan.think_toggle_btn.text())
            self.assertTrue(result_group.process_finalization_pending)
            self.assertFalse(result_group.process_finalized)

            result.update_thinking(duration=1.5, is_final=True)
            result.set_main_content("最终结果", final=True)

            self.assertFalse(result_group.process_finalization_pending)
            self.assertTrue(result_group.process_finalized)
            self.assertFalse(result_group.process_disclosure.isChecked())
            self.assertFalse(result.think_timer.isActive())
            self.assertNotIn("深度思考中", result.think_toggle_btn.text())
        finally:
            window.close()
            window.deleteLater()

    def test_rejected_daemon_guidance_does_not_mark_reply_as_stopped(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            bubble = ChatBubble("Agent", "")
            bubble.set_main_content("仍在处理", final=False)
            state.temp_thinking_bubble = bubble
            state.last_agent_bubble = bubble
            state.active_turn_id = 1
            worker = object()
            state.guidance_workers = [worker]
            message = {"id": "guide-rejected", "role": "user", "content": "调整"}
            with patch.object(window, "_restore_rejected_guidance"):
                window._handle_daemon_guidance_result(
                    {"status": "error", "accepted": False},
                    worker,
                    state.session_id,
                    1,
                    message,
                    "调整",
                    [],
                    "调整",
                    [],
                )
            self.assertEqual(bubble.main_content_text, "仍在处理")
            self.assertNotIn("任务已停止", bubble.main_content_text)
        finally:
            window.close()
            window.deleteLater()

    def test_stop_marks_partial_stage_without_message_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_stop_stage")
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            state.current_content_buffer = "部分结果"
            bubble.set_main_content("部分结果", final=False)
            window.stop_agent()
            self.assertEqual(bubble.main_content_text, "部分结果")
            self.assertEqual(state.conversation_notice.label.text(), "已停止")
            self.assertTrue(bubble.copy_result_btn.isHidden())
            self.assertTrue(bubble.office_draft_btn.isHidden())
            self.assertFalse(bubble.think_timer.isActive())
            self.assertNotIn("深度思考中", bubble.think_toggle_btn.text())
        finally:
            window.close()
            window.deleteLater()

    def test_submit_clear_input_also_clears_session_draft_before_history_refresh(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.composer_draft = "会被提交的首条问题"
            window.input_field.setPlainText("会被提交的首条问题")

            window._clear_submitted_composer(state, clear_current_input=True)
            window.set_current_session(state.session_id)

            self.assertEqual(state.composer_draft, "")
            self.assertEqual(window.input_field.toPlainText(), "")
        finally:
            window.close()
            window.deleteLater()

    def test_submit_reentrant_call_is_rejected_before_second_dispatch(self):
        window = MainWindow.__new__(MainWindow)
        state = SimpleNamespace(session_id="session-a", submit_in_progress=False)
        calls = []

        def submit_once(*_args, **_kwargs):
            calls.append("outer")
            self.assertFalse(window._submit_session_request(state, "重复提交"))
            return True

        window._submit_session_request_once = submit_once
        self.assertTrue(window._submit_session_request(state, "首条消息"))
        self.assertEqual(calls, ["outer"])
        self.assertFalse(state.submit_in_progress)

    def test_stale_run_events_do_not_mutate_active_session(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.active_turn_id = 2
            state.active_turn_request_id = "request-2"
            state.current_content_buffer = ""

            window.handle_content_signal(
                "旧请求内容",
                state.session_id,
                turn_id=1,
                request_id="request-1",
            )
            window.add_tool_card(
                {"id": "old-tool", "name": "run_command", "args": {}},
                state.session_id,
                turn_id=1,
                request_id="request-1",
            )

            self.assertEqual(state.current_content_buffer, "")
            self.assertNotIn("old-tool", state.tool_cards)
        finally:
            window.close()
            window.deleteLater()

    def test_new_stage_freezes_previous_thinking_timer(self):
        first = ChatBubble("Agent", "", thinking="...")
        second = ChatBubble("Agent", "", thinking="...")
        group = AssistantTurnGroup("turn-1")
        try:
            group.add_stage(first)
            self.assertTrue(first.think_timer.isActive())
            group.add_stage(second)
            self.assertFalse(first.think_timer.isActive())
            self.assertTrue(second.think_timer.isActive())
        finally:
            first.deleteLater()
            second.deleteLater()
            group.deleteLater()

    def test_history_rewrite_keeps_sqlite_commit_when_runtime_sidecar_fails(self):
        window = MainWindow.__new__(MainWindow)
        state = SimpleNamespace(
            session_id="session-edit",
            messages=[{"id": "edited", "role": "user", "content": "修改后"}],
            chat_save_revision=1,
            persisted_conversation_meta={},
            session_status="draft",
        )
        window.chat_storage = MagicMock()
        window.chat_storage.rewrite_conversation_safely.return_value = {
            "revision": 2,
            "messages_hash": "new-hash",
            "previous_messages_hash": "old-hash",
            "message_count": 1,
            "meta": {},
        }
        window.chat_recovery_journal = MagicMock()
        window.chat_recovery_journal._path_for_session.return_value = "missing"
        window.runtime_journal = MagicMock()
        window.runtime_journal.load_manifest.side_effect = PermissionError("manifest denied")
        window._compose_session_meta = MagicMock(return_value={})
        window._resolved_session_title = MagicMock(return_value="修改后")
        window.append_log = MagicMock()
        window._show_conversation_notice = MagicMock()

        result = MainWindow._persist_history_rewrite(
            window,
            state,
            {"messages_hash": "old-hash", "revision": 1},
            operation="edit_message",
        )

        self.assertEqual(state.chat_save_revision, 2)
        self.assertIn("manifest denied", result["post_commit_error"])
        window.chat_storage.rewrite_conversation_safely.assert_called_once()
        window._show_conversation_notice.assert_called_once()

    def test_error_preserves_interrupted_context_without_raw_reasoning(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_error_stage")
            state.live_activity = True
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            window.handle_llm_response({"error": "provider failed"}, state.session_id, turn_id=1)
            self.assertIsNotNone(bubble.parent())
            self.assertFalse(bubble.think_timer.isActive())
            self.assertNotIn("深度思考中", bubble.think_toggle_btn.text())
            interrupted = [
                message
                for message in state.messages
                if isinstance(message, dict)
                and (message.get("meta") or {}).get("context_visible_interruption")
            ]
            self.assertEqual(interrupted, [])
            self.assertEqual(state.conversation_notice.label.text(), "本轮执行失败")
            self.assertFalse(
                any("本轮执行失败" in str(message.get("content") or "") for message in state.messages)
            )
        finally:
            window.close()
            window.deleteLater()

    def test_worker_error_output_is_logged_without_user_toast(self):
        window = MainWindow.__new__(MainWindow)
        window.append_log = MagicMock()
        window.add_system_toast = MagicMock()

        MainWindow.handle_worker_output(
            window,
            "Provider Error: network disconnected",
            "session-1",
        )

        window.append_log.assert_called_once()
        window.add_system_toast.assert_not_called()

    def test_tool_rounds_append_stages_inside_one_assistant_turn_group(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_unified_turn")
            state.live_activity = True
            state.active_turn_id = 1
            first = window._append_live_thinking_segment(state)
            first.update_thinking("先检查")
            state.current_content_buffer = "先执行检查。"
            window.flush_session_content(state.session_id, final=False)

            window.add_tool_card(
                {"id": "tool-unified", "name": "run_command", "args": {"command": "pytest"}},
                session_id=state.session_id,
                animate=False,
            )
            group = state.active_agent_turn_group
            self.assertIsInstance(group, AssistantTurnGroup)
            self.assertEqual(len(group.stage_bubbles), 1)
            self.assertFalse(first.copy_result_btn.isVisible())
            self.assertFalse(first.office_draft_btn.isVisible())

            window.handle_thinking_signal("继续分析", state.session_id, turn_id=1)
            second = state.temp_thinking_bubble
            self.assertIsNot(first, second)
            self.assertIs(second.parentWidget(), group)
            self.assertEqual(len(group.stage_bubbles), 2)
            self.assertEqual(state.chat_layout.indexOf(group), 0)
            window.handle_content_signal("检查已通过。", state.session_id, turn_id=1)
            window.handle_llm_response(
                {
                    "role": "assistant",
                    "content": "检查已通过。",
                    "reasoning": "继续分析",
                    "duration": 0.2,
                    "generated_messages": [
                        {
                            "id": "assistant-live-stage",
                            "role": "assistant",
                            "content": "先执行检查。",
                            "reasoning_content": "先检查",
                            "tool_calls": [{
                                "id": "tool-unified",
                                "type": "function",
                                "function": {"name": "run_command", "arguments": "{\"command\": \"pytest\"}"},
                            }],
                        },
                        {"id": "tool-live-result", "role": "tool", "tool_call_id": "tool-unified", "content": "ok"},
                        {
                            "id": "assistant-live-final",
                            "role": "assistant",
                            "content": "检查已通过。",
                            "reasoning_content": "继续分析",
                        },
                    ],
                },
                state.session_id,
                turn_id=1,
            )
            self.assertIs(state.chat_layout.itemAt(0).widget(), group)
            self.assertTrue(first.copy_result_btn.isHidden())
            self.assertFalse(second.copy_result_btn.isHidden())
            assistant_messages = [message for message in state.messages if message.get("role") == "assistant"]
            self.assertEqual(assistant_messages[0]["meta"]["ui_reply_kind"], "stage")
            self.assertEqual(assistant_messages[-1]["meta"]["ui_reply_kind"], "final")
        finally:
            window.close()
            window.deleteLater()

    def test_history_restores_one_group_and_final_actions_only(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_unified_history")
            messages = [
                {
                    "id": "assistant-stage",
                    "role": "assistant",
                    "content": "先执行检查。",
                    "reasoning_content": "分析环境",
                    "tool_calls": [{
                        "id": "tool-history",
                        "type": "function",
                        "function": {"name": "run_command", "arguments": "{\"command\": \"pytest\"}"},
                    }],
                },
                {"id": "tool-result", "role": "tool", "tool_call_id": "tool-history", "content": "ok"},
                {
                    "id": "assistant-final",
                    "role": "assistant",
                    "content": "检查已通过。",
                    "reasoning_content": "汇总结论",
                },
            ]
            inserted = window.render_message_batch(messages, state.session_id, animate=False)
            self.assertEqual(inserted, 1)
            group = state.chat_layout.itemAt(0).widget()
            self.assertIsInstance(group, AssistantTurnGroup)
            self.assertEqual(len(group.stage_bubbles), 2)
            stage, final = group.stage_bubbles
            self.assertTrue(stage.copy_result_btn.isHidden())
            self.assertTrue(stage.office_draft_btn.isHidden())
            self.assertFalse(final.copy_result_btn.isHidden())
            self.assertFalse(final.office_draft_btn.isHidden())
            self.assertEqual(final.main_content_text, "检查已通过。")
        finally:
            window.close()
            window.deleteLater()

    def test_history_guidance_splits_groups_and_suppresses_pre_guidance_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_legacy_guidance_history")
            messages = [
                {"id": "assistant-before-guide", "role": "assistant", "content": "先给出阶段结论。"},
                {
                    "id": "legacy-guide",
                    "role": "user",
                    "content": "继续验证测试",
                    "meta": {"same_turn_guidance": True, "turn_id": "1"},
                },
                {"id": "assistant-after-guide", "role": "assistant", "content": "最终结论。"},
            ]
            inserted = window.render_message_batch(messages, state.session_id, animate=False)
            self.assertEqual(inserted, 3)
            first_group = state.chat_layout.itemAt(0).widget()
            guidance = state.chat_layout.itemAt(1).widget()
            second_group = state.chat_layout.itemAt(2).widget()
            self.assertIsInstance(first_group, AssistantTurnGroup)
            self.assertIsInstance(second_group, AssistantTurnGroup)
            self.assertIsNotNone(guidance)
            self.assertTrue(first_group.stage_bubbles[-1].copy_result_btn.isHidden())
            self.assertFalse(second_group.stage_bubbles[-1].copy_result_btn.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_worker_messages_strip_unified_ui_metadata(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.messages = [{
                "id": "assistant-final",
                "role": "assistant",
                "content": "完成",
                "meta": {
                    "ui_turn_id": "1",
                    "ui_turn_group_id": "turn-1-group-1",
                    "ui_stage_id": "turn-1-group-1:stage-1",
                    "ui_reply_kind": "final",
                    "provider_hint": "keep",
                },
            }]
            messages = window._messages_for_worker(state, {})
            self.assertEqual(messages[0]["meta"], {"provider_hint": "keep"})
            self.assertEqual(state.messages[0]["meta"]["ui_turn_group_id"], "turn-1-group-1")
        finally:
            window.close()
            window.deleteLater()

    def test_history_missing_final_body_shows_error_without_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_missing_history_final")
            messages = [
                {
                    "id": "assistant-stage",
                    "role": "assistant",
                    "content": "阶段内容不能成为最终答复。",
                    "tool_calls": [{
                        "id": "tool-missing-final",
                        "type": "function",
                        "function": {"name": "run_command", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "tool-missing-final", "content": "ok"},
                {"id": "assistant-empty-final", "role": "assistant", "content": ""},
            ]
            self.assertEqual(window.render_message_batch(messages, state.session_id, animate=False), 1)
            group = state.chat_layout.itemAt(0).widget()
            final = group.stage_bubbles[-1]
            self.assertIn("未收到最终答复", final.main_content_text)
            self.assertNotIn("阶段内容不能成为最终答复", final.main_content_text)
            self.assertTrue(final.copy_result_btn.isHidden())
            self.assertTrue(final.office_draft_btn.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_live_missing_final_body_does_not_promote_stage_reply(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_missing_live_final")
            state.live_activity = True
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            window.handle_llm_response(
                {
                    "role": "assistant",
                    "content": "",
                    "generated_messages": [
                        {
                            "id": "assistant-stage",
                            "role": "assistant",
                            "content": "阶段内容不能成为最终答复。",
                            "tool_calls": [{
                                "id": "tool-live-missing-final",
                                "type": "function",
                                "function": {"name": "run_command", "arguments": "{}"},
                            }],
                        },
                        {
                            "id": "tool-result-live-missing-final",
                            "role": "tool",
                            "tool_call_id": "tool-live-missing-final",
                            "content": "ok",
                        },
                        {"id": "assistant-empty-final", "role": "assistant", "content": ""},
                    ],
                },
                state.session_id,
                turn_id=1,
            )
            self.assertIsNotNone(bubble.parent())
            self.assertNotIn("本轮执行失败", bubble.main_content_text)
            self.assertEqual(state.conversation_notice.label.text(), "本轮执行失败")
            assistants = [message for message in state.messages if message.get("role") == "assistant"]
            self.assertEqual(len(assistants), 2)
            self.assertFalse(
                any(
                    message.get("id") == "assistant-stage"
                    and not message.get("tool_calls")
                    for message in assistants
                )
            )
            self.assertFalse(
                any((message.get("meta") or {}).get("context_visible_interruption") for message in assistants)
            )
        finally:
            window.close()
            window.deleteLater()

    def test_live_continuation_renders_registered_inline_visualization(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow()
            try:
                state = window.get_current_session()
                window._retire_session_empty_state(state, reason="test_inline_visualization")
                window.chat_storage = ChatStorage(os.path.join(root, "chat.sqlite"))
                path = os.path.join(root, "live-visual.html")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write('<div id="live-visual">ok</div>')
                with open(path, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
                artifact = {
                    "file": "live-visual-12345678.html",
                    "path": path,
                    "sha256": digest,
                    "title": "续接视图",
                    "origins": [],
                }
                window.chat_storage.register_inline_visualization(state.session_id, artifact)

                with patch.object(window.config_manager, "is_skill_enabled", return_value=True):
                    continuation = window._append_live_thinking_segment(state)
                directive = '::cowork-inline-vis{file="live-visual-12345678.html"}'
                with patch("main.QTimer.singleShot"):
                    continuation.set_main_content(f"结果\n\n{directive}", final=True)

                self.assertEqual(continuation.session_id, state.session_id)
                self.assertIs(continuation.chat_storage, window.chat_storage)
                self.assertTrue(continuation.visualize_enabled)
                self.assertEqual(len(continuation.inline_visualization_cards), 1)
                self.assertNotIn("cowork-inline-vis", continuation.content_edit.toPlainText())
            finally:
                window.close()
                window.deleteLater()

    def test_ui_timeline_metadata_validation_is_explicit(self):
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{"kind": "thinking", "sequence": 1, "started_at": 1.0, "text": "检查"}],
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(warning, "")
        events, warning = MainWindow._normalize_ui_timeline_events(None, [{"kind": "unknown"}])
        self.assertEqual(events, [])
        self.assertTrue(warning)
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{
                "kind": "thinking",
                "sequence": 2,
                "started_at": 2.0,
                "group_id": "turn-1-group-1",
                "stage_id": "turn-1-group-1:stage-1",
            }],
        )
        self.assertEqual(events[0]["group_id"], "turn-1-group-1")
        self.assertEqual(warning, "")
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{"kind": "thinking", "sequence": 3, "started_at": 3.0, "group_id": "incomplete"}],
        )
        self.assertEqual(events, [])
        self.assertIn("不完整", warning)

    def test_ui_timeline_coalesces_deltas_and_splits_at_guidance(self):
        class TimelineStub:
            _timeline_append_event = MainWindow._timeline_append_event
            _timeline_find_event = MainWindow._timeline_find_event
            _timeline_append_text_delta = MainWindow._timeline_append_text_delta

        window = TimelineStub()
        state = SimpleNamespace(active_turn_id=3, ui_timeline_events=[], ui_timeline_sequence=0)
        first = window._timeline_append_text_delta(state, "thinking", "先检查")
        second = window._timeline_append_text_delta(state, "thinking", "环境")
        self.assertIs(first, second)
        self.assertEqual(first["text"], "先检查环境")
        window._timeline_append_event(
            state,
            "guidance",
            status="queued",
            message_id="guide-4",
            text="优先测试",
        )
        continued = window._timeline_append_text_delta(state, "thinking", "继续")
        self.assertIsNot(first, continued)
        self.assertEqual([event["kind"] for event in state.ui_timeline_events], ["thinking", "guidance", "thinking"])

    def test_persisted_timeline_rebuilds_send_time_order(self):
        state = SimpleNamespace(
            session_id="session-timeline",
            live_activity=False,
            last_agent_bubble=None,
            tool_cards={},
            ui_timeline_events=[
                {"sequence": 1, "turn_id": "7", "kind": "thinking", "status": "completed", "started_at": 1.0, "finished_at": 2.0, "text": "先分析"},
                {"sequence": 2, "turn_id": "7", "kind": "tool", "status": "completed", "started_at": 2.0, "finished_at": 3.0, "tool_call_id": "tool-7", "text": "运行测试"},
                {"sequence": 3, "turn_id": "7", "kind": "guidance", "status": "applied", "started_at": 2.5, "finished_at": 3.1, "message_id": "guide-7", "text": "优先测试"},
                {"sequence": 4, "turn_id": "7", "kind": "thinking", "status": "completed", "started_at": 3.1, "finished_at": 4.0, "text": "继续分析"},
                {"sequence": 5, "turn_id": "7", "kind": "final_content", "status": "completed", "started_at": 4.0, "finished_at": 4.2, "text": "最终结果"},
            ],
        )

        class RenderStub:
            _render_persisted_timeline_items = MainWindow._render_persisted_timeline_items

            def __init__(self):
                self.bubbles = []
                self.render_order = []

            def add_chat_bubble(self, *_args, **_kwargs):
                bubble = ChatBubble("Agent", "")
                self.bubbles.append(bubble)
                self.render_order.append(("thinking", bubble))
                return bubble

            def add_turn_guidance_inline(self, message, **kwargs):
                guidance = GuidanceTimelineEvent(
                    message.get("id") or "",
                    kwargs.get("display_content") or "",
                    status=kwargs.get("status") or "applied",
                )
                self.render_order.append(("guidance", guidance))
                return guidance

            def add_tool_card(self, data, **_kwargs):
                card = ToolCallCard(data["name"], data["args"], data["id"], meta=data.get("meta"))
                state.tool_cards[data["id"]] = card
                state.last_agent_bubble.add_tool_card(card)

            def update_tool_card(self, data, **_kwargs):
                state.tool_cards[data["id"]].set_result(data.get("result") or "", data.get("result_obj"))

            def _assistant_source_message_id_from_messages(self, messages):
                return str((messages or [{}])[-1].get("id") or "")

            def _message_display_content(self, message):
                return str((message or {}).get("content") or "")

            def _message_user_attachments(self, _message):
                return []

        render_items = [
            {
                "type": "assistant",
                "content": "阶段回复",
                "messages": [{"id": "assistant-1"}],
                "tool_calls": [{"id": "tool-7", "name": "run_command", "args": {"command": "tests"}, "result": "ok"}],
            },
            {"type": "guidance", "message": {"id": "guide-7", "content": "优先测试", "meta": {"same_turn_guidance": True, "turn_id": "7"}}},
            {"type": "assistant", "content": "最终结果", "messages": [{"id": "assistant-2"}], "tool_calls": []},
        ]
        window = RenderStub()
        self.assertTrue(window._render_persisted_timeline_items(render_items, state))
        self.assertEqual([kind for kind, _widget in window.render_order], ["thinking", "guidance", "thinking"])
        self.assertTrue(any(isinstance(item, ToolCallCard) for item in window.bubbles[0].timeline_events))
        self.assertEqual(window.bubbles[1].main_content_text, "最终结果")
        for _kind, widget in window.render_order:
            widget.deleteLater()

    def test_persisted_interrupted_timeline_restores_without_guidance(self):
        state = SimpleNamespace(
            session_id="session-interrupted-timeline",
            live_activity=False,
            last_agent_bubble=None,
            tool_cards={},
            ui_timeline_events=[
                {
                    "sequence": 1,
                    "turn_id": "8",
                    "kind": "thinking",
                    "status": "interrupted",
                    "started_at": 1.0,
                    "finished_at": 2.0,
                    "text": "已完成分析",
                },
                {
                    "sequence": 2,
                    "turn_id": "8",
                    "kind": "error",
                    "status": "interrupted",
                    "finished_at": 2.0,
                    "text": "本轮已中断",
                },
            ],
        )

        class RenderStub:
            _render_persisted_timeline_items = MainWindow._render_persisted_timeline_items

            def __init__(self):
                self.bubble = None

            def add_chat_bubble(self, *_args, **_kwargs):
                self.bubble = ChatBubble("Agent", "")
                return self.bubble

            def _assistant_source_message_id_from_messages(self, messages):
                return str((messages or [{}])[-1].get("id") or "")

        render_items = [{
            "type": "assistant",
            "content": "部分正文\n\n⚠️ 本轮已中断，以上内容可能不完整。",
            "messages": [{
                "id": "assistant-interrupted",
                "meta": {
                    "ui_turn_id": "8",
                    "ui_reply_kind": "interrupted",
                    "context_visible_interruption": True,
                },
            }],
            "tool_calls": [],
        }]
        window = RenderStub()

        self.assertTrue(window._render_persisted_timeline_items(render_items, state))
        self.assertIn("本轮已中断", window.bubble.main_content_text)
        self.assertFalse(window.bubble.think_timer.isActive())
        self.assertTrue(window.bubble.copy_result_btn.isHidden())
        window.bubble.deleteLater()

    def test_short_user_message_uses_natural_width(self):
        bubble = ChatBubble("User", "你好")
        bubble.apply_dynamic_widths(880, 640)
        self.assertLess(bubble.user_content_edit.width(), 200)
        bubble.deleteLater()

    def test_skill_update_uses_searchable_target_not_native_combo(self):
        parent = QWidget()
        parent.skill_manager = SimpleNamespace(is_skill_editable=lambda _name: True)
        skills = [{"name": "editable-skill", "display_name": "Editable Skill", "description": "可编辑"}]
        dialog = ConversationSkillOptionsDialog(skills, parent)
        self.assertIsInstance(dialog.target_combo, SearchableSkillPickerButton)
        self.assertNotIsInstance(dialog.target_combo, QComboBox)
        dialog.deleteLater()
        parent.deleteLater()

    def test_skill_range_hides_injected_context_and_supports_quick_ranges(self):
        messages = [
            {
                "id": "context",
                "role": "system",
                "content": "ctx",
                "meta": {"kind": "skill_context", "hidden": True, "source": "skill_prompt"},
            },
            {"id": "u1", "role": "user", "content": "问题"},
            {"id": "a1", "role": "assistant", "content": "回答"},
        ]
        dialog = ConversationSkillRangeDialog(messages)
        self.assertEqual(dialog.message_list.count(), 2)
        dialog._apply_quick_range("current")
        self.assertEqual(len(dialog.selected_messages()), 2)
        dialog.deleteLater()

    def test_relative_time_uses_product_copy(self):
        now = __import__("time").time()
        self.assertEqual(MainWindow._format_project_session_age(None, now), "刚刚")
        self.assertIn("分钟前", MainWindow._format_project_session_age(None, now - 120))


if __name__ == "__main__":
    unittest.main()
