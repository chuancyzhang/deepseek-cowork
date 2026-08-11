import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

from core.theme import default_design_tokens
from core.theme_package import build_asset_record
from core.theme_service import ThemeRepository
from main import MainWindow, ConversationSkillWizardDialog


class LinearManagementNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_sidebar_only_exposes_three_management_routes(self):
        self.assertEqual(
            set(self.window.product_nav_buttons),
            {"capabilities", "favorites", "settings"},
        )
        self.assertFalse(hasattr(self.window, "sidebar_agent_module_btn"))
        self.assertFalse(hasattr(self.window, "sidebar_memory_btn"))
        self.assertFalse(hasattr(self.window, "sidebar_skill_capture_btn"))
        self.assertTrue(hasattr(self.window, "agent_picker_btn"))

    def test_settings_and_favorites_use_main_page_stack_without_exec(self):
        with patch.object(QDialog, "exec", side_effect=AssertionError("large modal opened")):
            self.assertTrue(self.window.open_settings("个性与记忆"))
            self.assertEqual(self.window.current_product_route, "settings")
            self.assertIs(
                self.window.main_page_stack.currentWidget(),
                self.window.product_pages["settings"],
            )
            self.assertTrue(self.window.show_conversation_page())
            self.assertTrue(self.window.open_favorites())
            self.assertEqual(self.window.current_product_route, "favorites")
            self.window.show_conversation_page()
            self.window.skill_manager_ready = True
            self.assertTrue(self.window.open_skills_center())
            self.assertEqual(self.window.current_product_route, "capabilities")

    def test_sidebar_reopens_settings_after_image_theme_is_saved(self):
        with tempfile.TemporaryDirectory() as data_dir:
            repository = ThemeRepository(data_dir)
            image_path = os.path.join(data_dir, "background.png")
            image = QImage(1672, 941, QImage.Format_RGB32)
            image.fill(0xFFF8F2)
            self.assertTrue(image.save(image_path))
            record, data = build_asset_record("background", image_path)
            saved = repository.upsert_theme(
                name="图片主题",
                overrides={},
                default_tokens=default_design_tokens(),
                assets={"background": record},
                asset_bytes={record["path"]: data},
                workspace_scene={
                    "attachment": "fixed",
                    "layers": [
                        {
                            "type": "image",
                            "asset": "background",
                            "fit": "cover",
                            "opacity": 1,
                        }
                    ],
                },
            )
            repository.activate_theme(saved["theme"]["id"])
            self.window.theme_manager = SimpleNamespace(repository=repository)

            self.assertTrue(self.window.open_settings())
            self.assertEqual(self.window.current_product_route, "settings")
            self.assertIs(
                self.window.main_page_stack.currentWidget(),
                self.window.product_pages["settings"],
            )

    def test_favorite_editor_is_embedded_and_dirty_aware(self):
        self.window.open_favorites()
        self.assertTrue(self.window.show_favorite_editor())
        editor = self.window.product_pages["favorite_editor"]
        self.assertEqual(self.window.current_product_subroute, "favorite_editor")
        self.assertFalse(editor.save_btn.isEnabled())
        editor.name_input.setText("日报")
        self.app.processEvents()
        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.save_btn.isEnabled())

    def test_memory_is_a_settings_section_and_part_of_dirty_state(self):
        self.window.open_settings("个性与记忆")
        page = self.window.product_pages["settings"]
        self.assertEqual(page.nav_list.currentItem().text(), "个性与记忆")
        original = page.memory_soul_edit.toPlainText()
        page.memory_soul_edit.setPlainText(original + "\n保持直接。")
        self.app.processEvents()
        self.assertTrue(page._settings_dirty)
        self.assertTrue(page.save_settings_btn.isEnabled())

    def test_settings_navigation_compacts_below_threshold(self):
        self.window.open_settings("模型与服务")
        page = self.window.product_pages["settings"]
        self.window.resize(720, 720)
        self.window.show()
        self.app.processEvents()
        self.assertLess(page.width(), 760)
        self.assertFalse(page.nav_combo.isHidden())
        self.assertTrue(page.nav_list.isHidden())

    def test_opening_settings_does_not_probe_runtime_components(self):
        with (
            patch("main.toolkit_status", side_effect=AssertionError("toolkit status probed")),
            patch("main.node_runtime_status", side_effect=AssertionError("node status probed")),
            patch("main.browser_skill_status", side_effect=AssertionError("browser status probed")),
        ):
            self.assertTrue(self.window.open_settings("组件与依赖"))
        page = self.window.product_pages["settings"]
        self.assertTrue(
            all(row["status"].text() for row in page.component_rows.values())
        )

    def test_product_page_construction_failure_is_visible_and_logged(self):
        with (
            patch.object(
                self.window,
                "_ensure_product_page",
                side_effect=TypeError("主题状态包含不可序列化数据"),
            ),
            patch("main.log_ui_navigation") as navigation_log,
            patch.object(self.window, "add_system_toast") as toast,
        ):
            self.assertFalse(self.window.open_settings())

        navigation_log.assert_any_call(
            "product_page_open_error",
            route="settings",
            section="",
            error="主题状态包含不可序列化数据",
        )
        toast.assert_called_once()
        self.assertIn("无法打开设置", toast.call_args.args[0])

    def test_skill_wizard_preserves_message_origin_selection(self):
        messages = [
            {"id": "u1", "role": "user", "content": "整理这个流程"},
            {"id": "a1", "role": "assistant", "content": "可复用步骤"},
            {"id": "u2", "role": "user", "content": "无关消息"},
        ]
        wizard = ConversationSkillWizardDialog([], messages, selected_message_ids=["u1", "a1"])
        try:
            self.assertEqual(
                [message["id"] for message in wizard.selected_messages()],
                ["u1", "a1"],
            )
            self.assertEqual(wizard.stack.currentIndex(), 0)
            wizard._go_next()
            self.assertEqual(wizard.stack.currentIndex(), 1)
            self.assertEqual(wizard.next_btn.text(), "开始复用分析")
        finally:
            wizard.deleteLater()


if __name__ == "__main__":
    unittest.main()
