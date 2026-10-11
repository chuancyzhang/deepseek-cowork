import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository, DEFAULT_THEME_ID
from core.task_details import tool_result_status
from main import MainWindow, ToolCallCard


class ToolTaskUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
                QFontDatabase.addApplicationFont(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", filename))
        cls.app.setFont(QFont("Microsoft YaHei UI", 10))

    def test_pending_completed_and_interrupted_cards_at_small_width(self):
        original_style, original_font = self.app.styleSheet(), self.app.font()
        original_tokens = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        original_registry = getattr(self.app, "theme_binding_registry", None)
        original_manager = getattr(self.app, "theme_manager", None)
        temporary = tempfile.TemporaryDirectory()
        runtime = ThemeRuntimeManager(self.app, ThemeRepository(temporary.name))
        self.app.theme_manager = runtime
        def cleanup():
            runtime.stop()
            self.app.theme_binding_registry = original_registry
            self.app.theme_manager = original_manager
            for key, value in original_tokens.items():
                setattr(DesignTokens, key, value)
            self.app.setStyleSheet(original_style)
            self.app.setFont(original_font)
            temporary.cleanup()
        self.addCleanup(cleanup)
        for theme in ("light", "dark"):
            tokens = {} if theme == "light" else {
                "bg_main": "#202124", "bg_app": "#202124", "bg_secondary": "#292b30",
                "bg_chat": "#202124", "bg_assistant_stream": "#202124", "bg_hover": "#35373d",
                "text_primary": "#eeeeee", "text_secondary": "#b6b8bd", "text_tertiary": "#a9adb5",
                "chat_text": "#eeeeee", "chat_text_muted": "#b6b8bd",
                "border_subtle": "#42454b", "separator": "#42454b", "primary_soft": "#283b51",
            }
            with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                self.assertTrue(runtime.apply_profile({"id": "qa-" + theme, "name": theme,
                    "base": DEFAULT_THEME_ID, "overrides": {"tokens": tokens}}, reason="tool-wait-qa"), runtime.last_error)
            host = QWidget()
            host.setObjectName("ToolTaskPreview")
            host.setStyleSheet(f"QWidget#ToolTaskPreview {{ background: {DesignTokens.bg_main}; }}")
            layout = QVBoxLayout(host)
            title = QLabel("工具等待与结果")
            title.setStyleSheet(f"color: {DesignTokens.text_primary};")
            layout.addWidget(title)
            cards = []
            for status in ("running", "queued", "completed", "unknown"):
                card = ToolCallCard("search_web", {"query": "收集项目资料"}, status, parent=host)
                pending = {"background_tool": True, "status": "running" if status not in {"running", "queued"} else status,
                           "task_id": status, "elapsed_seconds": 125}
                card.set_result(json.dumps(pending), pending)
                if status == "completed":
                    card.set_result("已找到三份资料", {"content": "已找到三份资料"})
                elif status == "unknown":
                    card.set_result("已请求停止", {"status": "unknown", "content": "已请求停止，结果尚未确认。"})
                layout.addWidget(card)
                cards.append(card)
            host.resize(360, 300)
            host.show()
            self.app.processEvents()
            self.assertFalse(cards[0].is_finished)
            self.assertFalse(cards[1].is_finished)
            self.assertTrue(cards[2].is_finished)
            self.assertTrue(cards[3].failed)
            self.assertEqual(tool_result_status(cards[0].result_obj), "running")
            self.assertIn("2 分 5 秒", cards[0].args_preview_label.text())
            for card in cards:
                self.assertLessEqual(card.geometry().right(), host.width())
                self.assertGreaterEqual(card.args_preview_label.height(), card.args_preview_label.fontMetrics().height())
            selected = []
            cards[0].clicked.connect(lambda *args: selected.append(args))
            cards[0].setFocus()
            QTest.keyClick(cards[0], Qt.Key_Return)
            self.assertTrue(selected)
            directory = os.environ.get("COWORK_TOOL_WAIT_SCREENSHOTS")
            if directory:
                os.makedirs(directory, exist_ok=True)
                scale = os.environ.get("QT_SCALE_FACTOR", "1")
                self.assertTrue(host.grab().save(os.path.join(directory, f"tool-wait-{theme}-{scale}.png")))
            host.close()
            host.deleteLater()
            self.app.processEvents()

    def test_runtime_result_is_not_a_question_navigator_entry(self):
        state = SimpleNamespace(messages=[{
            "id": "completion", "role": "user", "content": "tool result",
            "meta": {"kind": "tool_completion", "hidden": True},
        }])
        self.assertEqual(MainWindow._question_navigator_entries_for_state(SimpleNamespace(), state), [])

    def test_theme_preview_does_not_change_saved_configuration(self):
        original = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        style, font = self.app.styleSheet(), self.app.font()
        registry = getattr(self.app, "theme_binding_registry", None)
        with tempfile.TemporaryDirectory() as directory:
            repository = ThemeRepository(directory)
            runtime = ThemeRuntimeManager(self.app, repository)
            card = ToolCallCard("search_web", {}, "preview")
            pending = {"background_tool": True, "status": "running", "task_id": "preview", "elapsed_seconds": 120}
            card.set_result(json.dumps(pending), pending)
            try:
                saved = repository.load()
                repository.write_preview(name="工具等待预览", overrides={"tokens": {"primary": "#265840"}},
                                         default_tokens=default_design_tokens(), session_id="tool-test")
                with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                    self.assertTrue(runtime.apply_repository_state(reason="test_preview"), runtime.last_error)
                    self.assertTrue(runtime.restore_saved_theme(), runtime.last_error)
                self.assertEqual(repository.load(), saved)
                self.assertIsNone(repository.load_preview())
                self.assertFalse(card.is_finished)
            finally:
                runtime.stop()
                card.close()
                card.deleteLater()
                self.app.theme_binding_registry = registry
                for key, value in original.items():
                    setattr(DesignTokens, key, value)
                self.app.setStyleSheet(style)
                self.app.setFont(font)


if __name__ == "__main__":
    unittest.main()
