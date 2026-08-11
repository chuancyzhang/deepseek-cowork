import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.favorites_manager import (
    FAVORITE_EXECUTION_CHAT,
    FAVORITE_EXECUTION_WORKSPACE,
    FAVORITE_PROMPT_CUSTOM,
    FAVORITE_RUN_STATUS_MISSED,
    FAVORITE_RUN_STATUS_RUNNING,
    FAVORITE_SCHEDULE_CRON,
    FAVORITE_SCHEDULE_DAILY,
    FAVORITE_SCHEDULE_INTERVAL,
    FAVORITE_SCHEDULE_MONTHLY,
    FAVORITE_SCHEDULE_ONCE,
    FAVORITE_SCHEDULE_WEEKLY,
    advance_favorite_schedule,
    compute_next_run_at,
    describe_schedule,
    favorite_effective_prompt,
    make_favorite_run_record,
    migrate_automation_task,
    normalize_favorite,
    normalize_favorite_run_history,
    validate_cron_expression,
)


class TestFavoritesManager(unittest.TestCase):
    def test_prompt_only_chat_favorite_drops_workspace(self):
        favorite = normalize_favorite(
            {
                "name": "产品周报",
                "prompt": "汇总本周产品进展",
                "execution_mode": FAVORITE_EXECUTION_CHAT,
                "workspace_dir": "D:/should-not-be-carried",
            },
            now_ts=1716195600,
        )

        self.assertEqual(favorite["prompt"], "汇总本周产品进展")
        self.assertEqual(favorite["skill_names"], [])
        self.assertEqual(favorite["workspace_dir"], "")
        self.assertIsNone(favorite["schedule"])

    def test_ability_only_and_combined_favorites_are_supported(self):
        ability_only = normalize_favorite(
            {"name": "数据分析", "skill_names": ["visualize", "visualize", ""]}
        )
        combined = normalize_favorite(
            {"name": "研究", "prompt": "研究这个主题", "skill_names": ["web-search"]}
        )

        self.assertEqual(ability_only["prompt"], "")
        self.assertEqual(ability_only["skill_names"], ["visualize"])
        self.assertEqual(combined["skill_names"], ["web-search"])

    def test_favorite_requires_prompt_or_ability(self):
        with self.assertRaisesRegex(ValueError, "提示词或至少一项能力"):
            normalize_favorite({"name": "空常用"})

    def test_workspace_mode_requires_a_workspace_value(self):
        with self.assertRaisesRegex(ValueError, "必须选择工作区"):
            normalize_favorite(
                {"name": "项目检查", "prompt": "检查项目", "execution_mode": FAVORITE_EXECUTION_WORKSPACE}
            )

    def test_ability_only_schedule_requires_custom_prompt(self):
        with self.assertRaisesRegex(ValueError, "必须使用专用提示词"):
            normalize_favorite(
                {
                    "name": "能力组合",
                    "skill_names": ["visualize"],
                    "schedule": {"schedule_type": FAVORITE_SCHEDULE_DAILY, "time_of_day": "09:00"},
                }
            )

        favorite = normalize_favorite(
            {
                "name": "能力组合",
                "skill_names": ["visualize"],
                "schedule": {
                    "enabled": True,
                    "prompt_mode": FAVORITE_PROMPT_CUSTOM,
                    "custom_prompt": "生成今天的指标图",
                    "schedule_type": FAVORITE_SCHEDULE_DAILY,
                    "time_of_day": "09:00",
                },
            },
            now_ts=1716195600,
        )
        self.assertEqual(favorite_effective_prompt(favorite), "生成今天的指标图")

    def test_schedule_calculations_and_descriptions(self):
        now_ts = 1716195600
        schedules = [
            ({"schedule_type": FAVORITE_SCHEDULE_DAILY, "time_of_day": "07:00"}, "每天 07:00"),
            ({"schedule_type": FAVORITE_SCHEDULE_WEEKLY, "time_of_day": "07:00", "weekdays": [0, 2]}, "每周 周一/周三 07:00"),
            ({"schedule_type": FAVORITE_SCHEDULE_MONTHLY, "time_of_day": "09:00", "day_of_month": 31}, "每月 31 日 09:00"),
            ({"schedule_type": FAVORITE_SCHEDULE_INTERVAL, "interval_minutes": 30, "interval_anchor_at": 1000}, "每隔 30 分钟"),
            ({"schedule_type": FAVORITE_SCHEDULE_ONCE, "one_time_at": 1916195600}, "单次 ·"),
            ({"schedule_type": FAVORITE_SCHEDULE_CRON, "cron_expression": "15 8 * * 1-5"}, "Cron · 15 8 * * 1-5"),
        ]
        for schedule, expected_description in schedules:
            self.assertGreater(compute_next_run_at(schedule, now_ts=now_ts), now_ts)
            description = describe_schedule(schedule)
            if expected_description == "单次 ·":
                self.assertTrue(description.startswith(expected_description))
            else:
                self.assertEqual(description, expected_description)

    def test_interval_uses_anchor_and_advance_moves_forward(self):
        favorite = normalize_favorite(
            {
                "name": "轮询",
                "prompt": "检查状态",
                "schedule": {
                    "schedule_type": FAVORITE_SCHEDULE_INTERVAL,
                    "interval_minutes": 30,
                    "interval_anchor_at": 1000,
                },
            },
            now_ts=1000,
        )
        self.assertEqual(compute_next_run_at(favorite["schedule"], now_ts=2500), 2800)
        advanced = advance_favorite_schedule(favorite, now_ts=2500, after_ts=2500)
        self.assertEqual(advanced["schedule"]["next_run_at"], 2800)

    def test_cron_validation_is_strict(self):
        self.assertTrue(validate_cron_expression("15 8 * * 1-5"))
        self.assertFalse(validate_cron_expression("invalid cron"))
        with self.assertRaisesRegex(ValueError, "Cron 表达式无效"):
            normalize_favorite(
                {
                    "name": "错误计划",
                    "prompt": "执行",
                    "schedule": {"schedule_type": FAVORITE_SCHEDULE_CRON, "cron_expression": "invalid cron"},
                }
            )

    def test_history_normalization_orders_latest_first(self):
        history = normalize_favorite_run_history(
            [
                {"id": "a", "favorite_name": "早", "status": FAVORITE_RUN_STATUS_MISSED, "started_at": 10},
                {"id": "b", "favorite_name": "晚", "status": FAVORITE_RUN_STATUS_RUNNING, "started_at": 20},
            ]
        )
        self.assertEqual(history[0]["id"], "b")
        record = make_favorite_run_record({"id": "fav-1", "name": "日报"}, status=FAVORITE_RUN_STATUS_RUNNING)
        self.assertEqual(record["favorite_id"], "fav-1")

    def test_legacy_automation_migration_drops_agent_and_parks_missing_prompt(self):
        migrated = migrate_automation_task(
            {
                "id": "task-1",
                "name": "旧任务",
                "template_id": "old-sop",
                "agent_profile_id": "agent-old",
                "schedule_type": "manual",
            },
            now_ts=1716195600,
        )

        self.assertEqual(migrated["id"], "task-1")
        self.assertNotIn("agent_profile_id", migrated)
        self.assertFalse(migrated["schedule"]["enabled"])
        self.assertIn("旧版自动化迁移", migrated["prompt"])
        self.assertEqual(migrated["schedule"]["schedule_type"], FAVORITE_SCHEDULE_DAILY)


class TestFavoritesConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_dir = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def _create_config_manager(self, payload=None):
        config_file = os.path.join(self.temp_dir, "config.json")
        if payload is not None:
            with open(config_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), patch(
            "core.config_manager.get_base_dir", return_value=self.temp_dir
        ):
            return ConfigManager()

    def test_favorites_persist_and_history_is_bounded_to_new_schema(self):
        manager = self._create_config_manager()
        saved = manager.upsert_favorite({"name": "日报", "prompt": "生成日报"})
        manager.append_favorite_run_history(
            make_favorite_run_record(saved, status=FAVORITE_RUN_STATUS_RUNNING, session_id="session-1")
        )

        self.assertEqual(manager.get_favorite("日报")["id"], saved["id"])
        self.assertEqual(manager.get_favorite_run_history()[0]["session_id"], "session-1")

    def test_legacy_config_is_migrated_atomically_and_old_keys_are_removed(self):
        workspace = os.path.join(self.temp_dir, "project")
        os.makedirs(workspace)
        manager = self._create_config_manager(
            {
                "default_workspace": workspace,
                "automation_tasks": [
                    {
                        "id": "task-1",
                        "name": "每日简报",
                        "prompt": "汇总",
                        "agent_profile_id": "agent-news",
                        "schedule_type": "daily",
                        "time_of_day": "07:00",
                    }
                ],
                "automation_run_history": [
                    {"id": "run-1", "task_id": "task-1", "task_name": "每日简报", "status": "completed"}
                ],
            }
        )

        favorite = manager.get_favorite("task-1")
        self.assertEqual(favorite["execution_mode"], FAVORITE_EXECUTION_WORKSPACE)
        self.assertEqual(favorite["workspace_dir"], os.path.normpath(workspace))
        self.assertNotIn("agent_profile_id", favorite)
        self.assertEqual(manager.get_favorite_run_history()[0]["favorite_id"], "task-1")
        self.assertNotIn("automation_tasks", manager.config)
        self.assertNotIn("automation_run_history", manager.config)
        with open(os.path.join(self.temp_dir, "config.json"), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertIn("favorites", stored)
        self.assertNotIn("automation_tasks", stored)

    def test_explicit_favorites_win_and_stale_legacy_keys_are_dropped(self):
        manager = self._create_config_manager(
            {
                "favorites": [{"id": "fav-1", "name": "新常用", "prompt": "新提示词"}],
                "automation_tasks": [{"id": "old-1", "name": "旧任务", "prompt": "旧提示词"}],
            }
        )
        self.assertIsNotNone(manager.get_favorite("fav-1"))
        self.assertIsNone(manager.get_favorite("old-1"))
        self.assertNotIn("automation_tasks", manager.config)


if __name__ == "__main__":
    unittest.main()
