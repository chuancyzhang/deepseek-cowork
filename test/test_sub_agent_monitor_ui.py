import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from main import SubAgentMonitor


class SubAgentMonitorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        layout = QVBoxLayout(self.host)
        self.monitor = SubAgentMonitor(self.host)
        layout.addWidget(self.monitor)
        self.host.show()
        self.app.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def _render_agents(self, count, round_index=0):
        for index in range(count):
            self.monitor.update_log(
                f"agent-{index}",
                f"round-{round_index}-event-{index}",
                "running",
                agent_name=f"Agent {index}",
            )
        self.app.processEvents()

    def test_reset_keeps_retired_cards_out_of_top_level_windows(self):
        self._render_agents(4)
        retired_cards = list(self.monitor.agents.values())

        self.monitor.reset()

        top_level_widgets = set(QApplication.topLevelWidgets())
        self.assertEqual(self.monitor.agents, {})
        for card in retired_cards:
            self.assertFalse(card.isWindow())
            self.assertTrue(card.isHidden())
            self.assertIs(card.parentWidget(), self.monitor.content)
            self.assertNotIn(card, top_level_widgets)

    def test_repeated_reset_and_growth_never_promotes_cards_to_windows(self):
        for round_index, agent_count in enumerate((1, 3, 6), start=1):
            self._render_agents(agent_count, round_index=round_index)
            self.assertEqual(len(self.monitor.agents), agent_count)
            retired_cards = list(self.monitor.agents.values())

            self.monitor.reset()

            top_level_widgets = set(QApplication.topLevelWidgets())
            for card in retired_cards:
                self.assertFalse(card.isWindow())
                self.assertTrue(card.isHidden())
                self.assertNotIn(card, top_level_widgets)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
