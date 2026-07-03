import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.automation_manager import (
    AUTOMATION_HISTORY_STATUS_MISSED,
    AUTOMATION_HISTORY_STATUS_RUNNING,
    AUTOMATION_SCHEDULE_CRON,
    AUTOMATION_SCHEDULE_DAILY,
    AUTOMATION_SCHEDULE_INTERVAL,
    AUTOMATION_SCHEDULE_MONTHLY,
    AUTOMATION_SCHEDULE_ONCE,
    AUTOMATION_SCHEDULE_WEEKLY,
    advance_task_to_next_run,
    build_automation_execution_prompt,
    compute_next_run_at,
    cron_expression_from_legacy_schedule,
    describe_schedule,
    make_automation_history_record,
    normalize_automation_history,
    normalize_automation_task,
    normalize_cron_expression,
    validate_cron_expression,
)
from core.config_manager import ConfigManager


class TestAutomationManager(unittest.TestCase):
    def test_normalize_task_keeps_prompt_skills_and_agent(self):
        task = normalize_automation_task(
            {
                "name": "每日简报",
                "prompt": "汇总今天的重要更新",
                "skill_names": ["browser-automation", "", "browser-automation"],
                "agent_profile_id": "agent-news",
                "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                "time_of_day": "07:30",
            },
            valid_agent_profile_ids=["agent-news"],
            now_ts=1716195600,
        )

        self.assertIsNotNone(task)
        self.assertTrue(task["id"].startswith("auto-"))
        self.assertEqual(task["prompt"], "汇总今天的重要更新")
        self.assertEqual(task["skill_names"], ["browser-automation"])
        self.assertEqual(task["agent_profile_id"], "agent-news")
        self.assertEqual(task["schedule_summary"], "每天 07:30")

    def test_normalize_task_requires_prompt(self):
        task = normalize_automation_task({"name": "每日简报", "prompt": ""})
        self.assertIsNone(task)

    def test_normalize_task_clears_missing_agent(self):
        task = normalize_automation_task(
            {"name": "每日简报", "prompt": "汇总", "agent_profile_id": "missing"},
            valid_agent_profile_ids=["agent-news"],
        )
        self.assertEqual(task["agent_profile_id"], "")

    def test_compute_next_run_daily(self):
        now_ts = 1716195600
        task = {"schedule_type": AUTOMATION_SCHEDULE_DAILY, "time_of_day": "07:00"}
        self.assertGreater(compute_next_run_at(task, now_ts=now_ts), now_ts)

    def test_compute_next_run_weekly(self):
        now_ts = 1716202800
        task = {
            "schedule_type": AUTOMATION_SCHEDULE_WEEKLY,
            "time_of_day": "07:00",
            "weekdays": [0, 2],
        }
        self.assertGreater(compute_next_run_at(task, now_ts=now_ts), now_ts)
        self.assertEqual(describe_schedule(task), "每周 周一/周三 07:00")

    def test_compute_next_run_monthly_clamps_day(self):
        task = {
            "schedule_type": AUTOMATION_SCHEDULE_MONTHLY,
            "time_of_day": "09:00",
            "day_of_month": 31,
        }
        self.assertTrue(compute_next_run_at(task, now_ts=1709187600))

    def test_compute_next_run_interval_uses_anchor(self):
        task = normalize_automation_task(
            {
                "name": "轮询",
                "prompt": "检查状态",
                "schedule_type": AUTOMATION_SCHEDULE_INTERVAL,
                "interval_minutes": 30,
                "interval_anchor_at": 1000,
            },
            now_ts=1000,
        )
        self.assertEqual(compute_next_run_at(task, now_ts=2500), 2800)

    def test_once_schedule_keeps_timestamp(self):
        task = normalize_automation_task(
            {
                "name": "单次",
                "prompt": "执行一次",
                "schedule_type": AUTOMATION_SCHEDULE_ONCE,
                "one_time_at": 123456,
            },
            now_ts=1716195600,
        )
        self.assertEqual(task["next_run_at"], 123456)

    def test_compute_next_run_cron(self):
        task = {"schedule_type": AUTOMATION_SCHEDULE_CRON, "cron_expression": "15 8 * * 1-5"}
        self.assertGreater(compute_next_run_at(task, now_ts=1716195600), 1716195600)
        self.assertEqual(describe_schedule(task), "Cron · 15 8 * * 1-5")

    def test_cron_validation_and_normalization(self):
        self.assertTrue(validate_cron_expression("15 8 * * 1-5"))
        self.assertFalse(validate_cron_expression("invalid cron"))
        self.assertEqual(normalize_cron_expression(""), "0 9 * * *")

    def test_cron_expression_from_legacy_weekly_schedule(self):
        expression = cron_expression_from_legacy_schedule(
            {"schedule_type": AUTOMATION_SCHEDULE_WEEKLY, "time_of_day": "07:00", "weekdays": [0, 2]}
        )
        self.assertEqual(expression, "0 7 * * 1,3")

    def test_advance_task_to_next_run_updates_timestamp(self):
        task = normalize_automation_task(
            {
                "name": "每日简报",
                "prompt": "汇总",
                "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                "time_of_day": "07:00",
            },
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

    def test_build_execution_prompt_uses_prompt_without_sop(self):
        prompt = build_automation_execution_prompt({"name": "日报", "prompt": "请汇总今天的重要更新"})
        self.assertIn("请汇总今天的重要更新", prompt)
        self.assertNotIn("SOP", prompt)
        self.assertNotIn("步骤", prompt)

    def test_history_records_agent_profile(self):
        entry = make_automation_history_record(
            {"id": "task-1", "name": "日报", "agent_profile_id": "agent-news"},
            {"id": "agent-news", "name": "新闻助手"},
            status=AUTOMATION_HISTORY_STATUS_RUNNING,
            trigger_source="scheduler",
        )
        self.assertEqual(entry["agent_profile_id"], "agent-news")
        self.assertEqual(entry["agent_profile_name"], "新闻助手")


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
                "agent_profiles": [{"id": "agent-news", "name": "新闻助手"}],
                "automation_tasks": [
                    {
                        "name": "每日简报",
                        "prompt": "汇总",
                        "agent_profile_id": "agent-news",
                        "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                        "time_of_day": "07:00",
                    }
                ],
            }
        )
        tasks = cm.get_automation_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["id"].startswith("auto-"))
        self.assertEqual(tasks[0]["agent_profile_id"], "agent-news")
        self.assertEqual(tasks[0]["schedule_summary"], "每天 07:00")

    def test_legacy_sop_tasks_are_cleared(self):
        cm = self._create_config_manager(
            {
                "sop_templates": [{"id": "office-flow", "name": "Office", "steps": [{"title": "Step 1"}]}],
                "automation_tasks": [
                    {
                        "name": "旧任务",
                        "template_id": "office-flow",
                        "schedule_type": AUTOMATION_SCHEDULE_DAILY,
                        "time_of_day": "07:00",
                    }
                ],
            }
        )
        self.assertEqual(cm.config.get("sop_templates"), [])
        tasks = cm.get_automation_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "旧任务")
        self.assertFalse(tasks[0]["enabled"])
        self.assertIn("旧版 SOP", tasks[0]["migration_note"])
        self.assertIn("请编辑任务", tasks[0]["prompt"])

    def test_append_automation_history_persists_entry(self):
        cm = self._create_config_manager()
        entry = make_automation_history_record(
            {"id": "task-1", "name": "日报", "agent_profile_id": "agent-news"},
            {"id": "agent-news", "name": "新闻助手"},
            status=AUTOMATION_HISTORY_STATUS_RUNNING,
            trigger_source="scheduler",
        )
        cm.append_automation_run_history(entry)
        history = cm.get_automation_run_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["task_id"], "task-1")
        self.assertEqual(history[0]["agent_profile_name"], "新闻助手")


if __name__ == "__main__":
    unittest.main()
