import copy
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from core.config_manager import ConfigManager
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_presets import BUILTIN_THEMES
from core.theme_service import ThemeRepository
from main import MainWindow, SettingsDialog


class SettingsRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf"):
            path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", filename)
            if os.path.exists(path):
                QFontDatabase.addApplicationFont(path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir"):
            patcher = patch(target, return_value=self.temp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.original_registry = getattr(self.app, "theme_binding_registry", None)
        self.original_font = self.app.font()
        self.original_styles = self.app.styleSheet()
        self.original_palette = self.app.palette()
        self.original_tokens = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        self.repository = ThemeRepository(self.temp.name)
        self.manager = ThemeRuntimeManager(self.app, self.repository)
        self.config = ConfigManager()
        self.widgets = []
        self.assertTrue(self.manager.apply_repository_state(reason="test_start"), self.manager.last_error)

    def tearDown(self):
        self.manager.stop()
        for widget in reversed(self.widgets):
            if isinstance(widget, SettingsDialog):
                widget._allow_close_without_prompt = True
            widget.close()
            if isinstance(widget, MainWindow):
                self.wait_for(lambda: getattr(widget, "_checkpoint_close_ready", False))
                widget.file_model.directoryLoaded.disconnect()
            widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.theme_binding_registry = self.original_registry
        self.app.setFont(self.original_font)
        self.app.setStyleSheet(self.original_styles)
        self.app.setPalette(self.original_palette)
        for key, value in self.original_tokens.items():
            setattr(DesignTokens, key, value)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate())

    def panel(self):
        parent = MainWindow(config_manager=self.config, theme_manager=self.manager)
        self.widgets.append(parent)
        page = SettingsDialog(self.config, parent)
        self.widgets.append(page)
        return parent, page

    def test_builtin_pairs_and_undo_keep_reading_preferences(self):
        self.repository.save_appearance({"mode": "light", "reading": {"font_scale": 1.15, "density": "comfortable"}})
        self.manager.apply_repository_state()
        for key in BUILTIN_THEMES:
            self.assertTrue(self.manager.switch_theme(key, mode="dark"))
            self.assertEqual(self.manager.current["font_scale"], 1.15)
            self.assertEqual(self.manager.current["density"], "comfortable")
        previous = copy.deepcopy(self.manager.current)
        self.manager.switch_theme("default", mode="light")
        self.assertTrue(self.manager.switch_theme(undo=True))
        self.assertEqual(self.manager.current["tokens"], previous["tokens"])

    def test_unchanged_theme_does_not_refresh_widgets(self):
        with patch.object(self.manager, "_refresh_existing_widgets") as refresh:
            self.assertTrue(self.manager.apply_repository_state())
        refresh.assert_not_called()

    def test_dark_switch_repaints_cached_palette_without_replacing_global_styles(self):
        parent, page = self.panel()
        parent.resize(1280, 900)
        page.resize(1000, 760)
        self.manager.switch_theme("builtin_forest")
        stylesheet = self.app.styleSheet()
        self.manager.switch_theme(mode="dark")
        self.assertEqual(self.app.styleSheet(), stylesheet)
        from PySide6.QtGui import QPalette
        self.assertEqual(parent.main_container.palette().color(QPalette.Base).name(), DesignTokens.overlay_bg)
        self.assertEqual(page.grab().toImage().pixelColor(2, 2).name(), DesignTokens.bg_main)

    def test_quick_switch_alone_does_not_mark_settings_dirty(self):
        _parent, page = self.panel()
        self.manager.switch_theme("builtin_paper")
        self.assertFalse(page._settings_dirty)
        self.manager.switch_theme(undo=True)
        self.assertFalse(page._settings_dirty)

    def test_search_opens_matching_nested_section(self):
        _parent, page = self.panel()
        page.settings_search.setText("日志")
        save_clicks = QSignalSpy(page.save_settings_btn.clicked)
        QTest.keyClick(page.settings_search, Qt.Key_Return)
        self.assertEqual(page._page_labels[page._active_settings_row], "高级与维护")
        self.assertEqual(page.advanced_section.current_label, "运行日志")
        self.assertEqual(save_clicks.count(), 0)

    def test_search_results_navigate_without_discarding_drafts(self):
        _parent, page = self.panel()
        page.theme_settings_panel.reading_scale.setValue(1.2)
        page.settings_search.setText("字体")
        self.assertTrue(page._settings_search_matches)
        self.assertFalse(any("等宽字体" in match[1] for match in page._settings_search_matches))
        field_index = next(index for index, match in enumerate(page._settings_search_matches) if match[-1] is not None and match[-1].text() == "界面字体")
        page._open_settings_search_result(page.settings_search_results.item(field_index))
        self.assertEqual(page._page_labels[page._active_settings_row], "外观与阅读")
        self.assertEqual(page.theme_settings_panel.reading_scale.value(), 1.2)
        self.assertTrue(page._settings_dirty)
        page.settings_search.setText("没有这项设置_xyz")
        self.assertFalse(page._settings_search_matches)
        self.assertIn("没有找到", page.settings_search_results.item(0).text())
        page.settings_search.clear()
        self.assertTrue(page.settings_search_results.isHidden())
        self.assertTrue(all(not page.nav_list.item(index).isHidden() for index in range(page.nav_list.count())))

    def test_failed_settings_save_preserves_theme_and_reading_drafts(self):
        _parent, page = self.panel()
        panel = page.theme_settings_panel
        panel._new_theme()
        panel.color_edits["primary"].setText("#39735a")
        panel.reading_scale.setValue(1.2)
        page.memory_soul_edit.setPlainText("待保存的偏好")
        with patch.object(panel.repository, "replace_state", side_effect=OSError("disk write failed")), \
                patch("main.QMessageBox.critical") as error:
            page.save_settings()
        error.assert_called_once()
        self.assertEqual(panel.color_edits["primary"].text(), "#39735a")
        self.assertEqual(panel.reading_scale.value(), 1.2)
        self.assertEqual(page.memory_soul_edit.toPlainText(), "待保存的偏好")
        self.assertTrue(page._settings_dirty)
        self.assertEqual(self.repository.load().themes, ())

    def test_reading_save_does_not_rewrite_theme_packages(self):
        _parent, page = self.panel()
        panel = page.theme_settings_panel
        panel.reading_scale.setValue(1.15)
        with patch.object(panel.repository, "replace_state", side_effect=AssertionError("packages rewritten")):
            panel.commit()
        self.assertEqual(self.repository.load_appearance()["reading"]["font_scale"], 1.15)

    def test_theme_apply_keeps_unsaved_reading_draft(self):
        _parent, page = self.panel()
        panel = page.theme_settings_panel
        panel._new_theme()
        panel.reading_scale.setValue(1.2)
        panel.commit(include_reading=False)
        panel._selection_updated(None)
        self.assertEqual(panel.reading_scale.value(), 1.2)
        self.assertNotEqual(panel.repository.load_appearance()["reading"].get("font_scale"), 1.2)
        self.assertTrue(page._settings_dirty)

    def test_discarding_settings_draft_preserves_new_ai_candidate(self):
        _parent, page = self.panel()
        panel = page.theme_settings_panel
        panel._new_theme()
        panel._preview_current()
        candidate = self.repository.write_preview(name="新的 AI 候选", overrides={},
            default_tokens=default_design_tokens(), session_id="other-design", replace_existing=True)
        panel.restore_saved_theme()
        self.assertEqual(self.repository.load_preview()["preview_id"], candidate["preview_id"])
        self.wait_for(lambda: self.manager.preview is not None and self.manager.preview["preview_id"] == candidate["preview_id"])

    def test_quick_switch_preserves_invalid_theme_editor_input(self):
        _parent, page = self.panel()
        panel = page.theme_settings_panel
        panel._new_theme()
        panel.token_editor.setPlainText("{unfinished")
        self.manager.switch_theme("builtin_paper")
        self.assertEqual(panel.token_editor.toPlainText(), "{unfinished")
        self.assertTrue(page._settings_dirty)

    def test_superseded_background_preview_is_not_published(self):
        preview = self.repository.write_preview(name="候选", overrides={}, default_tokens=default_design_tokens(), session_id="design")
        started, release = threading.Event(), threading.Event()
        original = self.repository.load_preview_bundle
        calls = 0
        def delayed(defaults):
            nonlocal calls
            result = original(defaults)
            calls += 1
            if calls == 1:
                started.set()
                release.wait(3)
            return result
        published = []
        self.manager.previewStateChanged.connect(lambda value: published.append(value))
        with patch.object(self.repository, "load_preview_bundle", side_effect=delayed):
            try:
                self.manager.request_preview_update()
                self.wait_for(started.is_set)
                latest = self.repository.patch_preview(preview_id=preview["preview_id"], preview_revision=preview["revision"],
                    set_overrides={"tokens": {"primary": "#225533"}}, unset_tokens=[], default_tokens=default_design_tokens())
                release.set()
                self.wait_for(lambda: self.manager.preview is not None)
            finally:
                release.set()
        self.assertEqual([item["revision"] for item in published if item], [latest["revision"]])

    def test_applied_ai_candidate_can_be_undone(self):
        self.manager.switch_theme("builtin_forest", mode="dark")
        previous = copy.deepcopy(self.manager.current["tokens"])
        preview = self.repository.write_preview(name="AI 候选", overrides={"tokens": {"primary": "#225533"}},
                                               default_tokens=default_design_tokens(), session_id="design")
        self.manager.commit_current_preview(expected_id=preview["preview_id"], expected_revision=preview["revision"])
        self.assertNotEqual(self.manager.current["id"], "builtin_forest")
        self.manager.switch_theme(undo=True)
        self.assertEqual(self.manager.current["tokens"], previous)

    def test_ai_candidate_is_prepared_without_global_apply_and_exact_revision_required(self):
        original = copy.deepcopy(self.manager.current)
        preview = self.repository.write_preview(name="候选", overrides={"tokens": {"primary": "#39735a"}},
                                                default_tokens=default_design_tokens(), session_id="theme-session")
        with patch.object(self.manager, "apply_profile", wraps=self.manager.apply_profile) as apply:
            self.manager.poll_external_changes()
            self.wait_for(lambda: self.manager.preview is not None)
            apply.assert_not_called()
        self.assertEqual(self.manager.current, original)
        self.assertEqual(self.manager.preview["_resolved"]["tokens"]["primary"], "#39735a")
        self.repository.patch_preview(preview_id=preview["preview_id"], preview_revision=preview["revision"],
                                      set_overrides={"tokens": {"primary": "#225533"}}, unset_tokens=[],
                                      default_tokens=default_design_tokens())
        with self.assertRaisesRegex(ValueError, "已更新"):
            self.manager.commit_current_preview(expected_id=preview["preview_id"], expected_revision=preview["revision"])
        with self.assertRaisesRegex(ValueError, "已更新"):
            self.manager.restore_saved_theme(expected_id=preview["preview_id"], expected_revision=preview["revision"])
        self.assertEqual(self.repository.load_preview()["revision"], preview["revision"] + 1)
        self.assertEqual(self.repository.load().active_theme_id, "default")

    def test_navigation_preserves_drafts_and_legacy_routes(self):
        _parent, page = self.panel()
        self.assertEqual(page._page_labels, ["外观与阅读", "回答偏好", "模型与服务", "消息连接", "企业账号", "隐私与数据", "文件与存储", "高级与维护"])
        page.memory_soul_edit.setPlainText("保留的回答偏好")
        page.theme_settings_panel.reading_scale.setValue(1.15)
        with patch.object(page, "_confirm_discard_settings", side_effect=AssertionError("switch discarded draft")):
            page.select_initial_page("模型与服务")
            page.select_initial_page("变量与凭据")
            self.assertEqual(page.privacy_section.current_label, "变量与凭据")
            page.select_initial_page("运行日志")
            self.assertEqual(page.advanced_section.current_label, "运行日志")
        self.assertEqual(page.memory_soul_edit.toPlainText(), "保留的回答偏好")
        self.assertEqual(page.theme_settings_panel.reading_scale.value(), 1.15)
        self.assertTrue(page._settings_dirty)

    def test_quick_switch_preserves_input_and_does_not_rebuild_history(self):
        parent, page = self.panel()
        parent.input_field.setPlainText("仍未发送的输入")
        page.memory_soul_edit.setPlainText("未保存的偏好")
        page.theme_settings_panel.reading_scale.setValue(1.2)
        with patch.object(parent, "refresh_history_list", side_effect=AssertionError("history rebuilt")):
            self.manager.switch_theme("builtin_forest")
        self.assertEqual(parent.input_field.toPlainText(), "仍未发送的输入")
        self.assertEqual(page.memory_soul_edit.toPlainText(), "未保存的偏好")
        self.assertEqual(page.theme_settings_panel.reading_scale.value(), 1.2)
        self.assertTrue(page._settings_dirty)

    def test_background_design_uses_separate_session_and_preserves_composer(self):
        parent, _page = self.panel()
        current_id = parent.current_session_id
        parent.input_field.setPlainText("正在写的原对话")
        with patch.object(parent, "_home_action_skill_records", return_value={"theme-customizer": {"enabled": True}}), \
                patch.object(parent, "_submit_session_request", return_value=True) as submit:
            session_id = parent.start_theme_design("希望是苔绿色")
        self.assertNotEqual(session_id, current_id)
        self.assertEqual(parent.current_session_id, current_id)
        self.assertEqual(parent.input_field.toPlainText(), "正在写的原对话")
        self.assertIs(submit.call_args.args[0], parent.sessions[session_id])
        self.assertIn("苔绿色", submit.call_args.args[1])
        self.assertIn("theme-customizer", parent.sessions[session_id].selected_skill_names)


if __name__ == "__main__":
    unittest.main()
