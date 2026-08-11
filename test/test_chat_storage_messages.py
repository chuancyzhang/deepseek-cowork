import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import json
from contextlib import closing
from unittest.mock import patch

from core.chat_storage import ChatStorage, ConversationWriteConflict
from core.llm.deepseek import DEEPSEEK_RESPONSES_REPLAY_META_KEY
from core.llm.responses_replay import RESPONSES_REPLAY_META_KEY


class TestChatStorageMessages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_conversation_rolls_back_summary_when_message_write_fails(self):
        storage = ChatStorage(self.db_path)
        with patch.object(
            storage,
            "_replace_messages_in_connection",
            side_effect=RuntimeError("simulated message failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated message failure"):
                storage.save_conversation(
                    "atomic-conversation",
                    [{"id": "m1", "role": "user", "content": "must be atomic"}],
                    title="Atomic",
                )
        self.assertIsNone(storage.get_conversation_record("atomic-conversation"))

    def test_safe_save_only_appends_and_rejects_divergent_snapshots(self):
        storage = ChatStorage(self.db_path)
        first = {"id": "u1", "role": "user", "content": "hello"}
        second = {"id": "a1", "role": "assistant", "content": "world"}

        created = storage.save_conversation_safely("safe", [first], title="Safe")
        appended = storage.save_conversation_safely("safe", [first, second], title="Safe")
        stale = storage.save_conversation_safely("safe", [first], title="Stale")

        self.assertEqual(created["outcome"], "created")
        self.assertEqual(appended["outcome"], "appended")
        self.assertEqual(stale["outcome"], "stale")
        self.assertEqual([item["id"] for item in storage.get_messages("safe")], ["u1", "a1"])
        self.assertEqual(storage.get_conversation_record("safe")["title"], "Safe")

        with self.assertRaises(ConversationWriteConflict):
            storage.save_conversation_safely(
                "safe",
                [{"id": "u1", "role": "user", "content": "edited"}, second],
            )
        self.assertEqual(
            [item["content"] for item in storage.get_messages("safe")],
            ["hello", "world"],
        )

    def test_safe_save_reconciles_legacy_ui_only_rows_before_append(self):
        storage = ChatStorage(self.db_path)
        first = {"id": "u1", "role": "user", "content": "hello"}
        interrupted = {
            "id": "ui-interrupted",
            "role": "assistant",
            "content": "任务已停止",
            "meta": {"ui_only": True, "ui_reply_kind": "interrupted"},
        }
        next_user = {"id": "u2", "role": "user", "content": "continue"}
        storage.save_conversation("legacy-ui", [first, interrupted], title="Legacy UI")

        result = storage.save_conversation_safely(
            "legacy-ui",
            [first, next_user],
            title="Reconciled",
        )

        self.assertEqual(result["outcome"], "appended")
        self.assertEqual(
            [item["id"] for item in storage.get_messages("legacy-ui")],
            ["u1", "u2"],
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            positions = conn.execute(
                "SELECT position FROM messages WHERE conversation_id = ? ORDER BY position",
                ("legacy-ui",),
            ).fetchall()
        self.assertEqual(positions, [(0,), (1,)])

    def test_safe_save_does_not_remove_ui_rows_when_content_still_diverges(self):
        storage = ChatStorage(self.db_path)
        stored = [
            {"id": "u1", "role": "user", "content": "original"},
            {
                "id": "ui-error",
                "role": "assistant",
                "content": "local error",
                "meta": {"ui_only": True, "ui_reply_kind": "error"},
            },
        ]
        storage.save_conversation("real-conflict", stored, title="Conflict")

        with self.assertRaises(ConversationWriteConflict):
            storage.save_conversation_safely(
                "real-conflict",
                [
                    {"id": "u1", "role": "user", "content": "edited"},
                    {"id": "u2", "role": "user", "content": "continue"},
                ],
            )

        self.assertEqual(
            [item["id"] for item in storage.get_messages("real-conflict")],
            ["u1", "ui-error"],
        )

    def test_safe_save_never_replaces_existing_history_with_empty_snapshot(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation_safely(
            "non-empty",
            [{"id": "u1", "role": "user", "content": "keep"}],
        )

        result = storage.save_conversation_safely("non-empty", [])

        self.assertEqual(result["outcome"], "stale")
        self.assertEqual(
            [item["content"] for item in storage.get_messages("non-empty")],
            ["keep"],
        )

    def test_safe_save_does_not_change_sqlite_schema(self):
        storage = ChatStorage(self.db_path)

        def schema_snapshot():
            with closing(sqlite3.connect(self.db_path)) as conn:
                master = conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
                tables = {
                    row[1]: conn.execute(f'PRAGMA table_info("{row[1]}")').fetchall()
                    for row in master
                    if row[0] == "table"
                }
                indexes = conn.execute("PRAGMA index_list(messages)").fetchall()
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            return master, tables, indexes, user_version

        before = schema_snapshot()
        storage.save_conversation_safely(
            "schema-stable",
            [{"id": "u1", "role": "user", "content": "hello"}],
        )
        storage.save_conversation_safely(
            "schema-stable",
            [
                {"id": "u1", "role": "user", "content": "hello"},
                {"id": "a1", "role": "assistant", "content": "done"},
            ],
        )

        self.assertEqual(schema_snapshot(), before)

    def test_reading_legacy_rows_does_not_rewrite_them_and_safe_append_still_works(self):
        storage = ChatStorage(self.db_path)
        storage.upsert_conversation("legacy-read-only", title="Legacy")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, position, created_at) "
                "VALUES (NULL, ?, 'user', 'legacy message', 0, 1)",
                ("legacy-read-only",),
            )
            conn.commit()
            before = conn.execute(
                "SELECT rowid, id, role, content, position, created_at "
                "FROM messages WHERE conversation_id = ?",
                ("legacy-read-only",),
            ).fetchall()

        messages = storage.get_messages("legacy-read-only")
        self.assertTrue(messages[0].get("id"))

        with closing(sqlite3.connect(self.db_path)) as conn:
            after_read = conn.execute(
                "SELECT rowid, id, role, content, position, created_at "
                "FROM messages WHERE conversation_id = ?",
                ("legacy-read-only",),
            ).fetchall()
        self.assertEqual(after_read, before)

        storage.save_conversation_safely(
            "legacy-read-only",
            messages + [{"id": "a1", "role": "assistant", "content": "new answer"}],
            title="Legacy",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, content FROM messages WHERE conversation_id = ? ORDER BY position",
                ("legacy-read-only",),
            ).fetchall()
        self.assertIsNone(rows[0][0])
        self.assertEqual(rows[1], ("a1", "new answer"))

    def test_concurrent_divergent_appends_preserve_the_winning_branch(self):
        storage = ChatStorage(self.db_path)
        base = {"id": "u1", "role": "user", "content": "base"}
        storage.save_conversation_safely("concurrent", [base])
        barrier = threading.Barrier(3)
        outcomes = []

        def save_branch(message):
            barrier.wait()
            try:
                result = storage.save_conversation_safely("concurrent", [base, message])
                outcomes.append(result["outcome"])
            except ConversationWriteConflict:
                outcomes.append("conflict")

        threads = [
            threading.Thread(
                target=save_branch,
                args=({"id": "a1", "role": "assistant", "content": "branch-a"},),
            ),
            threading.Thread(
                target=save_branch,
                args=({"id": "a2", "role": "assistant", "content": "branch-b"},),
            ),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)

        self.assertCountEqual(outcomes, ["appended", "conflict"])
        stored = storage.get_messages("concurrent")
        self.assertEqual(len(stored), 2)
        self.assertIn(stored[-1]["content"], {"branch-a", "branch-b"})

    def test_cross_conversation_message_id_conflict_is_remapped(self):
        storage = ChatStorage(self.db_path)
        storage.save_conversation(
            "first-conversation",
            [{"id": "shared-id", "role": "assistant", "content": "first"}],
            title="First",
        )
        storage.save_conversation(
            "second-conversation",
            [{"id": "shared-id", "role": "assistant", "content": "second"}],
            title="Second",
        )
        first = storage.get_messages("first-conversation")[0]
        second = storage.get_messages("second-conversation")[0]
        self.assertEqual(first.get("id"), "shared-id")
        self.assertNotEqual(second.get("id"), "shared-id")
        self.assertEqual(
            (second.get("meta") or {}).get("original_message_id"),
            "shared-id",
        )
        self.assertTrue((second.get("meta") or {}).get("message_id_remapped"))

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

    def test_message_roundtrip_preserves_deepseek_responses_replay_items(self):
        storage = ChatStorage(self.db_path)
        replay_items = [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "thinking"}],
            },
            {
                "id": "ws_1",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "latest"},
            },
        ]
        storage.save_conversation(
            "responses-replay",
            [{
                "id": "assistant-1",
                "role": "assistant",
                "content": "done",
                "reasoning_content": "thinking",
                "meta": {DEEPSEEK_RESPONSES_REPLAY_META_KEY: replay_items},
            }],
            title="responses",
        )

        message = storage.get_messages("responses-replay")[0]

        self.assertEqual(
            message["meta"][DEEPSEEK_RESPONSES_REPLAY_META_KEY],
            replay_items,
        )

    def test_message_roundtrip_preserves_generic_responses_replay_items(self):
        storage = ChatStorage(self.db_path)
        replay_items = [{
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "done", "annotations": []}],
        }]
        storage.save_conversation(
            "generic-responses-replay",
            [{
                "id": "assistant-1",
                "role": "assistant",
                "content": "done",
                "meta": {RESPONSES_REPLAY_META_KEY: replay_items},
            }],
            title="responses",
        )

        message = storage.get_messages("generic-responses-replay")[0]

        self.assertEqual(message["meta"][RESPONSES_REPLAY_META_KEY], replay_items)

    def test_normalize_messages_preserves_duplicate_content_and_order(self):
        storage = ChatStorage(self.db_path)
        messages = [
            {"id": "u1", "role": "user", "content": "same"},
            {"id": "u2", "role": "user", "content": "same"},
            {"id": "a1", "role": "assistant", "content": "same"},
        ]

        normalized = storage.normalize_messages(messages)

        self.assertEqual([message["id"] for message in normalized], ["u1", "u2", "a1"])
        self.assertEqual([message["role"] for message in normalized], ["user", "user", "assistant"])

    def test_normalize_messages_repairs_duplicate_message_ids_without_dropping_history(self):
        storage = ChatStorage(self.db_path)

        normalized = storage.normalize_messages(
            [
                {"id": "same-id", "role": "user", "content": "first"},
                {"id": "same-id", "role": "assistant", "content": "second"},
            ],
            conversation_id="duplicate-history",
        )

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["id"], "same-id")
        self.assertNotEqual(normalized[1]["id"], "same-id")
        self.assertEqual([message["content"] for message in normalized], ["first", "second"])
        self.assertEqual(
            normalized[1]["meta"]["original_message_id"],
            "same-id",
        )
        self.assertTrue(normalized[1]["meta"]["message_id_remapped"])

    def test_legacy_history_with_duplicate_ids_loads_all_messages(self):
        storage = ChatStorage(self.db_path)
        legacy_path = os.path.join(self.temp_dir, "chat_history_legacy-duplicate.json")
        with open(legacy_path, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {"id": "same-id", "role": "user", "content": "第一条"},
                    {"id": "same-id", "role": "assistant", "content": "第二条"},
                ],
                handle,
                ensure_ascii=False,
            )

        transcripts = storage._legacy_json_transcripts(set())

        self.assertEqual(len(transcripts), 1)
        messages = transcripts[0]["messages"]
        self.assertEqual([item["content"] for item in messages], ["第一条", "第二条"])
        self.assertNotEqual(messages[0]["id"], messages[1]["id"])

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
