import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

import main as main_module
from main import (
    SubAgentEventSummaryRow,
    SubAgentMonitor,
    SubAgentTimelineCard,
    ToolCallCard,
)


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

    def test_new_cards_and_event_rows_have_parents_at_construction(self):
        construction_states = []

        def build_card(*args, **kwargs):
            card = SubAgentTimelineCard(*args, **kwargs)
            construction_states.append(
                ("card", card.parentWidget(), card.isWindow())
            )
            return card

        def build_event_row(*args, **kwargs):
            row = SubAgentEventSummaryRow(*args, **kwargs)
            construction_states.append(
                ("event", row.parentWidget(), row.isWindow())
            )
            return row

        with (
            patch.object(main_module, "SubAgentTimelineCard", side_effect=build_card),
            patch.object(main_module, "SubAgentEventSummaryRow", side_effect=build_event_row),
        ):
            self.monitor.update_log(
                "agent-parented",
                "event-parented",
                "running",
                agent_name="Parented Agent",
            )

        self.assertEqual([item[0] for item in construction_states], ["card", "event"])
        self.assertIs(construction_states[0][1], self.monitor.content)
        self.assertIs(construction_states[1][1], self.monitor.agents["agent-parented"])
        self.assertFalse(construction_states[0][2])
        self.assertFalse(construction_states[1][2])

    def test_inline_tool_card_agent_row_has_parent_at_construction(self):
        tool_card = ToolCallCard("spawn_agent", {}, "tool-parented")
        self.host.layout().addWidget(tool_card)
        construction_states = []
        real_widget = main_module.QWidget

        def build_widget(*args, **kwargs):
            widget = real_widget(*args, **kwargs)
            construction_states.append((widget.parentWidget(), widget.isWindow()))
            return widget

        with patch.object(main_module, "QWidget", side_effect=build_widget):
            tool_card.update_agent_state(
                {
                    "agent_id": "inline-parented",
                    "agent_name": "Inline Parented Agent",
                    "status": "running",
                }
            )

        self.assertEqual(len(construction_states), 1)
        self.assertIs(construction_states[0][0], tool_card.sub_agents_container)
        self.assertFalse(construction_states[0][1])

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
