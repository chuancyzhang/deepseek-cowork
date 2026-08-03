import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from main import (
    BROWSER_SKILL_COMPONENT_ID,
    BROWSER_SKILL_EXTENSION_URL,
    CapabilityWorkbenchDialog,
    ComponentTaskManager,
    MainWindow,
    RuntimeComponentWorker,
    SettingsDialog,
    SkillsCenterDialog,
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
                "bundled_extension_available": True,
                "bundle_error": "",
                "extension_prepared": False,
                "extension_path": "",
                "available_browsers": [
                    {
                        "id": "edge",
                        "name": "Microsoft Edge",
                        "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                        "extensions_url": "edge://extensions/",
                    }
                ],
            }
            manager.status = connected_status
            manager.component_status_changed.emit(
                BROWSER_SKILL_COMPONENT_ID,
                connected_status,
            )
            self.app.processEvents()
            self.assertEqual(page.browser_offline_btn.text(), "离线安装扩展")
            self.assertEqual(page.browser_store_btn.text(), "Chrome Web Store")
            self.assertEqual(page.browser_setup_secondary_btn.text(), "检查并开启")
            self.assertFalse(page.browser_offline_btn.isEnabled())

            page.browser_choice_combo.setCurrentIndex(1)
            self.assertEqual(page.browser_choice_combo.currentData(), "edge")
            self.assertTrue(page.browser_offline_btn.isEnabled())
            page.browser_offline_btn.click()
            self.assertIn(
                ("prepare_extension", BROWSER_SKILL_COMPONENT_ID),
                manager.enqueued,
            )

            with patch("main.QDesktopServices.openUrl", return_value=True) as open_url:
                page.browser_store_btn.click()
            open_url.assert_called_once()
            self.assertEqual(
                open_url.call_args.args[0].toString(),
                BROWSER_SKILL_EXTENSION_URL,
            )
            self.app.processEvents()
            self.assertEqual(page.browser_setup_primary_btn.text(), "检查并开启")
            page.browser_setup_primary_btn.click()
            self.assertIn(("check", BROWSER_SKILL_COMPONENT_ID), manager.enqueued)
        finally:
            page.deleteLater()
            parent.deleteLater()

    def test_prepare_extension_worker_uses_background_component_action(self):
        worker = RuntimeComponentWorker(
            "prepare_extension",
            BROWSER_SKILL_COMPONENT_ID,
        )
        results = []
        worker.finished_signal.connect(results.append)
        with patch(
            "main.browser_skill_status",
            return_value={"installed": True, "needs_update": False, "needs_repair": False},
        ), patch(
            "main.prepare_browser_skill_extension",
            return_value={"installed": True, "extension_prepared": True},
        ) as prepare:
            worker.run()
        prepare.assert_called_once()
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[0]["result"]["extension_prepared"])

    def test_offline_finish_opens_selected_edge_and_stable_directory(self):
        status = {
            "known": True,
            "installed": True,
            "ready": False,
            "state": "cli_installed",
            "bundled_extension_available": True,
            "extension_prepared": False,
            "extension_path": "",
            "available_browsers": [
                {
                    "id": "edge",
                    "name": "Microsoft Edge",
                    "path": r"C:\edge.exe",
                    "extensions_url": "edge://extensions/",
                }
            ],
        }
        manager = _FakeComponentManager(status)
        parent = QWidget()
        parent.component_task_manager = manager
        page = CapabilityWorkbenchDialog(
            self._browser_skill(),
            MagicMock(),
            MagicMock(),
            parent,
            simple_mode=True,
        )
        try:
            page.browser_choice_combo.setCurrentIndex(1)
            page.browser_pending_browser_id = "edge"
            prepared = dict(status)
            prepared.update({
                "extension_prepared": True,
                "extension_prepared_version": "0.1.4",
                "extension_path": r"C:\AppData\BrowserSkill\extension",
            })
            folder_result = MagicMock(ok=True, error="")
            with patch(
                "main.launch_browser_skill_extension_manager",
                return_value={"ok": True},
            ) as launch, patch(
                "main.reveal_path_in_file_manager",
                return_value=folder_result,
            ) as reveal:
                page._handle_browser_component_finished(
                    BROWSER_SKILL_COMPONENT_ID,
                    "prepare_extension",
                    {"ok": True, "result": prepared},
                )
            launch.assert_called_once_with("edge")
            reveal.assert_called_once_with(r"C:\AppData\BrowserSkill\extension")
            self.assertFalse(page.browser_extension_guidance.isHidden())
            self.assertEqual(page.browser_setup_primary_btn.text(), "检查并开启")
        finally:
            page.deleteLater()
            parent.deleteLater()

    def test_copy_path_uses_prepared_extension_directory(self):
        status = {
            "known": True,
            "installed": True,
            "ready": False,
            "bundled_extension_available": True,
            "extension_prepared": True,
            "extension_path": r"C:\AppData\BrowserSkill\extension",
            "available_browsers": [],
        }
        manager = _FakeComponentManager(status)
        parent = QWidget()
        parent.component_task_manager = manager
        page = CapabilityWorkbenchDialog(
            self._browser_skill(),
            MagicMock(),
            MagicMock(),
            parent,
            simple_mode=True,
        )
        try:
            self.assertTrue(page._copy_browser_extension_path())
            self.assertEqual(
                QApplication.clipboard().text(),
                r"C:\AppData\BrowserSkill\extension",
            )
            page._refresh_browser_automation_theme()
            self.assertTrue(page.browser_copy_path_btn.styleSheet())
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

    def test_enabled_browser_card_keeps_settings_and_refreshes_stale_status(self):
        manager = _FakeComponentManager({
            "known": True,
            "installed": True,
            "ready": True,
            "version": "0.1.7",
            "expected_version": "0.1.7",
        })
        parent = QWidget()
        parent.component_task_manager = manager
        skill_manager = MagicMock()
        skill_manager.get_all_skills.return_value = [self._browser_skill(enabled=True)]
        skill_manager.is_skill_editable.return_value = False
        page = SkillsCenterDialog(skill_manager, MagicMock(), parent)
        try:
            self.app.processEvents()
            settings = [
                button
                for button in page.findChildren(QPushButton)
                if button.objectName() == "BrowserAutomationSettingsAction"
            ]
            self.assertEqual([button.text() for button in settings], ["设置"])
            self.assertIn(("probe", BROWSER_SKILL_COMPONENT_ID), manager.enqueued)
        finally:
            page.deleteLater()
            parent.deleteLater()

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
