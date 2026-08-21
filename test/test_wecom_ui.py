import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

import main as main_module
from main import (
    CapabilityWorkbenchDialog,
    SkillsCenterDialog,
    WecomQrAuthorizationWorker,
    initialize_desktop_theme,
)


class _SkillManager:
    def is_skill_editable(self, _name):
        return False

    def get_all_skills(self):
        return [_skill(False)]


class _ConfigManager:
    def __init__(self):
        self.values = []

    def set_skill_enabled(self, name, enabled):
        self.values.append((name, enabled))


def _skill(enabled=False):
    return {
        "name": "wecom-unified",
        "display_name": "企业微信办公套件",
        "enabled": enabled,
        "source_type": "bundled_plugin",
        "authorization": {"provider": "wecom_cli", "required": True},
        "presentation": {
            "summary": "连接企业微信办公能力。",
            "examples": ["查询日程", "读写文档"],
            "access_note": "需要扫码连接。",
        },
    }


class TestWecomCapabilityUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        initialize_desktop_theme(cls.app)
        cls.dialogs = []

    @classmethod
    def tearDownClass(cls):
        for dialog in cls.dialogs:
            dialog.close()
            dialog.deleteLater()
        cls.app.processEvents()
        cls.dialogs.clear()

    def _dialog(self, enabled=False):
        with patch.object(CapabilityWorkbenchDialog, "_check_authorization", return_value=True):
            dialog = CapabilityWorkbenchDialog(
                _skill(enabled),
                _SkillManager(),
                _ConfigManager(),
                simple_mode=True,
            )
        self.dialogs.append(dialog)
        return dialog

    def test_authorization_setup_uses_inline_page_and_theme_primitives(self):
        dialog = self._dialog(False)
        self.assertTrue(dialog.tabs.isHidden())
        self.assertEqual(dialog.authorization_primary_btn.text(), "扫码连接")
        self.assertIn("QPushButton", dialog.authorization_primary_btn.styleSheet())
        self.assertIsNotNone(dialog.findChild(QFrame, "CapabilityAuthorizationSetup"))
        self.assertIn("联网身份校验", dialog.authorization_description.text())

    def test_connected_disabled_and_reauthorization_states_are_distinct(self):
        dialog = self._dialog(False)
        dialog._handle_authorization_status({
            "state": "connected",
            "state_text": "已连接",
            "authorized": True,
            "verified": True,
        })
        self.assertFalse(dialog.authorization_enable_btn.isHidden())
        self.assertEqual(dialog.authorization_primary_btn.text(), "重新授权")
        self.assertIn("已连接", dialog.authorization_notice.label.text())

        dialog._handle_authorization_status({
            "state": "needs_reauthorization",
            "state_text": "需要重新授权",
            "authorized": True,
            "verified": False,
            "detail": "服务端身份校验失败。",
        })
        self.assertFalse(dialog.authorization_enable_btn.isVisible())
        self.assertIn("需要重新授权", dialog.authorization_notice.label.text())

    def test_enabled_capability_can_be_closed_without_deleting_authorization(self):
        dialog = self._dialog(True)
        dialog._handle_authorization_status({
            "state": "authorized_unverified",
            "state_text": "本机已授权但未验证",
            "authorized": True,
            "verified": False,
        })
        buttons = [button.text() for button in dialog.findChildren(type(dialog.authorization_primary_btn))]
        self.assertIn("关闭能力", buttons)
        self.assertIn("重新授权", buttons)

    def test_staged_credentials_publish_atomically_and_rollback_on_cleanup_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "wecom-cli"
            staging = root / ".reauth-staging-test"
            config.mkdir()
            staging.mkdir()
            (config / "credentials.enc").write_text("old", encoding="utf-8")
            (staging / "credentials.enc").write_text("new", encoding="utf-8")

            WecomQrAuthorizationWorker._publish_staged_config(str(config), str(staging))
            self.assertEqual((config / "credentials.enc").read_text(encoding="utf-8"), "new")
            self.assertFalse(staging.exists())

            rollback_staging = root / ".reauth-staging-rollback"
            rollback_staging.mkdir()
            (rollback_staging / "credentials.enc").write_text("newer", encoding="utf-8")
            with patch.object(main_module.shutil, "rmtree", side_effect=PermissionError("busy")):
                with self.assertRaisesRegex(RuntimeError, "已回滚"):
                    WecomQrAuthorizationWorker._publish_staged_config(
                        str(config), str(rollback_staging)
                    )
            self.assertEqual((config / "credentials.enc").read_text(encoding="utf-8"), "new")

    def test_store_card_distinguishes_connected_disabled_and_unauthorized(self):
        connected = {
            "state": "connected",
            "state_text": "已连接",
            "authorized": True,
            "verified": True,
        }
        with patch.object(main_module, "authorization_status_for_skill", return_value=connected):
            store = SkillsCenterDialog(_SkillManager(), _ConfigManager())
        self.dialogs.append(store)
        card = store._capability_row(store._all_skills[0])
        labels = [label.text() for label in card.findChildren(QLabel)]
        buttons = [button.text() for button in card.findChildren(QPushButton)]
        self.assertIn("已连接 · 已关闭", labels)
        self.assertIn("开启", buttons)

        unauthorized_skill = _skill(False)
        unauthorized_skill["_authorization_status"] = {
            "state": "unauthorized",
            "state_text": "未授权",
            "authorized": False,
            "verified": False,
        }
        unauthorized_card = store._capability_row(unauthorized_skill)
        unauthorized_buttons = [button.text() for button in unauthorized_card.findChildren(QPushButton)]
        self.assertIn("连接并开启", unauthorized_buttons)


if __name__ == "__main__":
    unittest.main()
