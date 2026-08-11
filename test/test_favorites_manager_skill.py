import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import RUN_MODE_EXECUTION
from core.config_manager import ConfigManager
from core.favorites_manager import make_favorite_run_record
from core.skill_manager import SkillManager


class TestFavoritesManagerSkill(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_dir = self.temp.name
        self.skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _copy_repo_skill(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "skills", "favorites-manager")
        target_dir = os.path.join(self.skills_dir, "favorites-manager")
        shutil.copytree(source_dir, target_dir)
        dummy_dir = os.path.join(self.skills_dir, "visualize")
        os.makedirs(dummy_dir)
        with open(os.path.join(dummy_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write("---\nname: visualize\ndescription: Build visualizations for tests.\n---\n\n# Visualize\n")

    def _create_config_manager(self, payload=None):
        config_file = os.path.join(self.temp_dir, "config.json")
        if payload is not None:
            with open(config_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), patch(
            "core.config_manager.get_base_dir", return_value=self.temp_dir
        ):
            return ConfigManager()

    def _build_manager(self, config_manager):
        manager = SkillManager(workspace_dir=self.temp_dir, config_manager=config_manager)
        manager.skills_dirs = [self.skills_dir]
        manager.skill_root_kinds = {os.path.abspath(self.skills_dir): "optional"}
        manager.load_skills()
        return manager

    def _context(self, config_manager):
        return {"config_manager": config_manager, "run_context": {"mode": RUN_MODE_EXECUTION}}

    def test_tool_search_discovers_favorites_tools_and_not_legacy_automation_tools(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager()
        skill_manager = self._build_manager(config_manager)
        discovered = set()

        result = skill_manager.call_tool(
            "tool_search",
            {"query": "保存常用提示词和能力组合并定时运行"},
            context={"run_context": {"mode": RUN_MODE_EXECUTION}, "discovered_tool_names": discovered},
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("list_favorites", discovered)
        self.assertIn("upsert_favorite", discovered)
        self.assertIn("launch_favorite", discovered)
        self.assertNotIn("list_automation_tasks", skill_manager.tools)

    def test_upsert_supports_prompt_only_chat_and_clears_workspace(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager()
        skill_manager = self._build_manager(config_manager)

        result = skill_manager.call_tool(
            "upsert_favorite",
            {
                "favorite": {
                    "name": "产品周报",
                    "prompt": "生成本周产品周报",
                    "execution_mode": "chat",
                    "workspace_dir": "D:/must-not-be-carried",
                }
            },
            context=self._context(config_manager),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["item"]["workspace_dir"], "")
        self.assertEqual(config_manager.get_favorite("产品周报")["prompt"], "生成本周产品周报")

    def test_upsert_supports_ability_only_favorite(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager()
        skill_manager = self._build_manager(config_manager)

        result = skill_manager.call_tool(
            "upsert_favorite",
            {"favorite": {"name": "图表模式", "skill_names": ["visualize"]}},
            context=self._context(config_manager),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["item"]["prompt"], "")
        self.assertEqual(result["item"]["skill_names"], ["visualize"])

    def test_new_schedule_defaults_to_paused_and_ability_only_requires_custom_prompt(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager(
            {"favorites": [{"id": "fav-1", "name": "图表模式", "skill_names": ["visualize"]}]}
        )
        skill_manager = self._build_manager(config_manager)

        invalid = skill_manager.call_tool(
            "configure_favorite_schedule",
            {"favorite_id_or_name": "fav-1", "schedule": {"schedule_type": "daily", "time_of_day": "09:00"}},
            context=self._context(config_manager),
        )
        self.assertEqual(invalid["status"], "error")
        self.assertIn("专用提示词", invalid["error"])

        valid = skill_manager.call_tool(
            "configure_favorite_schedule",
            {
                "favorite_id_or_name": "fav-1",
                "schedule": {
                    "schedule_type": "daily",
                    "time_of_day": "09:00",
                    "prompt_mode": "custom",
                    "custom_prompt": "生成每日指标图",
                },
            },
            context=self._context(config_manager),
        )
        self.assertEqual(valid["status"], "ok")
        self.assertFalse(valid["item"]["schedule"]["enabled"])

    def test_workspace_mode_surfaces_missing_directory(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager()
        skill_manager = self._build_manager(config_manager)

        result = skill_manager.call_tool(
            "upsert_favorite",
            {
                "favorite": {
                    "name": "项目检查",
                    "prompt": "检查项目",
                    "execution_mode": "workspace",
                    "workspace_dir": os.path.join(self.temp_dir, "missing"),
                }
            },
            context=self._context(config_manager),
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("工作区不存在", result["error"])

    def test_delete_respects_approval_and_removes_history_after_confirmation(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager(
            {"favorites": [{"id": "fav-1", "name": "日报", "prompt": "生成日报"}]}
        )
        favorite = config_manager.get_favorite("fav-1")
        config_manager.append_favorite_run_history(make_favorite_run_record(favorite))
        skill_manager = self._build_manager(config_manager)

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": False, "status": "completed"}},
        ):
            cancelled = skill_manager.call_tool(
                "delete_favorite", {"favorite_id_or_name": "fav-1"}, context=self._context(config_manager)
            )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(config_manager.get_favorite("fav-1"))

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": True, "status": "completed"}},
        ):
            deleted = skill_manager.call_tool(
                "delete_favorite", {"favorite_id_or_name": "fav-1"}, context=self._context(config_manager)
            )
        self.assertEqual(deleted["status"], "ok")
        self.assertIsNone(config_manager.get_favorite("fav-1"))
        self.assertEqual(config_manager.get_favorite_run_history(), [])

    def test_launch_returns_client_action_after_approval(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager(
            {"favorites": [{"id": "fav-1", "name": "日报", "prompt": "生成日报"}]}
        )
        skill_manager = self._build_manager(config_manager)

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": True, "status": "completed"}},
        ):
            result = skill_manager.call_tool(
                "launch_favorite", {"favorite_id_or_name": "日报"}, context=self._context(config_manager)
            )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["client_action"], {"type": "launch_favorite", "favorite_id": "fav-1"})

    def test_list_history_filters_by_favorite_and_status(self):
        self._copy_repo_skill()
        config_manager = self._create_config_manager(
            {
                "favorites": [
                    {"id": "fav-1", "name": "日报", "prompt": "生成日报"},
                    {"id": "fav-2", "name": "巡检", "prompt": "巡检"},
                ],
                "favorite_run_history": [
                    {"id": "run-1", "favorite_id": "fav-1", "favorite_name": "日报", "status": "completed", "started_at": 20},
                    {"id": "run-2", "favorite_id": "fav-2", "favorite_name": "巡检", "status": "error", "started_at": 10},
                ],
            }
        )
        skill_manager = self._build_manager(config_manager)

        result = skill_manager.call_tool(
            "list_favorite_run_history",
            {"favorite_id_or_name": "日报", "limit": 10, "status_filter": "completed"},
            context=self._context(config_manager),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["id"], "run-1")


if __name__ == "__main__":
    unittest.main()
