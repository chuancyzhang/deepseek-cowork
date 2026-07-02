import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import RUN_MODE_EXECUTION
from core.config_manager import ConfigManager
from core.skill_manager import SkillManager


class TestAutomationTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _copy_repo_skill(self, skill_name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "skills", skill_name)
        target_dir = os.path.join(self.skills_dir, skill_name)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def _create_config_manager(self, payload=None):
        config_file = os.path.join(self.temp_dir, "config.json")
        if payload is not None:
            import json

            with open(config_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), patch(
            "core.config_manager.get_base_dir", return_value=self.temp_dir
        ):
            return ConfigManager()

    def _build_manager(self, config_manager):
        sm = SkillManager(workspace_dir=self.temp_dir, config_manager=config_manager)
        sm.skills_dirs = [self.skills_dir]
        sm.load_skills()
        return sm

    def test_tool_search_discovers_automation_tools(self):
        self._copy_repo_skill("automation-tools")
        cm = self._create_config_manager()
        sm = self._build_manager(cm)
        discovered = set()

        result = sm.call_tool(
            "tool_search",
            {"query": "automation task schedule"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("list_automation_tasks", discovered)
        self.assertIn("upsert_automation_task", discovered)
        self.assertIn("run_automation_task_now", discovered)

    def test_upsert_task_defaults_new_task_to_disabled(self):
        self._copy_repo_skill("automation-tools")
        cm = self._create_config_manager()
        sm = self._build_manager(cm)

        task_result = sm.call_tool(
            "upsert_automation_task",
            {
                "task": {
                    "name": "每日 9 点日报",
                    "prompt": "汇总昨天的产品进展并生成日报。",
                    "skill_names": ["browser-automation", "browser-automation", ""],
                    "schedule_type": "daily",
                    "time_of_day": "09:00",
                }
            },
            context={"config_manager": cm, "run_context": {"mode": RUN_MODE_EXECUTION}},
        )

        self.assertEqual(task_result["status"], "ok")
        self.assertFalse(task_result["item"]["enabled"])
        self.assertEqual(task_result["item"]["skill_names"], ["browser-automation"])
        stored = cm.get_automation_task("每日 9 点日报")
        self.assertFalse(stored["enabled"])
        self.assertEqual(stored["prompt"], "汇总昨天的产品进展并生成日报。")

    def test_delete_task_respects_approval(self):
        self._copy_repo_skill("automation-tools")
        cm = self._create_config_manager(
            {
                "automation_tasks": [
                    {
                        "id": "task-1",
                        "name": "日报任务",
                        "prompt": "生成日报。",
                        "schedule_type": "daily",
                        "time_of_day": "09:00",
                    }
                ],
            }
        )
        sm = self._build_manager(cm)

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": False, "status": "completed"}},
        ):
            result = sm.call_tool(
                "delete_automation_task",
                {"task_id_or_name": "日报任务"},
                context={"config_manager": cm, "run_context": {"mode": RUN_MODE_EXECUTION}},
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(cm.get_automation_task("日报任务"))

    def test_run_task_now_uses_runner_after_approval(self):
        self._copy_repo_skill("automation-tools")
        cm = self._create_config_manager(
            {
                "automation_tasks": [
                    {
                        "id": "task-1",
                        "name": "日报任务",
                        "prompt": "生成日报。",
                        "schedule_type": "daily",
                        "time_of_day": "09:00",
                    }
                ],
            }
        )
        sm = self._build_manager(cm)
        launched = []

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": True, "status": "completed"}},
        ):
            result = sm.call_tool(
                "run_automation_task_now",
                {"task_id_or_name": "日报任务"},
                context={
                    "config_manager": cm,
                    "automation_runner": lambda task_id: launched.append(task_id) or True,
                    "run_context": {"mode": RUN_MODE_EXECUTION},
                },
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["launched"])
        self.assertEqual(launched, ["task-1"])

    def test_list_history_can_filter_status(self):
        self._copy_repo_skill("automation-tools")
        cm = self._create_config_manager(
            {
                "automation_run_history": [
                    {"id": "run-1", "task_name": "日报", "status": "completed", "started_at": 20},
                    {"id": "run-2", "task_name": "巡检", "status": "error", "started_at": 10},
                ]
            }
        )
        sm = self._build_manager(cm)

        result = sm.call_tool(
            "list_automation_run_history",
            {"limit": 10, "status_filter": "completed"},
            context={"config_manager": cm, "run_context": {"mode": RUN_MODE_EXECUTION}},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["id"], "run-1")


if __name__ == "__main__":
    unittest.main()
