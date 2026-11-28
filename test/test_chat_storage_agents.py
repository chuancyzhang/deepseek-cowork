import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from core.chat_storage import ChatStorage


class TestChatStorageAgents(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")
        self.storage = ChatStorage(self.db_path)
        self.conversation_id = "conv-1"
        self.storage.upsert_conversation(self.conversation_id, title="test", status="active", meta={})

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_agent_tables_are_created(self):
        with self.storage._connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        self.assertIn("agents", tables)
        self.assertIn("agent_messages", tables)
        self.assertIn("idx_agents_conversation_updated", indexes)
        self.assertIn("idx_agents_conversation_status", indexes)
        self.assertIn("idx_agent_messages_agent_pos", indexes)

    def test_upsert_agent_and_messages_roundtrip(self):
        agent = self.storage.upsert_agent(
            "agent-1",
            conversation_id=self.conversation_id,
            name="worker",
            status="queued",
            fork_context=True,
            meta={"source_tool_call_id": "tool-123"},
        )
        self.assertEqual(agent["id"], "agent-1")
        self.assertEqual(agent["name"], "worker")
        self.assertTrue(agent["fork_context"])
        self.assertEqual(agent["meta"].get("source_tool_call_id"), "tool-123")

        self.storage.replace_agent_messages(
            "agent-1",
            [
                {"id": "m1", "role": "user", "content": "hello", "created_at": 111},
                {"id": "m2", "role": "assistant", "content": "world", "created_at": 222},
            ],
        )
        messages = self.storage.get_agent_messages("agent-1")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[1]["content"], "world")
        self.assertEqual(messages[0]["created_at"], 111)
        self.assertEqual(messages[1]["created_at"], 222)

        self.storage.append_agent_messages("agent-1", [{"id": "m3", "role": "user", "content": "next"}])
        messages = self.storage.get_agent_messages("agent-1")
        self.assertEqual(messages[-1]["content"], "next")

    def test_list_agents_and_status_filter(self):
        self.storage.upsert_agent("agent-1", conversation_id=self.conversation_id, name="a1", status="running")
        self.storage.upsert_agent("agent-2", conversation_id=self.conversation_id, name="a2", status="completed")
        all_agents = self.storage.list_agents(self.conversation_id)
        self.assertEqual(len(all_agents), 2)
        completed = self.storage.list_agents(self.conversation_id, status_filter="completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["id"], "agent-2")

    def test_concurrent_appends_preserve_all_rows_and_return_persisted_values(self):
        self.storage.upsert_agent("agent-1", conversation_id=self.conversation_id, name="worker")
        barrier = Barrier(4)

        def append(index):
            barrier.wait()
            return self.storage.append_agent_messages("agent-1", [
                {"id": f"m{index}", "role": "assistant", "content": str(index), "reasoning": "why"}])

        with patch.object(self.storage, "replace_agent_messages", side_effect=AssertionError("must append")), \
             ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(append, range(4)))
        stored = self.storage.get_agent_messages("agent-1")
        self.assertEqual({message["id"] for message in stored}, {f"m{i}" for i in range(4)})
        self.assertEqual([message["meta"]["sequence"] for message in stored], list(range(4)))
        for result in results:
            self.assertEqual(result, stored[:len(result)])
            self.assertTrue(all(message.get("created_at") for message in result))
            self.assertEqual(result[-1]["reasoning_content"], "why")

    def test_failed_append_rolls_back_entire_batch(self):
        self.storage.upsert_agent("agent-1", conversation_id=self.conversation_id, name="worker")
        original = self.storage.append_agent_messages("agent-1", [{"id": "seed", "role": "user", "content": "keep"}])
        with self.assertRaises(TypeError):
            self.storage.append_agent_messages("agent-1", [
                {"id": "good", "role": "assistant", "content": "partial"},
                {"id": "bad", "role": "assistant", "content": "invalid", "meta": {"value": object()}},
            ])
        self.assertEqual(self.storage.get_agent_messages("agent-1"), original)

    def test_set_agent_status_and_soft_delete(self):
        self.storage.upsert_agent("agent-1", conversation_id=self.conversation_id, name="a1", status="running")
        updated = self.storage.set_agent_status(
            "agent-1",
            "completed",
            last_result="done",
            meta_patch={"k": "v"},
        )
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["last_result"], "done")
        self.assertEqual(updated["meta"].get("k"), "v")
        self.assertIsNotNone(updated["finished_at"])

        soft = self.storage.delete_agent("agent-1", hard=False)
        self.assertTrue(soft["deleted"])
        self.assertEqual(self.storage.get_agent("agent-1")["status"], "closed")

        hard = self.storage.delete_agent("agent-1", hard=True)
        self.assertTrue(hard["deleted"])
        self.assertIsNone(self.storage.get_agent("agent-1"))

    def test_resolve_agent_target_id_and_name(self):
        self.storage.upsert_agent("agent-id", conversation_id=self.conversation_id, name="worker", status="queued")
        by_id = self.storage.resolve_agent_target(self.conversation_id, "agent-id")
        self.assertEqual(by_id["id"], "agent-id")
        by_name = self.storage.resolve_agent_target(self.conversation_id, "worker")
        self.assertEqual(by_name["id"], "agent-id")

        self.storage.upsert_agent("agent-2", conversation_id=self.conversation_id, name="dup", status="queued")
        self.storage.upsert_agent("agent-3", conversation_id=self.conversation_id, name="dup", status="queued")
        with self.assertRaises(ValueError):
            self.storage.resolve_agent_target(self.conversation_id, "dup")


if __name__ == "__main__":
    unittest.main()
