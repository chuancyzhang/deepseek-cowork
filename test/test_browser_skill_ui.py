import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from main import (
    BROWSER_SKILL_COMPONENT_ID,
    CapabilityWorkbenchDialog,
    ComponentTaskManager,
    MainWindow,
    RuntimeComponentWorker,
    SettingsDialog,
)
from core.config_manager import ConfigManager


class _FakeComponentManager(QObject):
    state_changed = Signal(dict)
    component_status_changed = Signal(str, dict)
    component_task_finished = Signal(str, str, dict)

    def __init__(self, status=None):
        super().__init__()
        self.status = dict(status or {})
        self.task = {}
        self.enqueued = []

    def component_status_snapshot(self):
        return {"components": {BROWSER_SKILL_COMPONENT_ID: dict(self.status)}}

    def snapshot(self):
        return {
            "tasks": (
                {BROWSER_SKILL_COMPONENT_ID: dict(self.task)}
                if self.task
                else {}
            )
        }

    def has_task(self, component_id):
        return component_id == BROWSER_SKILL_COMPONENT_ID and bool(self.task)

    def probe_component(self, component_id):
        self.enqueued.append(("probe", component_id))
        return True

    def enqueue(self, action, component_id, source=None):
        self.enqueued.append((action, component_id))
        return True


class BrowserSkillUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _browser_skill(self, *, enabled=False):
        return {
            "name": "browser-automation",
            "display_name": "浏览器自动化",
            "source_type": "bundled_plugin",
            "enabled": enabled,
            "presentation": {
                "category": "search_browse",
                "short_name": "浏览器操作",
                "summary": "让 AI 在独立窗口中读取和操作网页。",
                "examples": ["读取登录后的网页", "填写网页表单"],
                "access_note": "可访问浏览器中的登录态和网页内容。",
            },
        }

    def test_advanced_enable_routes_to_capability_setup_instead_of_settings(self):
        window = MainWindow.__new__(MainWindow)
        window.current_product_route = MainWindow.PAGE_CAPABILITIES
        window.component_task_manager = MagicMock()
        window.component_task_manager.component_status_snapshot.return_value = {
            "components": {
                BROWSER_SKILL_COMPONENT_ID: {
                    "known": True,
                    "installed": False,
                    "ready": False,
                }
            }
        }
        skill = self._browser_skill()
        window.skill_manager = MagicMock()
        window.skill_manager.get_all_skills.return_value = [skill]
        window.show_capability_detail = MagicMock(return_value=True)
        window.add_system_toast = MagicMock()

        opened = MainWindow.open_browser_automation_setup(window)

        self.assertTrue(opened)
        window.show_capability_detail.assert_called_once_with(skill)
        self.assertFalse(hasattr(SettingsDialog, "open_browser_skill_extension_page"))

    def test_ready_browser_does_not_reopen_setup(self):
        window = MainWindow.__new__(MainWindow)
        window.current_product_route = MainWindow.PAGE_CAPABILITIES
        window.component_task_manager = MagicMock()
        window.component_task_manager.component_status_snapshot.return_value = {
            "components": {
                BROWSER_SKILL_COMPONENT_ID: {
                    "known": True,
                    "installed": True,
                    "ready": True,
                }
            }
        }
        window.skill_manager = MagicMock()
        window.show_capability_detail = MagicMock()

        opened = MainWindow.open_browser_automation_setup(window)

        self.assertFalse(opened)
        window.show_capability_detail.assert_not_called()

    def test_browser_setup_keeps_install_extension_and_check_in_capability(self):
        manager = _FakeComponentManager(
            {
                "known": True,
                "installed": False,
                "ready": False,
                "state": "not_installed",
            }
        )
        parent = QWidget()
        parent.component_task_manager = manager
        skill_manager = MagicMock()
        skill_manager.is_skill_editable.return_value = False
        page = CapabilityWorkbenchDialog(
            self._browser_skill(),
            skill_manager,
            MagicMock(),
            parent,
            simple_mode=True,
        )
        try:
            labels = [label.text() for label in page.findChildren(QLabel)]
            self.assertIn("准备浏览器自动化", labels)
            self.assertEqual(page.browser_setup_primary_btn.text(), "开始设置")
            self.assertNotIn("组件与依赖", " ".join(labels))

            connected_status = {
                "known": True,
                "installed": True,
                "ready": False,
                "state": "cli_installed",
            }
            manager.status = connected_status
            manager.component_status_changed.emit(
                BROWSER_SKILL_COMPONENT_ID,
                connected_status,
            )
            self.app.processEvents()
            self.assertEqual(page.browser_setup_primary_btn.text(), "安装浏览器扩展")
            self.assertEqual(
                page.browser_setup_secondary_btn.text(),
                "已经安装，检查并开启",
            )

            with patch("main.QDesktopServices.openUrl", return_value=True):
                page.browser_setup_primary_btn.click()
            self.app.processEvents()
            self.assertEqual(page.browser_setup_primary_btn.text(), "检查并开启")
            page.browser_setup_primary_btn.click()
            self.assertIn(("check", BROWSER_SKILL_COMPONENT_ID), manager.enqueued)
        finally:
            page.deleteLater()
            parent.deleteLater()

    def test_component_settings_refresh_excludes_browser_capability(self):
        manager = ComponentTaskManager()
        manager.enqueue = MagicMock(return_value=True)

        self.assertTrue(manager.refresh_all_component_statuses())

        component_ids = [
            call.args[1]
            for call in manager.enqueue.call_args_list
        ]
        self.assertNotIn(BROWSER_SKILL_COMPONENT_ID, component_ids)

    def test_connection_check_does_not_succeed_until_browser_is_ready(self):
        worker = RuntimeComponentWorker(
            "check",
            BROWSER_SKILL_COMPONENT_ID,
        )
        results = []
        worker.finished_signal.connect(results.append)
        with patch(
            "main.browser_skill_status",
            return_value={
                "installed": True,
                "ready": False,
                "state": "extension_disconnected",
                "health_error": "Chrome 或 Edge 扩展尚未连接。",
            },
        ):
            worker.run()

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(
            results[0]["result"]["state"],
            "extension_disconnected",
        )

    def test_component_settings_no_longer_exposes_browser_maintenance_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir",
            return_value=temp_dir,
        ), patch(
            "core.config_manager.get_base_dir",
            return_value=temp_dir,
        ):
            config = ConfigManager()
        page = SettingsDialog(config, initial_page_label="组件与依赖")
        try:
            visible_copy = " ".join(
                [label.text() for label in page.findChildren(QLabel)]
                + [button.text() for button in page.findChildren(QPushButton)]
            )
            self.assertNotIn("可选浏览器能力", visible_copy)
            self.assertNotIn("Tencent BrowserSkill", visible_copy)
            self.assertNotIn("安装浏览器扩展", visible_copy)
        finally:
            if hasattr(page, "_im_gateway_status_timer"):
                page._im_gateway_status_timer.stop()
            page._allow_close_without_prompt = True
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
