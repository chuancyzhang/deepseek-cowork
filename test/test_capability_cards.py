import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.config_manager import ConfigManager
from main import SettingsDialog, McpServerManager, AgentProfileManager
from ui.capability_widgets import CapabilitySwitch


class TestConnectionWorker(QObject):
    __test__ = False
    finished_signal = Signal(dict)
    finished = Signal()

    def __init__(self, server, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.server = server

    def start(self):
        pass

    def isRunning(self):
        return True


class CapabilityCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target in ("get_app_data_dir", "get_base_dir"):
            p = patch("core.config_manager." + target, return_value=temp.name)
            p.start()
            self.addCleanup(p.stop)
        self.config = ConfigManager()
        self.servers = [dict(id=name, name=name, type="streamable_http",
                            url=f"https://example.invalid/{name}", enabled=False)
                        for name in ("alpha", "beta", "managed")]
        self.servers[2]["managed_by_skill"] = "sample"

    def test_search_targets_id_and_toggle_uses_save_discard_boundary(self):
        self.config.set_mcp_servers(self.servers)
        page = SettingsDialog(self.config, domain="capabilities", initial_page_label="MCP 服务")
        self.addCleanup(page.deleteLater)
        manager = page.mcp_server_manager
        manager.search_input.setText("beta")
        cards = manager.server_grid.cards
        self.assertEqual([c.property("serverId") for c in cards], ["beta"])
        cards[0].findChild(CapabilitySwitch).click()
        self.assertTrue(manager.servers[1]["enabled"])
        self.assertFalse(self.config.get_mcp_servers()[1]["enabled"])
        self.assertTrue(page._settings_dirty)
        with patch("main.ProductMessageDialog.exec_result", return_value="stay"):
            self.assertFalse(page._confirm_discard_settings())
        page.discard_page_changes()
        self.assertFalse(manager.servers[1]["enabled"])
        self.assertTrue(any(p.get("builtin") for p in page.agent_profile_manager.profiles))
        manager.server_grid.cards[0].findChild(CapabilitySwitch).click()
        page.save_settings()
        self.assertTrue(self.config.get_mcp_servers()[1]["enabled"])
        self.assertFalse(page._settings_dirty)

    def test_test_feedback_follows_server_and_exact_configuration(self):
        manager = McpServerManager(self.servers, self.config)
        self.addCleanup(manager.deleteLater)
        with patch("main.McpConnectionWorker", TestConnectionWorker):
            manager._server_action("alpha", manager.test_server)
        self.assertTrue(all(not b.isEnabled() for c in manager.server_grid.cards
                            for b in c.findChildren(QPushButton) if b.text() == "测试连接"))
        manager.server_list.setCurrentRow(1)
        manager._handle_test_result({"ok": False, "error": "连接失败"})
        states = {c.property("serverId"): c.findChild(QLabel, "CapabilityStateLabel").text()
                  for c in manager.server_grid.cards}
        self.assertIn("测试失败", states["alpha"])
        self.assertIn("未测试", states["beta"])
        manager.servers[0]["url"] += "/changed"
        manager._render_server_cards()
        self.assertIn("未测试", manager.server_grid.cards[0].findChild(QLabel, "CapabilityStateLabel").text())
        self.assertIsNone(manager.server_grid.cards[2].findChild(CapabilitySwitch))
        manager.search_input.setText("no match")
        self.assertEqual(manager.server_grid.cards, [])
        self.assertFalse(manager.server_grid.empty.isHidden())

    def test_expert_cards_open_copy_and_preserve_readonly_sources(self):
        manager = AgentProfileManager([dict(id="own", name="自己的专家", enabled=False, skill_names=["missing"])], lambda: [])
        self.addCleanup(manager.deleteLater)
        manager.resize(760, 600)
        manager.show()
        builtin = next(c for c in manager.profile_grid.cards if c.property("profileId") != "own")
        self.assertIsNone(builtin.findChild(CapabilitySwitch))
        builtin.title_button.click()
        self.assertTrue(manager.editing_profile)
        self.assertTrue(manager.name_input.isReadOnly())
        self.assertTrue(manager.leave_profile_editor())
        own = next(c for c in manager.profile_grid.cards if c.property("profileId") == "own")
        own.findChild(CapabilitySwitch).click()
        self.assertTrue(manager.get_profiles()[0]["enabled"])
        self.assertEqual(manager.get_profiles()[0]["skill_names"], ["missing"])
        builtin = next(c for c in manager.profile_grid.cards if c.property("profileId") != "own")
        builtin.findChild(QPushButton, "CapabilityCardAction").click()
        self.assertTrue(manager.editing_profile)
        self.assertFalse(manager.name_input.isReadOnly())
        self.assertFalse(manager.profiles[manager._current_index].get("builtin", False))
        self.assertTrue(manager.profile_list.isHidden())
        manager.close()

    def test_grid_reflow_preserves_keyboard_focus_and_edit_return(self):
        manager = McpServerManager(self.servers, self.config)
        self.addCleanup(manager.deleteLater)
        manager.resize(950, 650)
        manager.show()
        QTest.qWait(20)
        card = manager.server_grid.cards[1]
        card.title_button.setFocus()
        manager.resize(500, 650)
        QTest.qWait(20)
        self.assertIs(manager.server_grid.cards[1], card)
        self.assertTrue(card.title_button.hasFocus())
        self.assertEqual(manager.server_grid._columns, 1)
        card.title_button.click()
        self.assertIsNotNone(manager.editor_dialog)
        self.assertTrue(manager.confirm_editor())
        self.assertTrue(manager.server_list.isHidden())
        self.assertTrue(manager.server_grid.isVisible())
        manager.close()


if __name__ == "__main__":
    unittest.main()
