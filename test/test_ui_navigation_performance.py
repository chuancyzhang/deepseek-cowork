"""Behavioral guards for cached navigation and deferred status refreshes."""
import os
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.skill_catalog import SkillCatalogSnapshot
from main import MainWindow, SkillsCenterDialog, extract_agent_mentions
from ui.browser_automation_settings import BrowserAutomationSettings


class NavigationPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_session_inventory_uses_snapshot_and_excludes_disabled_skills(self):
        manager = MagicMock()
        manager.get_all_skills.return_value = [
            {"name": "enabled", "enabled": True}, {"name": "disabled", "enabled": False}]
        snapshot = SkillCatalogSnapshot(1, manager)
        service = MagicMock()
        service.snapshot.return_value = snapshot
        window = SimpleNamespace(skill_manager=manager, skill_manager_ready=True,
                                 skill_catalog_service=service, _flush_pending_skill_runtime_reload=lambda: None)
        window._skill_display_catalog = MethodType(MainWindow._skill_display_catalog, window)
        manager.get_all_skills.reset_mock()
        for _ in range(3):
            self.assertEqual(MainWindow._available_session_skills(window), [{"name": "enabled", "enabled": True}])
        manager.get_all_skills.assert_not_called()
        # Legacy in-place reloads must not read an outdated display snapshot.
        window._legacy_skill_display_manager = manager
        manager.get_all_skills.return_value = [{"name": "new"}]
        self.assertEqual(MainWindow._available_session_skills(window), [{"name": "new"}])
        manager.get_all_skills.assert_called_once()

    def test_plain_message_skips_profiles_but_keeps_mention_semantics(self):
        profiles = [{"id": "expert", "name": "专家"}]
        window = SimpleNamespace(_available_agent_profiles=MagicMock(return_value=profiles))
        for text in (None, "", "  普通消息\n", "含空格的 普通文本"):
            self.assertEqual(MainWindow._extract_agent_mentions(window, text), extract_agent_mentions(text, profiles))
        window._available_agent_profiles.assert_not_called()
        for text in ("@专家 请检查", "x@example.com", "@不存在 保留原文"):
            self.assertEqual(MainWindow._extract_agent_mentions(window, text), extract_agent_mentions(text, profiles))
        self.assertEqual(window._available_agent_profiles.call_count, 3)

    def test_library_refresh_is_single_flight_and_rejects_stale_results(self):
        callbacks = []
        page = SimpleNamespace(_library_refresh_pending=False, _library_revision=0,
                               skill_manager=object(), _main=SimpleNamespace(add_system_toast=MagicMock()),
                               _all_skills=[{"name": "retained"}], _apply_library_skills=MagicMock())
        page._hub_worker = lambda operation, callback: callbacks.append(callback)
        page.refresh_list_async = MethodType(SkillsCenterDialog.refresh_list_async, page)
        with patch("main._qt_object_alive", return_value=True):
            page.refresh_list_async()
            page.refresh_list_async()
            self.assertEqual(len(callbacks), 1)
            page.skill_manager = object()
            callbacks[0]({"ok": True, "data": [{"name": "stale"}]})
            page._apply_library_skills.assert_not_called()
            self.assertEqual(len(callbacks), 2)
            callbacks[1]({"ok": False, "error": "read failed"})
            self.assertFalse(page._library_refresh_pending)
            self.assertEqual(page._all_skills, [{"name": "retained"}])
            page._main.add_system_toast.assert_called_once()
            page.refresh_list_async()
            callbacks[2]({"ok": True, "data": [{"name": "fresh"}]})
            page._apply_library_skills.assert_called_once_with([{"name": "fresh"}])

    def test_unchanged_library_preserves_existing_cards(self):
        page = SimpleNamespace(_all_skills=[{"name": "same"}], _library_rendered=True,
                               _is_user_owned=lambda skill: True, _render_content=MagicMock())
        SkillsCenterDialog._apply_library_skills(page, [{"name": "same"}])
        page._render_content.assert_not_called()
        SkillsCenterDialog._apply_library_skills(page, [{"name": "changed"}])
        page._render_content.assert_called_once()
        self.assertEqual(page._user_owned_names, {"changed"})

    def test_browser_polling_stops_hidden_and_preserves_unsaved_input(self):
        coordinator = MagicMock()
        coordinator.snapshot.return_value = {"desired": "external", "backend": "external", "pending": False,
                                             "active_runs": 0, "cleanup_errors": [], "recoverable": False}
        with patch("ui.browser_automation_settings.BrowserBackendCoordinator", return_value=coordinator):
            page = BrowserAutomationSettings(MagicMock())
        try:
            self.assertFalse(page.timer.isActive())
            page.show()
            self.app.processEvents()
            self.assertTrue(page.timer.isActive())
            page.enabled.setChecked(True)
            page.hide()
            self.assertFalse(page.timer.isActive())
            coordinator.snapshot.reset_mock()
            page.timer.timeout.emit()
            coordinator.snapshot.assert_not_called()
            page.show()
            self.app.processEvents()
            self.assertTrue(coordinator.snapshot.called)
            self.assertTrue(page.enabled.isChecked())
            self.assertTrue(page.has_pending_input())
            coordinator.save.assert_not_called()
        finally:
            page.close()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
