import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import create_sop_run, mark_step_awaiting_confirmation
from main import MainWindow


class _State:
    def __init__(self, sop_run=None):
        self.sop_run = sop_run


class TestSopUiHelpers(unittest.TestCase):
    def test_prompt_tool_menu_order_helper_matches_spec(self):
        window = object.__new__(MainWindow)
        entries = window._prompt_tool_menu_entries()
        self.assertEqual(
            [label for _key, label in entries],
            ["添加文件", "添加智能体", "添加 SOP", "指定能力", "反问模式"],
        )

    def test_should_block_send_for_sop_only_when_awaiting_confirmation(self):
        window = object.__new__(MainWindow)
        active_run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "steps": [{"title": "Step 1"}],
            }
        )
        awaiting_run = mark_step_awaiting_confirmation(active_run, {"finished_at": 1})

        self.assertFalse(window._should_block_send_for_sop(_State(active_run)))
        self.assertTrue(window._should_block_send_for_sop(_State(awaiting_run)))


if __name__ == "__main__":
    unittest.main()
