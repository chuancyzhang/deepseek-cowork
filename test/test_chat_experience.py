import base64
import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")
from PySide6.QtCore import QMimeData, Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from core.conversation_render import build_conversation_render_spans
from core.llm.providers import _build_openai_user_content
from core.workbench_terminal import terminal_launch_args
from core.theme import DesignTokens, default_design_tokens
from core.theme_service import resolve_theme
from main import MainWindow, SessionContextChip, render_markdown_or_html_with_cache
from ui.conversation_search import find_conversation_messages
from ui.primitives import SidebarInlineNameEditor


class ChatExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-chat-test-")
        self.addCleanup(self.temp.cleanup)
        for name in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir", "main.get_app_data_dir"):
            self.enterContext(patch(name, return_value=self.temp.name))
        self.window = MainWindow()
        self.window._startup_hydration_scheduled = True
        self.window._background_services_scheduled = True

    def tearDown(self):
        self.window.close()
        for _ in range(100):
            QTest.qWait(50)
            if getattr(self.window, "_checkpoint_close_ready", False):
                break
        self.assertTrue(getattr(self.window, "_checkpoint_close_ready", False))
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_long_paste_attachment_preserves_exact_text_and_session(self):
        text = "完整原文🚀\r\n" * 1000
        self.window.input_field.setPlainText("请总结附件")
        mime = QMimeData()
        mime.setText(text)
        self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), "请总结附件")
        state = self.window.get_current_session()
        path, = state.prompt_files
        self.assertEqual(Path(path).read_bytes().decode("utf-8"), text)
        wire = _build_openai_user_content("请总结", [{"type": "input_file", "path": path, "name": Path(path).name}])
        self.assertIn("完整原文", str(wire))
        old_id = state.session_id
        self.window.create_new_session()
        self.assertEqual(self.window._current_prompt_files(), [])
        self.window.activate_session(old_id)
        self.assertEqual(self.window._current_prompt_files(), [path])
        self.window._remove_prompt_file(path)
        self.assertFalse(Path(path).exists())

    def test_failed_paste_keeps_original_and_does_not_attach(self):
        text = "不能丢失" * 1100
        mime = QMimeData()
        mime.setText(text)
        with patch("main.os.replace", side_effect=OSError("disk error")):
            self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), text)
        self.assertEqual(self.window._current_prompt_files(), [])

    def test_short_paste_is_inline(self):
        mime = QMimeData()
        mime.setText("一段短文字")
        self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), "一段短文字")
        self.assertFalse(self.window._current_prompt_files())

    def test_session_activation_runs_once_and_keeps_draft(self):
        previous = self.window.current_session_id
        self.window.create_new_session()
        state = self.window.get_current_session()
        self.window.input_field.setPlainText("草稿必须保留")
        with patch.object(self.window, "set_current_session", wraps=self.window.set_current_session) as activate:
            self.window.activate_session(previous)
        self.assertEqual(activate.call_count, 1)
        self.assertEqual(state.composer_draft, "草稿必须保留")

    def test_skill_click_adds_immediately_and_tag_removal_is_session_local(self):
        self.window.skill_manager_ready = True
        self.window._available_session_skills = lambda: [{"name": "report", "display_name": "报告", "type": "ai_generated"}]
        self.window.show()
        self.window.open_session_skill_picker()
        popover = self.window.session_skill_popover
        QTest.qWait(150)
        item = popover.skill_list.item(0)
        QTest.mouseClick(popover.skill_list.viewport(), Qt.LeftButton,
                         pos=popover.skill_list.visualItemRect(item).center())
        state = self.window.get_current_session()
        self.assertEqual(state.selected_skill_names, ["report"])
        QTest.keyClick(popover.skill_list, Qt.Key_Space)
        self.assertEqual(state.selected_skill_names, [])
        QTest.keyClick(popover.skill_list, Qt.Key_Space)
        popover.close()
        self.app.processEvents()
        self.window.create_new_session()
        self.assertTrue(self.window.composer_skill_tags.isHidden())
        self.window.activate_session(state.session_id)
        chip = self.window.composer_skill_tag_layout.itemAt(0).widget()
        self.assertIsInstance(chip, SessionContextChip)
        QTest.mouseClick(chip.close_btn, Qt.LeftButton)
        self.assertEqual(state.selected_skill_names, [])
        self.assertTrue(self.window.composer_skill_tags.isHidden())

    def test_permission_choices_persist_and_restore_on_save_failure(self):
        initial = self.window.composer_permission.isChecked()
        self.window._choose_composer_permission(not initial)
        self.assertEqual(self.window.config_manager.get_god_mode(), not initial)
        with patch.object(self.window.config_manager, "set_god_mode", side_effect=OSError("read only")):
            self.window._choose_composer_permission(initial)
        self.assertEqual(self.window.composer_permission.isChecked(), not initial)
        self.assertEqual(self.window.permission_button.text(), "完全访问" if not initial else "请求批准")

    def test_skill_and_connector_categories_preserve_each_others_selection(self):
        self.window.skill_manager_ready = True
        self.window._available_session_skills = lambda: [
            {"name": "report", "type": "ai_generated"},
            {"name": "docs", "source_format": "mcp_server"},
        ]
        self.window.show()
        self.window.set_session_selected_skills(["report"])
        self.window.open_session_skill_picker(category="connectors")
        picker = self.window.session_skill_popover
        self.assertEqual([skill["name"] for skill in picker.skills], ["docs"])
        picker.skill_list.item(0).setCheckState(Qt.Checked)
        self.assertEqual(self.window.get_current_session().selected_skill_names, ["report", "docs"])
        picker.close()
        self.window.open_session_skill_picker()
        picker = self.window.session_skill_popover
        self.assertEqual([skill["name"] for skill in picker.skills], ["report"])
        picker.skill_list.item(0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.window.get_current_session().selected_skill_names, ["docs"])
        picker.close()

    def test_mode_switch_uses_existing_grill_state_and_ignores_stale_session(self):
        self.window.show()
        state = self.window.get_current_session()
        self.window.show_composer_mode_picker()
        toggle = self.window.composer_grill_switch
        QTest.mouseClick(toggle, Qt.LeftButton)
        self.assertEqual(state.grill_mode_state, "armed")
        self.window.create_new_session()
        toggle.setChecked(False)
        self.assertEqual(state.grill_mode_state, "armed")
        self.assertEqual(self.window.get_current_session().grill_mode_state, "disabled")

    def test_settings_reused_until_configuration_changes(self):
        self.window.open_settings()
        page = self.window.product_pages["settings"]
        self.window.show_product_page("knowledge")
        self.window.open_settings()
        self.assertIs(page, self.window.product_pages["settings"])
        self.window.show_conversation_page()
        self.window.config_manager.config["god_mode"] = not self.window.config_manager.get_god_mode()
        self.window.open_settings()
        self.assertIsNot(page, self.window.product_pages["settings"])

    def test_search_loads_old_pages_and_highlights_matching_answer(self):
        state = self.window.get_current_session()
        self.window.clear_chat_layout(state.chat_layout)
        state.empty_state = None
        state.messages = [
            {"id": f"{role}{i}", "role": role, "content": f"{'目标答案' if i == 0 and role == 'assistant' else '普通文字'} {i}"}
            for i in range(8) for role in ("user", "assistant")
        ]
        state.render_items = build_conversation_render_spans(state.messages)
        state.history_loaded = True
        state.displayed_render_count = 2
        self.window._render_session_history_spans(state, state.render_items[-2:])
        self.window.resize(1280, 800)
        self.window.show()
        self.window._sync_home_presentation()
        self.window._open_conversation_search()
        self.window.conversation_search.input.setText("目标答案")
        for _ in range(100):
            QTest.qWait(50)
            if self.window._conversation_search_highlight is not None:
                break
        self.assertEqual(len(self.window._conversation_search_matches), 1)
        self.assertIn("assistant0", state.render_node_by_message_id)
        self.assertIsNotNone(self.window._conversation_search_highlight)
        self.assertIn("目标答案", self.window._conversation_search_highlight.toPlainText())
        self.window.create_new_session()
        self.assertTrue(self.window.conversation_search.isHidden())
        self.assertEqual(self.window._conversation_search_matches, [])

    def test_header_unchanged_title_does_not_write(self):
        state = self.window.get_current_session()
        with patch.object(self.window.chat_storage, "upsert_conversation") as save:
            self.window.begin_session_inline_rename(state.session_id, (self.window.workspace_title_layout, self.window.workspace_title_label))
            editor = self.window.workspace_title_layout.parentWidget().findChild(SidebarInlineNameEditor)
            editor._commit()
            save.assert_not_called()


class ChatExperienceUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_click_on_nonfocus_surface_commits_once(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        editor = SidebarInlineNameEditor("旧标题")
        blank = QLabel("点击空白区域")
        layout.addWidget(editor)
        layout.addWidget(blank)
        results = []
        editor.commitRequested.connect(results.append)
        host.show()
        editor.setFocus()
        editor.setText("新标题")
        self.app.processEvents()
        QTest.mouseClick(blank, Qt.LeftButton)
        QTest.mouseClick(blank, Qt.LeftButton)
        self.assertEqual(results, ["新标题"])
        host.close()

    def test_search_uses_visible_projection_without_modifying_history(self):
        messages = [
            {"id": "u", "role": "user", "content": "Hello 世界"},
            {"id": "a", "role": "assistant", "content": "secret", "meta": {"security_display_content": "HELLO"}},
            {"id": "h", "role": "user", "content": "hello", "meta": {"hidden": True, "kind": "runtime_context"}},
            {"id": "t", "role": "tool", "content": "hello"},
        ]
        original = copy.deepcopy(messages)
        self.assertEqual([m["message_id"] for m in find_conversation_messages(messages, "hello")], ["u", "a"])
        self.assertEqual(find_conversation_messages(messages, "secret"), [])
        self.assertEqual(messages, original)

    def test_link_style_and_cache_follow_theme(self):
        _, light = render_markdown_or_html_with_cache("[文件](C:/report.txt)", final=True)
        with patch.object(DesignTokens, "chat_link", "#8ab4f8"):
            _, dark = render_markdown_or_html_with_cache("[文件](C:/report.txt)", final=True)
        self.assertIn("text-decoration: underline", light)
        self.assertIn("#8ab4f8", dark)
        self.assertNotEqual(light, dark)
        resolved = resolve_theme({"overrides": {"tokens": {"bg_chat": "#202020"}}}, default_design_tokens())
        self.assertEqual(resolved["tokens"]["chat_link"], "#8ab4f8")

    @unittest.skipUnless(os.name == "nt", "PowerShell on Windows")
    def test_prompt_shortens_only_display_and_handles_unicode(self):
        args = terminal_launch_args({"id": "powershell", "args": ["-NoLogo"]})
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "含空格的目录 " + "long-name-" * 8)
            path.mkdir()
            script += "\n[Console]::OutputEncoding = [Text.Encoding]::UTF8; prompt; (Get-Location).Path"
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-EncodedCommand", encoded], cwd=path, capture_output=True, check=True)
            output = result.stdout.decode("utf-8-sig").splitlines()
            self.assertLess(len(output[0]), 55)
            self.assertIn("…", output[0])
            self.assertEqual(output[1], str(path))
