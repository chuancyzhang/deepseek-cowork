import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.clarify_mode import RUN_MODE_EXECUTION
from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.theme_service import ThemeRepository


class ThemeToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shutil.copytree(
            os.path.join(repo_root, "skills", "theme-customizer"),
            os.path.join(self.skills_dir, "theme-customizer"),
        )
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), patch(
            "core.config_manager.get_base_dir", return_value=self.temp_dir
        ):
            self.config_manager = ConfigManager()
        self.manager = SkillManager(
            workspace_dir=self.temp_dir,
            config_manager=self.config_manager,
        )
        self.manager.skills_dirs = [self.skills_dir]
        self.manager.load_skills()
        self.context = {
            "config_manager": self.config_manager,
            "run_context": {"mode": RUN_MODE_EXECUTION},
            "session_id": "session-1",
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_search_discovers_theme_capability(self):
        discovered = set()
        result = self.manager.call_tool(
            "tool_search",
            {"query": "customize UI theme colors font"},
            context={
                **self.context,
                "discovered_tool_names": discovered,
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("inspect_ui_theme", discovered)
        self.assertIn("preview_ui_theme", discovered)
        self.assertIn("save_ui_theme_preview", discovered)

    def test_preview_then_approved_save_uses_shared_repository(self):
        preview = self.manager.call_tool(
            "preview_ui_theme",
            {
                "name": "AI 工作主题",
                "overrides": {"tokens": {"primary": "#3366cc"}},
            },
            context=self.context,
        )
        self.assertEqual(preview["status"], "ok")
        repository = ThemeRepository(self.temp_dir)
        self.assertEqual(repository.load_preview()["preview_id"], preview["preview_id"])

        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": True, "status": "completed"}},
        ):
            saved = self.manager.call_tool(
                "save_ui_theme_preview",
                {
                    "preview_id": preview["preview_id"],
                    "preview_revision": preview["preview_revision"],
                    "activate": True,
                },
                context=self.context,
            )
        self.assertEqual(saved["status"], "ok")
        self.assertEqual(repository.load().active_theme_id, saved["theme"]["id"])

    def test_cancelled_save_keeps_preview(self):
        preview = self.manager.call_tool(
            "preview_ui_theme",
            {"name": "Draft", "overrides": {}},
            context=self.context,
        )
        with patch(
            "skills.interaction.impl.request_user_approval",
            return_value={"interaction_response": {"approved": False, "status": "completed"}},
        ):
            saved = self.manager.call_tool(
                "save_ui_theme_preview",
                {
                    "preview_id": preview["preview_id"],
                    "preview_revision": preview["preview_revision"],
                },
                context=self.context,
            )
        self.assertEqual(saved["status"], "cancelled")
        self.assertIsNotNone(ThemeRepository(self.temp_dir).load_preview())

    def test_patch_preview_increments_revision_and_stale_save_is_rejected(self):
        preview = self.manager.call_tool(
            "preview_ui_theme",
            {"name": "Patch", "overrides": {"tokens": {"primary": "#3366cc"}}},
            context=self.context,
        )
        patched = self.manager.call_tool(
            "patch_ui_theme_preview",
            {
                "preview_id": preview["preview_id"],
                "preview_revision": preview["preview_revision"],
                "set_overrides": {"tokens": {"bg_chat": "#101010"}},
                "unset_tokens": ["primary"],
            },
            context=self.context,
        )
        self.assertEqual(patched["preview_revision"], preview["preview_revision"] + 1)
        stale = self.manager.call_tool(
            "save_ui_theme_preview",
            {
                "preview_id": preview["preview_id"],
                "preview_revision": preview["preview_revision"],
            },
            context=self.context,
        )
        self.assertEqual(stale["status"], "error")

    def test_delete_default_is_rejected(self):
        result = self.manager.call_tool(
            "delete_ui_theme",
            {"theme_id": "default"},
            context=self.context,
        )
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
