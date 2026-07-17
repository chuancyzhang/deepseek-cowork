import unittest
from unittest.mock import MagicMock, patch

from main import MainWindow, SettingsDialog


class _FakeMessageDialog:
    def __init__(self, title, message, **kwargs):
        self.title = title
        self.message = message
        self.kwargs = kwargs

    def exec_result(self, _fallback=None):
        return "settings"


class BrowserSkillUiTests(unittest.TestCase):
    def test_enable_prompt_opens_target_component(self):
        window = MainWindow.__new__(MainWindow)
        window.open_settings = MagicMock(return_value=True)
        with patch(
            "main.browser_skill_status",
            return_value={
                "installed": False,
                "ready": False,
                "state": "not_installed",
            },
        ), patch("main.log_browser_skill_event"), patch(
            "main.ProductMessageDialog",
            _FakeMessageDialog,
        ):
            shown = MainWindow.offer_browser_skill_setup(window)
        self.assertTrue(shown)
        window.open_settings.assert_called_once_with(
            "组件与依赖",
            target_component="browser-skill",
        )

    def test_ready_component_does_not_prompt(self):
        window = MainWindow.__new__(MainWindow)
        window.open_settings = MagicMock()
        with patch(
            "main.browser_skill_status",
            return_value={
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ), patch("main.log_browser_skill_event"), patch(
            "main.ProductMessageDialog",
        ) as message_box:
            shown = MainWindow.offer_browser_skill_setup(window)
        self.assertFalse(shown)
        message_box.assert_not_called()
        window.open_settings.assert_not_called()

    def test_extension_page_open_failure_is_visible(self):
        dialog = SettingsDialog.__new__(SettingsDialog)
        with patch("main.QDesktopServices.openUrl", return_value=False), patch(
            "main.ProductMessageBox.warning",
        ) as warning:
            opened = SettingsDialog.open_browser_skill_extension_page(dialog)
        self.assertFalse(opened)
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
