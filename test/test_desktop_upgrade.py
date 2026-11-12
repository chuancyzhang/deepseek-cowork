"""Direct data reuse preserves history and refuses unavailable configured paths."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.chat_storage import ChatStorage
from core.desktop_store import DesktopChatStorage, DesktopStore
from core.desktop_upgrade import DataLease, existing_database


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

    def test_original_database_and_resource_paths_are_reused(self):
        managed = self.root / "conversation_workspaces" / "existing"
        managed.mkdir(parents=True)
        attachment = managed / "原附件.txt"
        attachment.write_text("附件内容", encoding="utf-8")
        original = DesktopStore(DesktopChatStorage(str(self.database)))
        original.put_state("draft:existing", {"text": "未发送草稿", "attachments": [{"path": str(attachment)}]})
        before = self.storage.get_messages("existing")
        config = (self.root / "config.json").read_bytes()
        for _ in range(2):
            self.assertEqual(existing_database(self.root), self.database)
            current = DesktopStore(DesktopChatStorage(str(existing_database(self.root))))
            self.assertEqual(current.storage.get_messages("existing"), before)
            self.assertEqual(current.get_state("draft:existing")["attachments"][0]["path"], str(attachment))
        self.assertEqual((self.root / "config.json").read_bytes(), config)
        self.assertFalse((self.root / "desktop-v2").exists())
        self.assertFalse((self.root / "updates").exists())
        current.storage.upsert_conversation("new", title="新版新增")
        self.assertEqual(ChatStorage(str(self.database)).get_conversation_record("new")["title"], "新版新增")

    def test_missing_custom_history_is_not_recreated(self):
        missing = self.root / "disconnected-drive"
        (self.root / "config.json").write_text(json.dumps({"chat_history_dir": str(missing)}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "原历史目录"):
            existing_database(self.root)
        self.assertFalse(missing.exists())

    def test_incomplete_prior_copy_does_not_redirect_original_data(self):
        (self.root / ".desktop-v2-abandoned").mkdir()
        (self.root / ".desktop-upgrade.json").write_text(json.dumps({"upgrade": "native-desktop-1", "database": str(self.database)}))
        self.assertEqual(existing_database(self.root), self.database)
        self.database.unlink()
        with self.assertRaisesRegex(RuntimeError, "历史数据库不存在"):
            existing_database(self.root)
        self.assertFalse(self.database.exists())

    def test_live_data_owner_prevents_another_service(self):
        lease = DataLease(self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, "另一个后台服务"):
                DataLease(self.root)
        finally:
            lease.close()
        DataLease(self.root).close()
