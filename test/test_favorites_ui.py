import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

from core.config_manager import ConfigManager
from core.favorite_delivery import FavoriteDeliveryService
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository
from main import (
    FAVORITE_EXECUTION_CHAT,
    FAVORITE_EXECUTION_WORKSPACE,
    FavoriteEditorPage,
    FavoritesPage,
    MainWindow,
    MultiLineElidedLabel,
    favorite_delivery_readiness,
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

    def test_editor_binding_code_becomes_saved_delivery_target(self):
        window = self._create_window()
        editor = FavoriteEditorPage(
            favorite={
                "id": "fav-bind-ui",
                "name": "日报",
                "prompt": "生成日报",
                "schedule": {"enabled": True, "schedule_type": "daily", "time_of_day": "09:00"},
            },
            delivery_service=window.favorite_delivery_service,
            active_provider_callback=lambda: "feishu",
        )
        try:
            editor._create_delivery_binding()
            self.assertTrue(editor.delivery_binding_command.startswith("绑定常用 "))
            code = editor.delivery_binding_command.rsplit(" ", 1)[-1]
            pending = window.favorite_delivery_service.find_pending_request(code)
            window.favorite_delivery_service.claim_binding_request(
                pending["request_id"],
                "feishu",
                {"target_type": "chat_id", "target_value": "chat-ui", "display_name": "测试群"},
            )
            editor._refresh_delivery_binding_status()
            payload = editor.favorite_payload()
            self.assertTrue(payload["schedule"]["delivery"]["enabled"])
            self.assertEqual(payload["schedule"]["delivery"]["binding_id"], pending["binding_id"])
            self.assertIn("已绑定", editor.delivery_binding_status.text())
            self.assertIn("飞书", editor.delivery_binding_status.text())
        finally:
            editor.deleteLater()
            window.close()
            window.deleteLater()

    def test_editor_uses_task_first_flow_and_progressive_run_options(self):
        editor = FavoriteEditorPage(
            skills=[{"name": "visualize", "display_name": "数据可视化", "description": "生成清晰图表"}],
            projects=[{"name": "项目", "path": self.workspace}],
            prefill={"name": "周报", "prompt": "整理周报"},
        )
        try:
            self.assertTrue(editor.run_options_content.isHidden())
            self.assertEqual(editor.execution_mode_combo.currentText(), "独立聊天")
            self.assertEqual(editor.schedule_attached_check.text(), "添加定时计划")
            self.assertEqual(editor.schedule_prompt_mode_combo.itemText(0), "使用上面的任务内容")
            self.assertEqual(editor.skill_list.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertIn("数据可视化\n生成清晰图表", editor.skill_list.item(0).text())
            self.assertTrue(editor.skill_list.isHidden())
            self.assertEqual(editor.add_skills_btn.text(), "添加能力")

            editor.run_options_toggle.setChecked(True)
            editor.schedule_attached_check.setChecked(True)
            self.assertFalse(editor.run_options_content.isHidden())
            self.assertFalse(editor.schedule_card.isHidden())
            self.assertEqual(editor.schedule_attached_check.text(), "删除计划")
            self.assertIsNotNone(editor.favorite_payload()["schedule"])
        finally:
            editor.deleteLater()

    def test_favorite_cards_are_compact_and_content_sized_for_odd_and_even_counts(self):
        manager = self._create_window().config_manager
        page = None
        try:
            for count in (1, 2, 3, 5):
                manager.set_favorites([
                    {
                        "id": f"fav-{index}",
                        "name": f"任务 {index}",
                        "description": "这是一段用于验证三行说明与统一卡片高度的较长普通用户任务用途。" * 2,
                        "prompt": f"执行任务 {index}",
                    }
                    for index in range(count)
                ])
                if page is not None:
                    page.deleteLater()
                page = FavoritesPage(manager)
                page.resize(1200, 760)
                page.show()
                self.app.processEvents()
                cards = page.findChildren(type(page.container), "FavoriteCard")
                self.assertEqual(len(cards), count)
                self.assertEqual(len({card.height() for card in cards}), 1)
                self.assertTrue(all(card.height() >= page.card_height() for card in cards))
                self.assertTrue(all(card.sizePolicy().verticalPolicy() == QSizePolicy.Maximum for card in cards))
                self.assertEqual(len(page.findChildren(QWidget, "FavoriteDeleteButton")), count)
                self.assertEqual(len(page.findChildren(QWidget, "FavoriteMoreButton")), 0)
                summaries = page.findChildren(MultiLineElidedLabel, "FavoriteSummary")
                self.assertEqual(len(summaries), count)
                self.assertTrue(all(summary.height() == summary.fontMetrics().lineSpacing() * 3 for summary in summaries))

            page.resize(700, 760)
            self.app.processEvents()
            page.refresh_cards()
            self.app.processEvents()
            cards = [
                page.grid.itemAt(index).widget()
                for index in range(page.grid.count())
                if page.grid.itemAt(index).widget()
                and page.grid.itemAt(index).widget().objectName() == "FavoriteCard"
            ]
            self.assertEqual(page._grid_columns, 1)
            self.assertEqual(len(cards), 5)
            self.assertEqual(len({card.height() for card in cards}), 1)
            self.assertTrue(all(card.height() >= page.card_height() for card in cards))
        finally:
            if page is not None:
                page.deleteLater()
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, MainWindow):
                    widget.close()
                    widget.deleteLater()

    def test_favorite_value_controls_send_closed_wheel_to_page(self):
        editor = FavoriteEditorPage(
            favorite={
                "id": "fav-wheel",
                "name": "滚轮测试",
                "prompt": "执行测试",
                "schedule": {"enabled": True, "schedule_type": "monthly", "time_of_day": "09:00"},
            }
        )
        try:
            editor.resize(640, 380)
            editor.show()
            editor.run_options_toggle.setChecked(True)
            self.app.processEvents()
            scrollbar = editor.scroll.verticalScrollBar()
            self.assertGreater(scrollbar.maximum(), 0)
            controls = (
                editor.execution_mode_combo,
                editor.schedule_prompt_mode_combo,
                editor.schedule_type_combo,
                editor.monthly_day_spin,
                editor.interval_minutes_spin,
                editor.once_datetime_edit,
            )
            for control in controls:
                before = (
                    control.currentIndex() if hasattr(control, "currentIndex")
                    else control.value() if hasattr(control, "value")
                    else control.dateTime().toSecsSinceEpoch()
                )
                scrollbar.setValue(0)
                event = QWheelEvent(
                    QPointF(4, 4),
                    QPointF(4, 4),
                    QPoint(0, 0),
                    QPoint(0, -120),
                    Qt.NoButton,
                    Qt.NoModifier,
                    Qt.ScrollUpdate,
                    False,
                )
                control.wheelEvent(event)
                after = (
                    control.currentIndex() if hasattr(control, "currentIndex")
                    else control.value() if hasattr(control, "value")
                    else control.dateTime().toSecsSinceEpoch()
                )
                self.assertEqual(after, before)
                self.assertGreater(scrollbar.value(), 0)
        finally:
            editor.close()
            editor.deleteLater()

    def test_favorites_history_view_separates_run_and_delivery_status(self):
        window = self._create_window()
        page = None
        try:
            window.config_manager.set_favorites(
                [{"id": "fav-history", "name": "日报", "prompt": "生成日报"}]
            )
            window.config_manager.set_favorite_run_history(
                [{
                    "id": "run-history",
                    "favorite_id": "fav-history",
                    "favorite_name": "日报",
                    "trigger_source": "scheduler",
                    "status": "completed",
                    "started_at": 100,
                    "delivery_id": "delivery-history",
                    "delivery_status": "failed",
                    "delivery_error": "飞书渠道未连接",
                }]
            )
            page = FavoritesPage(window.config_manager, window)
            page.show_history("fav-history")
            page.show()
            self.app.processEvents()
            rows = page.findChildren(QWidget, "FavoriteHistoryRow")
            self.assertEqual(len(rows), 1)
            text = " ".join(label.text() for label in rows[0].findChildren(QWidget) if hasattr(label, "text"))
            self.assertIn("任务已完成", text)
            self.assertIn("企业消息发送失败", text)
            self.assertIn("飞书渠道未连接", text)
        finally:
            if page is not None:
                page.deleteLater()
            window.close()
            window.deleteLater()

    def test_delivery_readiness_requires_enterprise_message_configuration(self):
        window = self._create_window()
        editor = None
        try:
            state = favorite_delivery_readiness(window.config_manager)
            self.assertEqual(state["state"], "unconfigured")
            editor = FavoriteEditorPage(
                favorite={
                    "id": "fav-setup",
                    "name": "日报",
                    "prompt": "生成日报",
                    "schedule": {"enabled": True, "schedule_type": "daily", "time_of_day": "09:00"},
                },
                delivery_service=window.favorite_delivery_service,
                delivery_readiness_callback=lambda: favorite_delivery_readiness(window.config_manager),
            )
            self.assertFalse(editor.delivery_bind_btn.isEnabled())
            self.assertFalse(editor.delivery_settings_btn.isHidden())
            self.assertIn("尚未就绪", editor.delivery_binding_status.text())
        finally:
            if editor is not None:
                editor.deleteLater()
            window.close()
            window.deleteLater()

    def test_enterprise_settings_round_trip_preserves_unsaved_favorite_editor(self):
        window = self._create_window()
        try:
            window.open_favorites()
            self.assertTrue(window.show_favorite_editor())
            editor = window.product_pages["favorite_editor"]
            editor.name_input.setText("未保存日报")
            editor.prompt_edit.setPlainText("生成未保存的日报")
            editor.run_options_toggle.setChecked(True)
            editor.schedule_attached_check.setChecked(True)
            self.assertTrue(editor.is_dirty())

            self.assertTrue(window._open_favorite_delivery_settings(editor))
            self.assertEqual(window.current_product_route, window.PAGE_SETTINGS)
            self.assertEqual(window.current_product_subroute, "favorite_delivery_setup")
            self.assertTrue(window.handle_product_back())
            self.assertIs(window.product_pages["favorite_editor"], editor)
            self.assertEqual(editor.name_input.text(), "未保存日报")
            self.assertEqual(editor.prompt_edit.toPlainText(), "生成未保存的日报")
            self.assertTrue(editor.is_dirty())
        finally:
            window.close()
            window.deleteLater()

    def test_binding_instruction_and_clipboard_use_complete_message(self):
        window = self._create_window()
        editor = FavoriteEditorPage(
            favorite={
                "id": "fav-complete-command",
                "name": "日报",
                "prompt": "生成日报",
                "schedule": {"enabled": True, "schedule_type": "daily", "time_of_day": "09:00"},
            },
            delivery_service=window.favorite_delivery_service,
        )
        try:
            editor._create_delivery_binding()
            command = editor.delivery_binding_command
            self.assertRegex(command, r"^绑定常用 \d{6}$")
            self.assertIn(command, editor.delivery_binding_command_label.text())
            self.assertIn("完整消息", editor.delivery_copy_btn.text())
            editor._copy_delivery_binding_command()
            self.assertEqual(QApplication.clipboard().text(), command)
        finally:
            editor.deleteLater()
            window.close()
            window.deleteLater()

    def test_favorites_follow_theme_preview_and_restore_without_changing_data(self):
        previous_manager = getattr(self.app, "theme_manager", None)
        manager = ThemeRuntimeManager(self.app, ThemeRepository(self.temp.name))
        self.app.theme_manager = manager
        window = self._create_window()
        try:
            window.theme_manager = manager
            manager.themeChanged.connect(window._apply_runtime_theme)
            manager.previewStateChanged.connect(window._on_theme_preview_state)
            window.config_manager.set_favorites(
                [{"id": "fav-theme", "name": "主题验收", "description": "验证安全预览与取消。", "prompt": "验收"}]
            )
            window.open_favorites()
            page = window.product_pages[window.PAGE_FAVORITES]
            original_favorites = window.config_manager.get_favorites()
            original_panel = DesignTokens.bg_panel

            manager.repository.write_preview(
                name="常用主题预览",
                overrides={
                    "tokens": {
                        "bg_panel": "#1c2230",
                        "text_primary": "#f2f4ff",
                        "text_secondary": "#b9c0d0",
                    }
                },
                default_tokens=default_design_tokens(),
                session_id="favorites-theme-test",
            )
            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                self.assertTrue(manager.apply_repository_state(reason="favorites_preview"), manager.last_error)
            page = window.product_pages[window.PAGE_FAVORITES]
            preview_card = next(
                page.grid.itemAt(index).widget()
                for index in range(page.grid.count())
                if page.grid.itemAt(index).widget()
                and page.grid.itemAt(index).widget().objectName() == "FavoriteCard"
            )
            self.assertIn("#1c2230", preview_card.styleSheet())
            self.assertFalse(window.theme_preview_bar.isHidden())
            self.assertEqual(window.config_manager.get_favorites(), original_favorites)

            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                self.assertTrue(manager.restore_saved_theme(reason="favorites_preview_restore"))
            restored_card = next(
                page.grid.itemAt(index).widget()
                for index in range(page.grid.count())
                if page.grid.itemAt(index).widget()
                and page.grid.itemAt(index).widget().objectName() == "FavoriteCard"
            )
            self.assertIn(original_panel, restored_card.styleSheet())
            self.assertTrue(window.theme_preview_bar.isHidden())
            self.assertEqual(window.config_manager.get_favorites(), original_favorites)
        finally:
            window.close()
            window.deleteLater()
            self.app.theme_manager = previous_manager

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
            self.assertEqual(state.persisted_conversation_meta["task_origin"]["favorite_name"], "后台日报")
            self.assertEqual(state.persisted_conversation_meta["task_origin"]["trigger_source"], "scheduler")
            window.set_session_status("error", state.session_id, error="provider unavailable")
            finished = window.config_manager.get_favorite_run_history()[0]
            self.assertEqual(finished["status"], "error")
            self.assertEqual(finished["error"], "provider unavailable")
        finally:
            window.close()
            window.deleteLater()

    def test_scheduled_terminal_result_is_enqueued_after_completion(self):
        window = self._create_window()
        try:
            window.config_manager.config["im_gateway"] = {
                "enabled_providers": ["feishu"],
                "providers": {
                    "feishu": {"enabled": True, "app_id": "app", "app_secret": "secret"},
                },
            }
            window.config_manager.save_config()
            request = window.favorite_delivery_service.create_binding_request("fav-delivery")
            pending = window.favorite_delivery_service.find_pending_request(request["code"])
            binding = window.favorite_delivery_service.claim_binding_request(
                pending["request_id"],
                "feishu",
                {"target_type": "chat_id", "target_value": "chat-delivery", "display_name": "日报群"},
            )
            saved = window.config_manager.upsert_favorite(
                {
                    "id": "fav-delivery",
                    "name": "发送日报",
                    "prompt": "生成日报",
                    "execution_mode": FAVORITE_EXECUTION_CHAT,
                    "schedule": {
                        "enabled": True,
                        "schedule_type": "daily",
                        "time_of_day": "09:00",
                        "delivery": {"enabled": True, "binding_id": binding["id"]},
                    },
                }
            )
            with patch.object(window, "_submit_session_request", return_value=True):
                self.assertTrue(window._trigger_favorite_schedule(saved["id"], "scheduler", 123))
            running = window.config_manager.get_favorite_run_history()[0]
            state = window.get_session(running["session_id"])
            artifact = os.path.join(state.workspace_dir, "日报.txt")
            with open(artifact, "w", encoding="utf-8") as handle:
                handle.write("日报")
            state.messages = [
                {"id": "assistant-final", "role": "assistant", "content": f"日报完成：{artifact}"}
            ]
            lifecycle_events = []
            original_save = window.save_chat_history
            original_enqueue = window.favorite_delivery_service.enqueue_delivery

            def save_then_record(*args, **kwargs):
                result = original_save(*args, **kwargs)
                lifecycle_events.append("persisted")
                return result

            def enqueue_then_record(*args, **kwargs):
                lifecycle_events.append("enqueued")
                return original_enqueue(*args, **kwargs)

            with patch.object(window, "save_chat_history", side_effect=save_then_record) as save:
                with patch.object(
                    window.favorite_delivery_service,
                    "enqueue_delivery",
                    side_effect=enqueue_then_record,
                ):
                    window.set_session_status("completed", state.session_id)
            self.assertTrue(save.call_args.kwargs["flush"])
            self.assertEqual(lifecycle_events, ["persisted", "enqueued"])
            finished = window.config_manager.get_favorite_run_history()[0]
            self.assertTrue(finished["delivery_id"])
            self.assertEqual(finished["delivery_status"], "pending")
            job = window.favorite_delivery_service.get_job(finished["delivery_id"])
            self.assertEqual(job["provider"], "feishu")
            self.assertEqual([item["type"] for item in job["payload"]["items"]], ["text", "artifact"])
            self.assertIn("日报完成", job["payload"]["items"][0]["text"])

            with patch.object(window, "_submit_session_request", return_value=True):
                self.assertTrue(window._trigger_favorite_schedule(saved["id"], "scheduler", 456))
            failed_persistence_run = window.config_manager.get_favorite_run_history()[0]
            failed_state = window.get_session(failed_persistence_run["session_id"])
            failed_state.messages = [
                {"id": "assistant-unsaved", "role": "assistant", "content": "尚未可靠保存"}
            ]
            with patch.object(window, "save_chat_history", return_value=False):
                with patch.object(
                    window.favorite_delivery_service,
                    "enqueue_delivery",
                ) as enqueue:
                    window.set_session_status("completed", failed_state.session_id)
            enqueue.assert_not_called()
            failed_finished = window.config_manager.get_favorite_run_history()[0]
            self.assertEqual(failed_finished["status"], "completed")
            self.assertEqual(failed_finished["delivery_status"], "failed")
            self.assertIn("未可靠持久化", failed_finished["delivery_error"])
        finally:
            window.close()
            window.deleteLater()

    def test_background_submit_retires_welcome_and_renders_user_request(self):
        window = self._create_window()
        try:
            foreground_id = window.current_session_id
            background_id = window.create_new_session(make_current=False)
            background = window.get_session(background_id)
            self.assertIsNotNone(background.empty_state)
            with patch.object(window, "_model_profile_for_state", return_value={"id": "test", "model_name": "test"}), patch.object(
                window, "_model_profile_snapshot_for_state", return_value={"id": "test", "model_name": "test"}
            ), patch.object(window, "_enqueue_staged_chat_save", return_value=True), patch.object(
                window, "_ensure_session_visible_in_history"
            ), patch.object(window, "process_agent_logic"):
                self.assertTrue(
                    window._submit_session_request(
                        background,
                        "自动整理今天的日报",
                        [],
                        check_duplicates=False,
                    )
                )
            self.assertEqual(window.current_session_id, foreground_id)
            self.assertIsNone(background.empty_state)
            self.assertEqual(background.messages[-1]["content"], "自动整理今天的日报")
            user_bubbles = [
                widget for widget in background.session_widget.findChildren(QWidget)
                if widget.__class__.__name__ == "ChatBubble" and getattr(widget, "role", "") == "User"
            ]
            self.assertEqual(len(user_bubbles), 1)
            window.handle_thinking_signal(
                "正在整理资料。",
                session_id=background_id,
                turn_id=background.active_turn_id,
            )
            window.flush_session_thinking(background_id)
            window.handle_content_signal(
                "日报已经整理完成。",
                session_id=background_id,
                turn_id=background.active_turn_id,
            )
            window.flush_session_content(background_id, final=True)
            turn_group = background.active_agent_turn_group
            self.assertIsNotNone(turn_group)
            self.assertLess(
                background.chat_layout.indexOf(user_bubbles[0]),
                background.chat_layout.indexOf(turn_group),
            )
            agent_bubble = turn_group.stage_bubbles[-1]
            self.assertIn("正在整理资料", agent_bubble.get_active_think_widget().text())
            self.assertEqual(agent_bubble.main_content_text, "日报已经整理完成。")
        finally:
            window.close()
            window.deleteLater()

    def test_task_origin_is_persisted_and_shown_in_conversation_header(self):
        window = self._create_window()
        try:
            state = window.get_current_session()
            window._set_favorite_task_origin(
                state,
                {"id": "fav-origin", "name": "晨间简报"},
                "scheduler",
                1720000000,
            )
            meta = window._compose_session_meta(state)
            self.assertEqual(meta["task_origin"]["favorite_name"], "晨间简报")
            window.update_conversation_header()
            self.assertFalse(window.workspace_subtitle_label.isHidden())
            self.assertEqual(window.workspace_subtitle_label.text(), "由定时任务启动 · 常用「晨间简报」")

            state.persisted_conversation_meta["task_origin"]["trigger_source"] = "manual"
            window.update_conversation_header()
            self.assertEqual(window.workspace_subtitle_label.text(), "手动运行计划 · 常用「晨间简报」")
        finally:
            window.close()
            window.deleteLater()

    def test_task_origin_restores_from_saved_conversation_meta(self):
        window = self._create_window()
        try:
            state = window.get_current_session()
            state.messages = [{"id": "u1", "role": "user", "content": "生成晨报"}]
            window._set_favorite_task_origin(
                state,
                {"id": "fav-deleted", "name": "已删除的晨报"},
                "scheduler",
                1720000100,
            )
            window.save_chat_history(session_id=state.session_id, flush=True)
            saved_id = state.session_id
            window.sessions.pop(saved_id)
            window.session_tabs.removeTab(window.session_tabs.indexOf(state.session_widget))

            window.create_new_session(session_id=saved_id, make_current=False)
            restored = window.get_session(saved_id)
            restored.history_loaded = False
            restored.persisted_conversation_meta = window.chat_storage.get_conversation_meta(saved_id)
            window.activate_session(saved_id, ensure_loaded=False)
            window.update_conversation_header()
            self.assertEqual(
                window.workspace_subtitle_label.text(),
                "由定时任务启动 · 常用「已删除的晨报」",
            )
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
