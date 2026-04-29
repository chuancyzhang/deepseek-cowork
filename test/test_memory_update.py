import json
import os
import shutil
import tempfile
import unittest

from core.chat_storage import ChatStorage
from core.memory_update import (
    batch_transcripts,
    estimate_tokens,
    generate_memory_update,
    save_memory_file_with_backup,
)


class FakeProvider:
    def __init__(self):
        self.calls = []

    def chat_stream(self, messages, tools=None):
        self.calls.append(messages)
        user_text = messages[-1]["content"]
        if "# 历史分批摘要" in user_text:
            yield {"type": "content", "content": "# Long-term Memory\n- merged memory"}
        else:
            yield {"type": "content", "content": "- batch summary"}


class TestMemoryUpdate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_estimate_tokens_counts_chinese_conservatively_and_ascii_by_four_chars(self):
        self.assertEqual(estimate_tokens("你好世界"), 4)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)
        self.assertEqual(estimate_tokens("你好 abcdefgh"), 4)

    def test_batch_transcripts_splits_at_token_limit(self):
        batches = batch_transcripts(["a" * 40, "b" * 40], max_tokens=15)
        self.assertGreaterEqual(len(batches), 2)
        self.assertTrue(all(estimate_tokens(batch) <= 15 for batch in batches))

    def test_batch_transcripts_counts_large_session_split_headers(self):
        transcripts = [
            {"id": "large", "title": "大型会话", "messages": [{"role": "user", "content": "甲" * 150}]},
        ]
        batches = batch_transcripts(transcripts, max_tokens=80)
        self.assertGreaterEqual(len(batches), 2)
        self.assertTrue(all(estimate_tokens(batch) <= 80 for batch in batches))

    def test_chat_storage_exports_sqlite_and_legacy_json_without_duplicates(self):
        storage = ChatStorage(os.path.join(self.temp_dir, "chat_history.sqlite"))
        storage.save_conversation(
            "sqlite-session",
            [{"role": "user", "content": "SQLite history"}],
            title="SQLite Session",
            meta={"archived": True},
        )
        duplicate_json = os.path.join(self.temp_dir, "chat_history_sqlite-session.json")
        with open(duplicate_json, "w", encoding="utf-8") as f:
            json.dump([{"role": "user", "content": "Duplicate"}], f)
        legacy_json = os.path.join(self.temp_dir, "chat_history_legacy-session.json")
        with open(legacy_json, "w", encoding="utf-8") as f:
            json.dump([{"role": "user", "content": "Legacy history"}], f)

        transcripts = storage.iter_conversation_transcripts(include_archived=True)
        ids = {item["id"] for item in transcripts}
        self.assertIn("sqlite-session", ids)
        self.assertIn("legacy-session", ids)
        self.assertEqual(len([item for item in transcripts if item["id"] == "sqlite-session"]), 1)

    def test_generate_memory_update_uses_batch_summaries_and_final_merge(self):
        provider = FakeProvider()
        transcripts = [
            {"id": "a", "title": "A", "messages": [{"role": "user", "content": "甲" * 20}]},
            {"id": "b", "title": "B", "messages": [{"role": "user", "content": "乙" * 20}]},
        ]
        result = generate_memory_update(provider, "old memory", transcripts, max_batch_tokens=35)
        self.assertEqual(result["content"], "# Long-term Memory\n- merged memory")
        self.assertGreaterEqual(result["batch_count"], 2)
        self.assertEqual(len(provider.calls), result["batch_count"] + 1)

    def test_save_memory_file_with_backup_replaces_atomically(self):
        path = os.path.join(self.temp_dir, "memories.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("old")
        result = save_memory_file_with_backup(self.temp_dir, "new")
        with open(path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "new")
        self.assertTrue(result["backup_path"])
        with open(result["backup_path"], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "old")


if __name__ == "__main__":
    unittest.main()
