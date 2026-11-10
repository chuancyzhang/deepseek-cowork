"""Upgrade preserves existing facts and never replaces an unavailable ledger."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.chat_storage import ChatStorage
from core.desktop_store import DesktopChatStorage, DesktopStore
from core.desktop_upgrade import DataLease, DesktopUpgrade


class DesktopUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / "external-history"
        self.history.mkdir()
        self.database = self.history / "chat_history.sqlite"
        (self.root / "config.json").write_text(json.dumps({"chat_history_dir": str(self.history)}), encoding="utf-8")
        self.storage = ChatStorage(str(self.database))
        self.storage.upsert_conversation("existing", title="旧对话", meta={"workspace_dir": str(self.root)})
        with self.storage._connect() as db:
            self.storage._insert_message_row(db, "messages", "conversation_id", "existing",
                {"id": "message", "role": "user", "content": "必须保留的内容", "meta": {"attachments": ["原附件.pdf"]}}, 0, 1)

    def test_additive_upgrade_retains_content_and_backup_and_is_repeatable(self):
        before = self.storage.get_messages("existing")
        upgrade = DesktopUpgrade(self.root)
        DesktopStore(DesktopChatStorage(str(self.database)))
        upgrade.complete()
        self.assertEqual(self.storage.get_messages("existing"), before)
        backup = ChatStorage(str(upgrade.backup / "chat_history.sqlite"))
        self.assertEqual(backup.get_messages("existing"), before)
        self.assertEqual(json.loads((upgrade.backup / "config.json").read_text())["chat_history_dir"], str(self.history))
        self.assertIsNone(DesktopUpgrade(self.root).backup)

    def test_history_change_fails_verification_and_keeps_backup(self):
        upgrade = DesktopUpgrade(self.root)
        with self.storage._connect() as db:
            db.execute("UPDATE messages SET content='bad change' WHERE id='message'")
        with self.assertRaisesRegex(RuntimeError, "历史内容变化"):
            upgrade.complete()
        self.assertFalse(upgrade.marker.exists())
        backup = ChatStorage(str(upgrade.backup / "chat_history.sqlite"))
        self.assertEqual(backup.get_messages("existing")[0]["content"], "必须保留的内容")

    def test_missing_custom_history_is_not_recreated(self):
        missing = self.root / "disconnected-drive"
        (self.root / "config.json").write_text(json.dumps({"chat_history_dir": str(missing)}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "原历史目录"):
            DesktopUpgrade(self.root)
        self.assertFalse(missing.exists())

    def test_missing_previous_database_is_not_replaced_with_empty_history(self):
        upgrade = DesktopUpgrade(self.root)
        upgrade.complete()
        self.database.unlink()
        with self.assertRaisesRegex(RuntimeError, "历史数据库不存在"):
            DesktopUpgrade(self.root)
        self.assertFalse(self.database.exists())

    def test_live_data_owner_prevents_another_service(self):
        lease = DataLease(self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, "另一个后台服务"):
                DataLease(self.root)
        finally:
            lease.close()
        DataLease(self.root).close()
