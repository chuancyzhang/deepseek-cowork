import os
import shutil
import tempfile
import unittest

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
                {"id": "m1", "role": "user", "content": "hello"},
                {"id": "m2", "role": "assistant", "content": "world"},
            ],
        )
        messages = self.storage.get_agent_messages("agent-1")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[1]["content"], "world")

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
