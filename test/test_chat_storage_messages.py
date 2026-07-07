import os
import shutil
import sqlite3
import tempfile
import unittest
import json
from contextlib import closing

from core.chat_storage import ChatStorage


class TestChatStorageMessages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_message_roundtrip_preserves_structured_fields(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation(
            "conv-1",
            [
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "thinking",
                    "content_parts": [{"type": "text", "text": "hello"}],
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "demo", "arguments": {"value": 1}},
                        }
                    ],
                },
                {
                    "id": "t1",
                    "role": "tool",
                    "tool_call_id": "tool-1",
                    "content": "{\"ok\": true}",
                    "meta": {"duration": 0.5},
                    "result_obj": {"ok": True},
                },
            ],
            title="demo",
            meta={"workspace_dir": "D:/demo"},
        )
        messages = storage.get_messages("conv-1")
        self.assertEqual(messages[0]["content_parts"][0]["text"], "hello")
        self.assertEqual(messages[1]["meta"]["duration"], 0.5)
        self.assertEqual(messages[1]["result_obj"], {"ok": True})
        self.assertTrue(messages[0]["id"])
        self.assertTrue(messages[1]["id"])

    def test_messages_without_ids_are_assigned_stable_ids(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation(
            "conv-no-id",
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
            title="no-id",
        )

        first_read = storage.get_messages("conv-no-id")
        second_read = storage.get_messages("conv-no-id")

        self.assertTrue(first_read[0]["id"])
        self.assertTrue(first_read[1]["id"])
        self.assertEqual(first_read[0]["id"], second_read[0]["id"])
        self.assertEqual(first_read[1]["id"], second_read[1]["id"])

    def test_save_conversation_appends_incrementally_and_falls_back_on_edit(self):
        storage = ChatStorage(self.db_path)
        first = {"id": "m1", "role": "user", "content": "hello"}
        second = {"id": "m2", "role": "assistant", "content": "world"}

        storage.save_conversation("conv-incremental", [dict(first)], title="demo")
        storage.save_conversation("conv-incremental", [dict(first), dict(second)], title="demo")

        messages = storage.get_messages("conv-incremental")
        self.assertEqual([msg["id"] for msg in messages], ["m1", "m2"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, position FROM messages WHERE conversation_id = ? ORDER BY position",
                ("conv-incremental",),
            ).fetchall()
        self.assertEqual(rows, [("m1", 0), ("m2", 1)])

        edited_first = dict(first)
        edited_first["content"] = "hello edited"
        storage.save_conversation("conv-incremental", [edited_first, dict(second)], title="demo")

        messages = storage.get_messages("conv-incremental")
        self.assertEqual(messages[0]["content"], "hello edited")
        self.assertEqual([msg["id"] for msg in messages], ["m1", "m2"])

    def test_update_conversation_meta_preserves_activity_time(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation(
            "conv-pin",
            [{"id": "u1", "role": "user", "content": "hello"}],
            title="demo",
            meta={
                "workspace_dir": "D:/demo",
                "conversation_branch": {"parent_session_id": "legacy"},
            },
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE conversations SET updated_at = 123 WHERE id = ?", ("conv-pin",))
            conn.commit()

        record = storage.update_conversation_meta("conv-pin", {"pinned": True})

        self.assertEqual(record["updated_at"], 123)
        self.assertTrue(record["meta"]["pinned"])
        self.assertEqual(record["meta"]["workspace_dir"], "D:/demo")
        self.assertEqual(record["meta"]["conversation_branch"]["parent_session_id"], "legacy")

    def test_list_conversation_summaries_supports_limit_and_offset(self):
        storage = ChatStorage(self.db_path)
        for index in range(5):
            conversation_id = f"conv-{index + 1}"
            storage.save_conversation(
                conversation_id,
                [{"id": f"u{index}", "role": "user", "content": conversation_id}],
                title=conversation_id,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            for index in range(5):
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (index + 1, f"conv-{index + 1}"),
                )
            conn.commit()

        page = storage.list_conversation_summaries(limit=2, offset=1)

        self.assertEqual([item["id"] for item in page], ["conv-4", "conv-3"])

    def test_migrate_legacy_json_histories_imports_missing_sessions(self):
        storage = ChatStorage(self.db_path)
        legacy_path = os.path.join(self.temp_dir, "chat_history_legacy-session.json")
        with open(legacy_path, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {"role": "user", "content": "legacy hello"},
                    {"role": "assistant", "content": "legacy world"},
                ],
                handle,
                ensure_ascii=False,
            )

        migrated = storage.migrate_legacy_json_histories()

        self.assertEqual(migrated, 1)
        record = storage.get_conversation_record("legacy-session")
        self.assertIsNotNone(record)
        self.assertEqual(record["title"], "legacy hello")
        self.assertTrue(record["meta"]["migrated_from_legacy_json"])

    def test_existing_database_is_migrated_with_new_message_columns(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    reasoning_content TEXT,
                    token_count INTEGER,
                    tool_call_id TEXT,
                    position INTEGER,
                    created_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE agent_messages (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    reasoning_content TEXT,
                    token_count INTEGER,
                    tool_call_id TEXT,
                    position INTEGER,
                    created_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at INTEGER,
                    updated_at INTEGER,
                    status TEXT,
                    meta TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE agents (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    parent_message_id TEXT,
                    name TEXT,
                    status TEXT NOT NULL,
                    is_subagent INTEGER NOT NULL DEFAULT 1,
                    fork_context INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER,
                    updated_at INTEGER,
                    started_at INTEGER,
                    finished_at INTEGER,
                    last_error TEXT,
                    last_result TEXT,
                    meta TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE im_sessions (
                    provider TEXT NOT NULL,
                    im_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_at INTEGER,
                    updated_at INTEGER,
                    PRIMARY KEY (provider, im_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE im_daily_summaries (
                    provider TEXT NOT NULL,
                    im_user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    summary_date TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    summary_text TEXT,
                    source_message_upto_pos INTEGER,
                    token_estimate INTEGER,
                    created_at INTEGER,
                    updated_at INTEGER,
                    PRIMARY KEY (provider, im_user_id, chat_id, summary_date)
                )
                """
            )
            conn.commit()

        storage = ChatStorage(self.db_path)
        with storage._connect() as conn:
            message_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            agent_message_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(agent_messages)").fetchall()
            }
        for column_name in ("content_parts", "meta", "result_obj"):
            self.assertIn(column_name, message_columns)
            self.assertIn(column_name, agent_message_columns)

    def test_legacy_plan_meta_is_cleaned_on_load_and_save(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at INTEGER,
                    updated_at INTEGER,
                    status TEXT,
                    meta TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO conversations (id, title, created_at, updated_at, status, meta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "conv-plan",
                    "legacy",
                    1,
                    1,
                    "draft",
                    json.dumps(
                        {
                            "workspace_dir": "D:/demo",
                            "plan_mode_enabled": True,
                            "plan_document": "# Old Plan",
                            "pending_plan_questions": [{"id": "scope", "question": "Scope?"}],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()

        storage = ChatStorage(self.db_path)
        meta = storage.get_conversation_meta("conv-plan")
        self.assertEqual(meta, {"workspace_dir": "D:/demo"})

        storage.save_conversation(
            "conv-new",
            [{"role": "user", "content": "hello"}],
            meta={"workspace_dir": "D:/demo", "plan_mode_enabled": True, "plan_phase": "exploring"},
        )
        new_meta = storage.get_conversation_meta("conv-new")
        self.assertEqual(new_meta, {"workspace_dir": "D:/demo"})

    def test_search_conversations_matches_titles_and_message_content(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation(
            "conv-title",
            [{"role": "user", "content": "unrelated message"}],
            title="Quarterly report cleanup",
        )
        storage.save_conversation(
            "conv-content",
            [{"role": "assistant", "content": "The screenshots were grouped by date."}],
            title="Image task",
        )
        storage.save_conversation(
            "conv-archived",
            [{"role": "user", "content": "archived details"}],
            title="Archived reference",
            meta={"archived": True},
        )
        storage.save_conversation(
            "conv-cjk",
            [{"role": "assistant", "content": "截图归档完成。"}],
            title="图片整理",
        )

        self.assertIn("conv-title", storage.search_conversations("Quarterly"))
        self.assertIn("conv-content", storage.search_conversations("screenshots"))
        self.assertIn("conv-archived", storage.search_conversations("archived"))
        self.assertIn("conv-cjk", storage.search_conversations("截图"))

    def test_project_grouping_and_unassigned_conversations(self):
        storage = ChatStorage(self.db_path)
        workspace = os.path.join(self.temp_dir, "workspace")
        chat_workspace = os.path.join(self.temp_dir, "conversation_workspaces", "chat-conv")
        os.makedirs(workspace)
        os.makedirs(chat_workspace)
        storage.save_conversation(
            "project-conv",
            [{"role": "user", "content": "project task"}],
            title="Project task",
            meta={"workspace_dir": workspace, "workspace_source": "project"},
        )
        storage.save_conversation(
            "chat-conv",
            [{"role": "user", "content": "direct chat"}],
            title="Direct chat",
            meta={"workspace_dir": chat_workspace, "workspace_source": "chat"},
        )
        storage.save_conversation(
            "plain-conv",
            [{"role": "user", "content": "plain chat"}],
            title="Plain chat",
        )

        grouped = storage.list_conversations_by_workspace()
        workspace_key = os.path.normcase(os.path.normpath(os.path.abspath(workspace)))
        self.assertEqual(grouped[workspace_key][0]["id"], "project-conv")
        self.assertNotIn(os.path.normcase(os.path.normpath(os.path.abspath(chat_workspace))), grouped)
        self.assertCountEqual(
            [item["id"] for item in storage.list_unassigned_conversations()],
            ["chat-conv", "plain-conv"],
        )

    def test_archive_conversations_for_workspace_only_archives_target(self):
        storage = ChatStorage(self.db_path)
        workspace = os.path.join(self.temp_dir, "workspace")
        other_workspace = os.path.join(self.temp_dir, "other")
        os.makedirs(workspace)
        os.makedirs(other_workspace)
        storage.save_conversation("target", [{"role": "user", "content": "a"}], meta={"workspace_dir": workspace, "workspace_source": "project"})
        storage.save_conversation("other", [{"role": "user", "content": "b"}], meta={"workspace_dir": other_workspace, "workspace_source": "project"})
        storage.save_conversation("chat", [{"role": "user", "content": "c"}], meta={"workspace_dir": workspace, "workspace_source": "chat"})

        self.assertEqual(storage.archive_conversations_for_workspace(workspace), 1)
        self.assertTrue(storage.get_conversation_meta("target").get("archived"))
        self.assertFalse(storage.get_conversation_meta("other").get("archived"))
        self.assertFalse(storage.get_conversation_meta("chat").get("archived"))

    def test_list_and_restore_archived_conversations(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation("archived", [{"role": "user", "content": "a"}], title="Archived", meta={"archived": True})
        storage.save_conversation("active", [{"role": "user", "content": "b"}], title="Active")

        self.assertEqual([item["id"] for item in storage.list_archived_conversations()], ["archived"])

        record = storage.restore_conversation("archived")

        self.assertFalse(record["meta"]["archived"])
        self.assertEqual(storage.list_archived_conversations(), [])


if __name__ == "__main__":
    unittest.main()
