import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.config_manager import ConfigManager
from main import (
    FAVORITE_EXECUTION_CHAT,
    FAVORITE_EXECUTION_WORKSPACE,
    FavoriteEditorPage,
    MainWindow,
)


class FavoritesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = os.path.join(self.temp.name, "project")
        os.makedirs(self.workspace)

    def tearDown(self):
        self.temp.cleanup()
        self.app.processEvents()

    def _create_window(self):
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp.name), patch(
            "core.config_manager.get_base_dir", return_value=self.temp.name
        ):
            manager = ConfigManager()
        return MainWindow(config_manager=manager)

    def test_chat_editor_hides_workspace_and_does_not_serialize_it(self):
        editor = FavoriteEditorPage(
            skills=[{"name": "visualize", "display_name": "数据可视化"}],
            projects=[{"name": "项目", "path": self.workspace}],
            prefill={
                "name": "研究模式",
                "prompt": "研究主题",
                "execution_mode": FAVORITE_EXECUTION_CHAT,
                "workspace_dir": self.workspace,
            },
        )
        try:
            self.assertTrue(editor.workspace_combo.isHidden())
            self.assertTrue(editor.workspace_label.isHidden())
            self.assertEqual(editor.favorite_payload()["workspace_dir"], "")

            editor.execution_mode_combo.setCurrentIndex(
                editor.execution_mode_combo.findData(FAVORITE_EXECUTION_WORKSPACE)
            )
            editor.workspace_combo.setCurrentIndex(editor.workspace_combo.findData(self.workspace))
            self.assertFalse(editor.workspace_combo.isHidden())
            self.assertEqual(editor.favorite_payload()["workspace_dir"], os.path.normpath(self.workspace))
        finally:
            editor.deleteLater()

    def test_missing_ability_can_be_removed_instead_of_blocking_editor(self):
        editor = FavoriteEditorPage(
            favorite={"id": "fav-1", "name": "旧组合", "skill_names": ["removed-skill"]}
        )
        try:
            item = editor.skill_list.item(0)
            self.assertTrue(bool(item.flags() & Qt.ItemIsEnabled))
            self.assertEqual(item.checkState(), Qt.Checked)
            item.setCheckState(Qt.Unchecked)
            editor.prompt_edit.setPlainText("替换后的提示词")
            self.assertEqual(editor.favorite_payload()["skill_names"], [])
        finally:
            editor.deleteLater()

    def test_dirty_signature_is_stable_without_user_changes(self):
        editor = FavoriteEditorPage(
            favorite={
                "id": "fav-1",
                "name": "日报",
                "prompt": "生成日报",
                "schedule": {"schedule_type": "daily", "time_of_day": "09:00"},
            }
        )
        try:
            self.assertEqual(editor._signature(), editor._baseline)
            self.assertFalse(editor.is_dirty())
        finally:
            editor.deleteLater()

    def test_composer_prefill_only_carries_real_project_workspace(self):
        window = self._create_window()
        try:
            state = window.get_current_session()
            window.input_field.setPlainText("生成一份周报")
            state.selected_skill_names = ["visualize"]
            captured = []
            with patch.object(window, "show_product_page", return_value=True), patch.object(
                window,
                "show_favorite_editor",
                side_effect=lambda **kwargs: captured.append(kwargs.get("prefill")) or True,
            ):
                self.assertTrue(window.save_composer_as_favorite())
            self.assertEqual(captured[-1]["execution_mode"], FAVORITE_EXECUTION_CHAT)
            self.assertEqual(captured[-1]["workspace_dir"], "")
            self.assertEqual(window.input_field.toPlainText(), "生成一份周报")

            window._set_session_workspace(state, self.workspace, source="project")
            with patch.object(window, "show_product_page", return_value=True), patch.object(
                window,
                "show_favorite_editor",
                side_effect=lambda **kwargs: captured.append(kwargs.get("prefill")) or True,
            ):
                self.assertTrue(window.save_composer_as_favorite())
            self.assertEqual(captured[-1]["execution_mode"], FAVORITE_EXECUTION_WORKSPACE)
            self.assertEqual(captured[-1]["workspace_dir"], os.path.normpath(self.workspace))
        finally:
            window.close()
            window.deleteLater()

    def test_prompt_favorite_launches_new_main_agent_chat_without_project(self):
        window = self._create_window()
        try:
            saved = window.config_manager.upsert_favorite(
                {
                    "id": "fav-chat",
                    "name": "快速研究",
                    "prompt": "研究这个主题",
                    "execution_mode": FAVORITE_EXECUTION_CHAT,
                }
            )
            existing_ids = set(window.sessions)
            with patch.object(window, "_submit_session_request", return_value=True) as submit:
                self.assertTrue(window.launch_favorite(saved["id"], source="test"))
            new_ids = set(window.sessions) - existing_ids
            self.assertEqual(len(new_ids), 1)
            state = window.get_session(new_ids.pop())
            self.assertEqual(state.workspace_source, "chat")
            self.assertNotEqual(os.path.normpath(state.workspace_dir), os.path.normpath(self.workspace))
            self.assertEqual(submit.call_args.args[1], "研究这个主题")
        finally:
            window.close()
            window.deleteLater()

    def test_scheduled_run_stays_in_background_and_records_terminal_error(self):
        window = self._create_window()
        try:
            saved = window.config_manager.upsert_favorite(
                {
                    "id": "fav-scheduled",
                    "name": "后台日报",
                    "prompt": "生成日报",
                    "execution_mode": FAVORITE_EXECUTION_CHAT,
                    "schedule": {
                        "enabled": True,
                        "schedule_type": "daily",
                        "time_of_day": "09:00",
                    },
                }
            )
            original_session_id = window.current_session_id
            with patch.object(window, "_submit_session_request", return_value=True):
                self.assertTrue(
                    window._trigger_favorite_schedule(saved["id"], trigger_source="scheduler", scheduled_at=123)
                )
            self.assertEqual(window.current_session_id, original_session_id)
            running = window.config_manager.get_favorite_run_history()[0]
            self.assertEqual(running["status"], "running")
            state = window.get_session(running["session_id"])
            window.set_session_status("error", state.session_id, error="provider unavailable")
            finished = window.config_manager.get_favorite_run_history()[0]
            self.assertEqual(finished["status"], "error")
            self.assertEqual(finished["error"], "provider unavailable")
        finally:
            window.close()
            window.deleteLater()

    def test_missed_schedule_is_logged_and_not_backfilled(self):
        window = self._create_window()
        try:
            window.config_manager.upsert_favorite(
                {
                    "id": "fav-missed",
                    "name": "错过的日报",
                    "prompt": "生成日报",
                    "schedule": {
                        "enabled": True,
                        "schedule_type": "daily",
                        "time_of_day": "09:00",
                        "next_run_at": 1716190000,
                    },
                }
            )
            with patch("main.time.time", return_value=1716200000), patch.object(
                window, "_trigger_favorite_schedule"
            ) as trigger:
                window.check_favorite_schedules()
            trigger.assert_not_called()
            history = window.config_manager.get_favorite_run_history()
            self.assertEqual(history[0]["status"], "missed")
            self.assertIn("跳过", history[0]["summary"])
            self.assertGreater(window.config_manager.get_favorite("fav-missed")["schedule"]["next_run_at"], 1716200000)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
