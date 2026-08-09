import base64
import os
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_package import build_asset_record
from core.theme_service import DEFAULT_THEME_ID, ThemeRepository
from ui.theme_settings import ThemeSettingsPanel


ANIMATED_GIF = base64.b64decode(
    "R0lGODlhBAADAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACAAAACwAAAAABAADAAAICAABCBxIUGBAACH5BAEMAAEALAAAAAAEAAMAgQAA/wAAAAAAAAAAAAgIAAEIHEhQYEAAOw=="
)


class ThemeSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ThemeRepository(self.temp_dir.name)
        self.panel = ThemeSettingsPanel(self.repository)

    def tearDown(self):
        self.panel.deleteLater()
        self.temp_dir.cleanup()

    def test_default_theme_is_read_only(self):
        self.assertEqual(self.panel.theme_combo.currentData(), DEFAULT_THEME_ID)
        self.assertFalse(self.panel.name_edit.isEnabled())
        self.assertFalse(self.panel.delete_btn.isEnabled())

    def test_new_theme_is_draft_until_commit(self):
        self.panel._new_theme()
        draft = self.panel.state_signature()
        self.assertNotEqual(draft["active_theme_id"], DEFAULT_THEME_ID)
        self.assertEqual(len(draft["themes"]), 1)
        self.assertEqual(self.repository.load().themes, tuple())
        self.panel.commit()
        snapshot = self.repository.load()
        self.assertEqual(len(snapshot.themes), 1)
        self.assertEqual(snapshot.active_theme_id, draft["active_theme_id"])
        self.assertIn("建议重启应用", self.panel.last_commit_warning)

    def test_saved_image_theme_can_reopen_with_serializable_dirty_state(self):
        self.panel._new_theme()
        profile = self.panel._profile(self.panel._current_theme_id)
        image_path = os.path.join(self.temp_dir.name, "background.png")
        image = QImage(1672, 941, QImage.Format_RGB32)
        image.fill(0xFFF8F2)
        self.assertTrue(image.save(image_path))
        record, data = build_asset_record("background", image_path)
        profile["assets"] = {"background": record}
        profile["_asset_bytes"] = {record["path"]: data}
        self.panel.commit()

        reopened = ThemeSettingsPanel(self.repository)
        self.addCleanup(reopened.deleteLater)
        signature = reopened.state_signature()

        self.assertEqual(signature["themes"][0]["assets"]["background"]["sha256"], record["sha256"])
        self.assertNotIn("_asset_bytes", signature["themes"][0])
        json.dumps(signature, ensure_ascii=False, sort_keys=True)

    def test_animated_asset_import_shows_verified_frame_metadata(self):
        self.panel._new_theme()
        image_path = os.path.join(self.temp_dir.name, "background.gif")
        with open(image_path, "wb") as stream:
            stream.write(ANIMATED_GIF)

        with patch(
            "ui.theme_settings.QFileDialog.getOpenFileName",
            return_value=(image_path, "主题图片"),
        ):
            self.panel._add_asset()

        self.assertEqual(self.panel.asset_list.count(), 1)
        item_text = self.panel.asset_list.item(0).text()
        self.assertIn("image/gif", item_text)
        self.assertIn("动态 2 帧", item_text)
        self.assertIn("0.2 秒", item_text)
        profile = self.panel._profile(self.panel._current_theme_id)
        self.assertEqual(
            profile["assets"]["background"]["animation"],
            {"frame_count": 2, "duration_ms": 200},
        )

    def test_state_signature_projects_editor_values_without_mutating_draft(self):
        self.panel._new_theme()
        profile = self.panel._profile(self.panel._current_theme_id)
        original_name = profile["name"]

        self.panel.name_edit.blockSignals(True)
        self.panel.name_edit.setText("仅用于状态签名")
        self.panel.name_edit.blockSignals(False)

        signature = self.panel.state_signature()

        self.assertEqual(profile["name"], original_name)
        self.assertEqual(signature["themes"][0]["name"], "仅用于状态签名")
        self.assertEqual(signature["editor_error"], "")

    def test_switching_from_image_theme_to_legacy_theme_does_not_copy_scene(self):
        self.panel.deleteLater()
        image_path = os.path.join(self.temp_dir.name, "sakura.png")
        image = QImage(48, 32, QImage.Format_RGB32)
        image.fill(0xFFF8F2)
        self.assertTrue(image.save(image_path))
        record, data = build_asset_record("sakura_magic_background", image_path)
        image_theme = self.repository.upsert_theme(
            name="贴图主题",
            overrides={},
            default_tokens=default_design_tokens(),
            assets={"sakura_magic_background": record},
            workspace_scene={
                "attachment": "fixed",
                "layers": [
                    {
                        "type": "image",
                        "asset": "sakura_magic_background",
                    }
                ],
            },
            asset_bytes={record["path"]: data},
        )["theme"]
        os.makedirs(self.repository.themes_dir, exist_ok=True)
        legacy_paths = {}
        for legacy_id, legacy_name in (
            ("legacy", "旧版 JSON 主题"),
            ("legacy_two", "旧版 JSON 主题二"),
        ):
            legacy_path = os.path.join(
                self.repository.themes_dir,
                f"{legacy_id}.json",
            )
            legacy_paths[legacy_id] = legacy_path
            with open(legacy_path, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "format": "cowork-theme",
                        "id": legacy_id,
                        "name": legacy_name,
                        "overrides": {"font_scale": 1.05},
                    },
                    stream,
                    ensure_ascii=False,
                )

        self.panel = ThemeSettingsPanel(self.repository)
        observed_signatures = []
        self.panel.name_edit.textChanged.connect(
            lambda: observed_signatures.append(self.panel.state_signature())
        )

        self.panel.theme_combo.setCurrentIndex(
            self.panel.theme_combo.findData(image_theme["id"])
        )
        self.panel.theme_combo.setCurrentIndex(
            self.panel.theme_combo.findData("legacy")
        )
        self.panel.theme_combo.setCurrentIndex(
            self.panel.theme_combo.findData(DEFAULT_THEME_ID)
        )
        self.panel.theme_combo.setCurrentIndex(
            self.panel.theme_combo.findData(image_theme["id"])
        )
        self.panel.theme_combo.setCurrentIndex(
            self.panel.theme_combo.findData("legacy_two")
        )

        for legacy_id in legacy_paths:
            legacy_draft = self.panel._profile(legacy_id)
            self.assertEqual(legacy_draft["assets"], {})
            self.assertEqual(
                legacy_draft["workspace_scene"],
                {"attachment": "fixed", "layers": []},
            )
        self.assertNotIn("sakura_magic_background", self.panel.scene_editor.toPlainText())
        self.assertTrue(observed_signatures)

        self.panel._preview_current()
        self.assertNotIn("预览失败", self.panel.validation_label.text())
        self.assertEqual(self.repository.load_preview()["workspace_scene"]["layers"], [])

        self.panel.commit()
        for legacy_path in legacy_paths.values():
            self.assertFalse(os.path.exists(legacy_path))
        saved_legacy = self.repository.get_theme("legacy")
        saved_legacy_two = self.repository.get_theme("legacy_two")
        saved_image = self.repository.get_theme(image_theme["id"])
        self.assertEqual(saved_legacy["assets"], {})
        self.assertEqual(saved_legacy["workspace_scene"]["layers"], [])
        self.assertEqual(saved_legacy_two["assets"], {})
        self.assertEqual(saved_legacy_two["workspace_scene"]["layers"], [])
        self.assertIn("sakura_magic_background", saved_image["assets"])
        self.assertEqual(
            saved_image["workspace_scene"]["layers"][0]["asset"],
            "sakura_magic_background",
        )

    def test_scene_editor_persists_single_scene_and_surface_materials(self):
        self.panel._new_theme()
        self.panel.scene_editor.setPlainText(
            json.dumps(
                {
                    "workspace_scene": {
                        "attachment": "fixed",
                        "layers": [
                            {"type": "solid", "color": "#f4f1e8"},
                            {
                                "type": "grid",
                                "color": "rgba(166,116,24,0.10)",
                                "spacing": 32,
                                "major_every": 4,
                                "major_color": "rgba(200,63,104,0.12)",
                            },
                        ],
                    },
                    "surfaces": {
                        "conversation.composer": {
                            "material": {"kind": "tint", "color": "#ffffff", "opacity": 0.92}
                        }
                    },
                },
                ensure_ascii=False,
            )
        )
        self.panel.commit()
        theme = self.repository.get_theme(self.repository.load().active_theme_id)
        self.assertEqual(theme["workspace_scene"]["layers"][1]["major_every"], 4)
        self.assertEqual(
            theme["surfaces"]["conversation.composer"]["material"]["kind"],
            "tint",
        )

    def test_unchanged_appearance_save_does_not_recommend_restart(self):
        self.panel.commit()

        self.assertEqual(self.panel.last_commit_warning, "")

    def test_runtime_refresh_failure_keeps_saved_theme_and_requests_restart(self):
        manager = Mock()
        manager.themeChanged = Mock()
        manager.themeChanged.connect = Mock()
        manager.apply_repository_state.return_value = False
        panel = ThemeSettingsPanel(self.repository, runtime_manager=manager)
        try:
            panel._new_theme()
            draft = panel.state_signature()
            panel.commit()

            snapshot = self.repository.load()
            self.assertEqual(snapshot.active_theme_id, draft["active_theme_id"])
            self.assertEqual(len(snapshot.themes), 1)
            self.assertIn("设置和主题已保存", panel.last_commit_warning)
            self.assertIn("请重启应用", panel.last_commit_warning)
            manager.acknowledge_repository_state.assert_called_once_with()
            manager.apply_repository_state.assert_called_once_with(
                reason="settings_save",
                persisted_on_failure=True,
            )
        finally:
            panel.deleteLater()

    def test_restore_discards_unsaved_theme(self):
        self.panel._new_theme()
        self.panel.restore_saved_theme()
        state = self.panel.state_signature()
        self.assertEqual(state["active_theme_id"], DEFAULT_THEME_ID)
        self.assertEqual(state["themes"], [])

    def test_low_contrast_edit_warns_without_blocking_draft(self):
        self.panel._new_theme()
        self.panel.token_editor.setPlainText('{"chat_text": "#ffffff"}')
        self.assertIn("对比度", self.panel.validation_label.text())
        self.assertEqual(self.panel.state_signature()["editor_error"], "")

    def test_numeric_wheel_requires_explicit_click_activation(self):
        self.panel._new_theme()
        self.panel.show()
        self.app.processEvents()
        spin = self.panel.font_scale_spin
        initial = spin.value()

        spin.setFocus(Qt.TabFocusReason)
        self.app.processEvents()
        ignored_wheel = QWheelEvent(
            QPointF(spin.rect().center()),
            QPointF(spin.mapToGlobal(spin.rect().center())),
            QPoint(),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(spin, ignored_wheel)
        self.assertEqual(spin.value(), initial)
        self.assertFalse(ignored_wheel.isAccepted())

        QTest.mouseClick(spin, Qt.LeftButton, pos=spin.rect().center())
        self.app.processEvents()
        active_wheel = QWheelEvent(
            QPointF(spin.rect().center()),
            QPointF(spin.mapToGlobal(spin.rect().center())),
            QPoint(),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(spin, active_wheel)
        self.assertEqual(spin.value(), initial + spin.singleStep())
        self.assertTrue(active_wheel.isAccepted())

        self.panel.name_edit.setFocus(Qt.MouseFocusReason)
        self.app.processEvents()
        QApplication.sendEvent(
            spin,
            QWheelEvent(
                QPointF(spin.rect().center()),
                QPointF(spin.mapToGlobal(spin.rect().center())),
                QPoint(),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.ScrollUpdate,
                False,
            ),
        )
        self.assertEqual(spin.value(), initial + spin.singleStep())

    def test_combo_ignores_wheel_until_explicit_click_activation(self):
        self.panel._new_theme()
        self.panel.show()
        self.app.processEvents()
        combo = self.panel.density_combo
        initial = combo.currentIndex()

        combo.setFocus(Qt.TabFocusReason)
        self.app.processEvents()
        ignored_wheel = QWheelEvent(
            QPointF(combo.rect().center()),
            QPointF(combo.mapToGlobal(combo.rect().center())),
            QPoint(),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(combo, ignored_wheel)
        self.assertEqual(combo.currentIndex(), initial)
        self.assertFalse(ignored_wheel.isAccepted())

        QTest.mouseClick(combo, Qt.LeftButton, pos=combo.rect().center())
        self.app.processEvents()
        self.assertTrue(combo._wheel_activated_by_click)
        combo.hidePopup()

    def test_editing_draft_does_not_apply_until_preview_clicked(self):
        manager = Mock()
        manager.themeChanged = Mock()
        manager.themeChanged.connect = Mock()
        panel = ThemeSettingsPanel(self.repository, runtime_manager=manager)
        try:
            panel._new_theme()
            panel.color_edits["primary"].setText("#3366cc")
            manager.apply_repository_state.assert_not_called()
            panel._preview_current()
            manager.apply_repository_state.assert_called_once_with(
                reason="settings_preview"
            )
            first_preview = self.repository.load_preview()
            panel.color_edits["primary"].setText("#2255aa")
            self.assertIn("不是最新", panel.validation_label.text())
            self.assertEqual(manager.apply_repository_state.call_count, 1)
            panel._preview_current()
            second_preview = self.repository.load_preview()
            self.assertEqual(
                second_preview["preview_id"],
                first_preview["preview_id"],
            )
            self.assertEqual(
                second_preview["revision"],
                first_preview["revision"] + 1,
            )
        finally:
            panel.deleteLater()

    def test_runtime_applies_valid_theme_and_rejects_missing_font(self):
        manager = ThemeRuntimeManager(self.app, self.repository)
        original_tokens = default_design_tokens()
        original_font = self.app.font()
        original_stylesheet = self.app.styleSheet()
        try:
            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                applied = manager.apply_profile(
                    {
                        "id": "runtime",
                        "name": "Runtime",
                        "base": DEFAULT_THEME_ID,
                        "overrides": {"tokens": {"primary": "#3366cc"}},
                    },
                    reason="test",
                )
                self.assertTrue(applied)
                self.assertEqual(DesignTokens.primary, "#3366cc")
                self.assertEqual(manager.current["id"], "runtime")
                rejected = manager.apply_profile(
                    {
                        "id": "missing-font",
                        "name": "Missing",
                        "base": DEFAULT_THEME_ID,
                        "overrides": {"font_family": "Not Installed"},
                    },
                    reason="test",
                )
                self.assertFalse(rejected)
                self.assertEqual(DesignTokens.primary, "#3366cc")
                self.assertIn("Not Installed", manager.last_error)
        finally:
            for name, value in original_tokens.items():
                if hasattr(DesignTokens, name):
                    setattr(DesignTokens, name, value)
            self.app.setFont(original_font)
            self.app.setStyleSheet(original_stylesheet)

    def test_runtime_start_discards_unconfirmed_preview(self):
        preview = self.repository.write_preview(
            name="Discard me",
            overrides={"tokens": {"primary": "#3366cc"}},
            default_tokens=default_design_tokens(),
        )
        self.assertEqual(
            self.repository.load_preview()["preview_id"],
            preview["preview_id"],
        )
        previous_registry = getattr(self.app, "theme_binding_registry", None)
        manager = ThemeRuntimeManager(self.app, self.repository)
        try:
            with patch(
                "core.theme.QFontDatabase.families",
                return_value=["Microsoft YaHei UI", "Consolas"],
            ):
                manager.start()
            self.assertIsNone(self.repository.load_preview())
            self.assertEqual(manager.current["id"], DEFAULT_THEME_ID)
        finally:
            manager.stop()
            self.app.theme_binding_registry = previous_registry


if __name__ == "__main__":
    unittest.main()
