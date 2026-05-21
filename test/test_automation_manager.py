import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.automation_manager import (
    AUTOMATION_HISTORY_STATUS_MISSED,
    AUTOMATION_HISTORY_STATUS_RUNNING,
    AUTOMATION_SCHEDULE_DAILY,
    AUTOMATION_SCHEDULE_INTERVAL,
    AUTOMATION_SCHEDULE_MONTHLY,
    AUTOMATION_SCHEDULE_ONCE,
    AUTOMATION_SCHEDULE_WEEKLY,
    advance_task_to_next_run,
    build_automation_execution_prompt,
    compute_next_run_at,
    describe_schedule,
    make_automation_history_record,
    normalize_automation_history,
    normalize_automation_task,
)
from core.config_manager import ConfigManager


class TestAutomationManager(unittest.TestCase):
    def test_normalize_task_auto_generates_id_and_summary(self):
        task = normalize_automation_task(
            {
                "name": "每日简报",
                "template_id": "tpl-1",
                "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                "time_of_day": "07:30",
            },
            valid_template_ids=["tpl-1"],
            now_ts=1716195600,
        )

        self.assertIsNotNone(task)
        self.assertTrue(task["id"].startswith("auto-"))
        self.assertEqual(task["schedule_summary"], "每天 07:30")

    def test_compute_next_run_daily(self):
        now_ts = 1716195600  # 2024-05-20 06:20:00 local
        task = {
            "schedule_type": AUTOMATION_SCHEDULE_DAILY,
            "time_of_day": "07:00",
        }
        next_run = compute_next_run_at(task, now_ts=now_ts)
        self.assertGreater(next_run, now_ts)

    def test_compute_next_run_weekly(self):
        now_ts = 1716202800  # 2024-05-20 08:20:00 local, Monday
        task = {
            "schedule_type": AUTOMATION_SCHEDULE_WEEKLY,
            "time_of_day": "07:00",
            "weekdays": [0, 2],
        }
        next_run = compute_next_run_at(task, now_ts=now_ts)
        self.assertGreater(next_run, now_ts)
        self.assertEqual(describe_schedule(task), "每周 周一/周三 07:00")

    def test_compute_next_run_monthly_clamps_day(self):
        task = {
            "schedule_type": AUTOMATION_SCHEDULE_MONTHLY,
            "time_of_day": "09:00",
            "day_of_month": 31,
        }
        next_run = compute_next_run_at(task, now_ts=1709187600)  # 2024-02-29 09:00
        self.assertTrue(next_run)

    def test_compute_next_run_interval_uses_anchor(self):
        task = normalize_automation_task(
            {
                "name": "轮询",
                "template_id": "tpl-1",
                "schedule_type": AUTOMATION_SCHEDULE_INTERVAL,
                "interval_minutes": 30,
                "interval_anchor_at": 1000,
            },
            valid_template_ids=["tpl-1"],
            now_ts=1000,
        )
        next_run = compute_next_run_at(task, now_ts=2500)
        self.assertEqual(next_run, 2800)

    def test_once_schedule_keeps_timestamp(self):
        task = normalize_automation_task(
            {
                "name": "单次",
                "template_id": "tpl-1",
                "schedule_type": AUTOMATION_SCHEDULE_ONCE,
                "one_time_at": 123456,
            },
            valid_template_ids=["tpl-1"],
            now_ts=1716195600,
        )
        self.assertEqual(task["next_run_at"], 123456)

    def test_advance_task_to_next_run_updates_timestamp(self):
        task = normalize_automation_task(
            {
                "name": "每日简报",
                "template_id": "tpl-1",
                "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                "time_of_day": "07:00",
            },
            valid_template_ids=["tpl-1"],
            now_ts=1716195600,
        )
        advanced = advance_task_to_next_run(task, now_ts=1716199200, after_ts=1716200000)
        self.assertGreater(advanced["next_run_at"], 1716200000)

    def test_history_normalization_orders_latest_first(self):
        history = normalize_automation_history(
            [
                {"id": "a", "task_name": "早", "status": AUTOMATION_HISTORY_STATUS_MISSED, "started_at": 10},
                {"id": "b", "task_name": "晚", "status": AUTOMATION_HISTORY_STATUS_RUNNING, "started_at": 20},
            ]
        )
        self.assertEqual(history[0]["id"], "b")

    def test_build_execution_prompt_includes_steps(self):
        prompt = build_automation_execution_prompt(
            {"name": "日报", "prompt": "请汇总今天的重要更新"},
            {
                "name": "日报模板",
                "description": "生成结构化日报",
                "steps": [{"title": "收集信息", "instructions": "先读变更"}],
            },
        )
        self.assertIn("请完整执行以下模板步骤", prompt)
        self.assertIn("请汇总今天的重要更新", prompt)


class TestAutomationConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        for root, dirs, files in os.walk(self.temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.temp_dir)

    def _create_config_manager(self, payload=None):
        config_file = os.path.join(self.temp_dir, "config.json")
        if payload is not None:
            with open(config_file, "w", encoding="utf-8") as handle:
                import json

                json.dump(payload, handle)
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), patch(
            "core.config_manager.get_base_dir", return_value=self.temp_dir
        ):
            return ConfigManager()

    def test_automation_tasks_are_normalized_and_persisted(self):
        cm = self._create_config_manager(
            {
                "sop_templates": [{"id": "office-flow", "name": "Office", "steps": [{"title": "Step 1"}]}],
                "automation_tasks": [
                    {
                        "name": "每日简报",
                        "template_id": "office-flow",
                        "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                        "time_of_day": "07:00",
                    }
                ],
            }
        )
        tasks = cm.get_automation_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["id"].startswith("auto-"))
        self.assertEqual(tasks[0]["schedule_summary"], "每天 07:00")

    def test_append_automation_history_persists_entry(self):
        cm = self._create_config_manager()
        entry = make_automation_history_record(
            {"id": "task-1", "name": "日报", "template_id": "tpl-1"},
            {"name": "模板"},
            status=AUTOMATION_HISTORY_STATUS_RUNNING,
            trigger_source="scheduler",
        )
        cm.append_automation_run_history(entry)
        history = cm.get_automation_run_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
