import copy
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")


from core.chat_storage import ChatStorage
from core.sidebar_search import SearchCancelled, search_tokens


class SidebarSearchStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = ChatStorage(str(Path(self.temp.name) / "history.sqlite"))

    def save(self, identity, text, **kwargs):
        self.storage.save_conversation(identity, [{"id": "m-" + identity, "role": "assistant", "content": text}], **kwargs)

    def message_hits(self, text):
        with self.storage._connect() as conn:
            return {row[0] for row in conn.execute("""
                SELECT m.id FROM sidebar_message_fts f JOIN messages m ON m.rowid=f.rowid
                WHERE sidebar_message_fts MATCH ?
            """, ('"' + search_tokens(text) + '"',))}

    def test_legacy_backfill_is_literal_and_preserves_messages_and_model_index(self):
        text = '截图归档完成 screenshots 100% ready_a "quoted" 😀\n换行'
        self.save("target", text)
        self.save("other", "1000 readyXa quoted")
        before = copy.deepcopy(self.storage.get_messages("target"))
        for query in ("图", "截图", "截图归档", "SCREEN", "%", "_", '"quoted"', "😀", "😀\n换行"):
            with self.subTest(query=query):
                self.assertEqual(self.storage.search_conversations(query), ["target"])
        self.assertEqual(self.storage.search_conversations("不存在"), [])
        self.assertEqual(self.storage.search_conversations('" OR *'), [])
        self.assertEqual(self.storage.search_conversations("   "), [])
        self.assertEqual(self.storage.get_messages("target"), before)
        with self.storage._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'screenshots'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT content FROM sidebar_message_fts WHERE sidebar_message_fts MATCH ?",
                                          ('"' + search_tokens("截图") + '"',)).fetchone()[0], None)

    def test_content_and_reasoning_do_not_form_a_cross_field_phrase(self):
        self.storage.save_conversation("reason", [{"role": "assistant", "content": "前半", "reasoning_content": "后半截图推理"}])
        self.assertEqual(self.storage.search_conversations("截图"), ["reason"])
        self.assertEqual(self.storage.search_conversations("前半后半"), [])

    def test_title_priority_literal_wildcards_and_archived_storage_contract(self):
        self.save("body", "Quarterly report")
        self.save("title", "unrelated", title="Quarterly report")
        self.save("archive", "归档截图", meta={"archived": True})
        self.save("wildcard", "unrelated", title="100% ready_a")
        self.assertEqual(self.storage.search_conversations("Quarterly", limit=1), ["title"])
        self.assertEqual(self.storage.search_conversations("%"), ["wildcard"])
        self.assertIn("archive", self.storage.search_conversations("截图"))

    def test_new_rows_rewrites_deletes_and_rollbacks_maintain_index(self):
        self.save("first", "旧版内容")
        self.storage.prepare_sidebar_search()
        self.save("second", "新建截图")
        self.assertEqual(self.storage.search_conversations("新建"), ["second"])
        self.save("first", "替换内容")
        self.assertEqual(self.storage.search_conversations("旧版"), [])
        self.assertEqual(self.storage.search_conversations("替换"), ["first"])
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.storage._connect() as conn:
                conn.execute("UPDATE messages SET content='不应提交' WHERE id='m-first'")
                raise RuntimeError("rollback")
        self.assertEqual(self.storage.search_conversations("不应提交"), [])
        self.assertEqual(self.storage.search_conversations("替换"), ["first"])
        self.storage.delete_conversation("first")
        self.assertEqual(self.storage.search_conversations("替换"), [])
        self.assertEqual(self.storage.search_conversations("新建"), ["second"])

    def test_partial_backfill_resumes_after_restart_and_interleaved_writes(self):
        messages = [{"id": f"m{i}", "role": "assistant", "content": f"原始内容{i}"} for i in range(160)]
        self.storage.save_conversation("legacy", messages)
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            return calls > 100

        with self.assertRaises(SearchCancelled):
            self.storage.prepare_sidebar_search(cancel)
        with self.storage._connect() as conn:
            cursor, upper = conn.execute("SELECT cursor,upper_bound FROM sidebar_search_state").fetchone()
            self.assertGreater(cursor, 0)
            self.assertLess(cursor, upper)
            conn.execute("UPDATE messages SET content='已索引处修改' WHERE id='m0'")
            conn.execute("UPDATE messages SET content='未索引处修改' WHERE id='m150'")
            conn.execute("DELETE FROM messages WHERE id IN ('m1','m151')")
        self.save("new", "并发新建")
        self.storage = ChatStorage(self.storage.db_path)
        self.storage.prepare_sidebar_search()
        self.assertEqual(self.message_hits("已索引处"), {"m0"})
        self.assertEqual(self.message_hits("未索引处"), {"m150"})
        self.assertEqual(self.message_hits("并发新建"), {"m-new"})
        self.assertNotIn("m1", self.message_hits("原始内容"))
        self.assertNotIn("m151", self.message_hits("原始内容"))
        with self.storage._connect() as conn:
            conn.execute("INSERT INTO sidebar_message_fts(sidebar_message_fts) VALUES('integrity-check')")

    def test_concurrent_search_build_and_save_do_not_lose_or_duplicate_messages(self):
        self.storage.save_conversation("legacy", [
            {"id": f"long{i}", "role": "assistant", "content": "初始历史" * 150} for i in range(600)
        ])
        entered = threading.Event()
        errors = []

        def build():
            try:
                self.storage.prepare_sidebar_search(lambda: entered.set() or False)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=build)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            self.save("live", "边建索引边保存")
        finally:
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.storage.search_conversations("边保存"), ["live"])
        self.assertEqual(len(self.storage.get_messages("live")), 1)
        self.assertEqual(len(self.storage.get_messages("legacy")), 600)

    def test_rebuild_is_explicit_and_does_not_modify_ledger(self):
        self.save("existing", "可重建内容")
        self.storage.prepare_sidebar_search()
        before = self.storage.get_messages("existing")
        self.storage.rebuild_sidebar_search()
        self.assertEqual(self.storage.search_conversations("重建"), ["existing"])
        self.assertEqual(self.storage.get_messages("existing"), before)

    def test_corrupt_derived_index_can_be_rebuilt_from_unchanged_ledger(self):
        self.save("existing", "可以恢复的搜索内容")
        self.storage.prepare_sidebar_search()
        before = self.storage.get_messages("existing")
        with self.storage._connect() as conn:
            conn.execute("UPDATE sidebar_message_fts_data SET block=x'00' WHERE id>10")
        with self.assertRaises(sqlite3.DatabaseError):
            self.storage.search_conversations("恢复")
        self.storage.rebuild_sidebar_search()
        self.assertEqual(self.storage.search_conversations("恢复"), ["existing"])
        self.assertEqual(self.storage.get_messages("existing"), before)

    def test_sidebar_search_keeps_feishu_display_title_matching(self):
        self.save("im", "unrelated", title="stored title")
        with self.storage._connect() as conn:
            conn.execute("INSERT INTO im_sessions(provider, im_user_id, conversation_id) VALUES ('feishu','test-user','im')")
        rows, _ = self.storage.search_sidebar_conversations("飞书")
        self.assertEqual([row["id"] for row in rows], ["im"])

    def test_query_plan_uses_index_and_never_scans_message_text(self):
        self.save("existing", "中文截图")
        self.storage.prepare_sidebar_search()
        statements = []
        original = self.storage._connect

        @contextmanager
        def traced():
            with original() as conn:
                conn.set_trace_callback(statements.append)
                yield conn

        with patch.object(self.storage, "_connect", traced):
            self.assertEqual(self.storage.search_conversations("截图"), ["existing"])
        self.assertFalse(any("content LIKE" in sql for sql in statements))
        with original() as conn:
            plan = [row[3] for row in conn.execute("""EXPLAIN QUERY PLAN
                SELECT m.conversation_id FROM sidebar_message_fts f JOIN messages m ON m.rowid=f.rowid
                WHERE sidebar_message_fts MATCH ?""", ('"' + search_tokens("截图") + '"',))]
        self.assertTrue(any("VIRTUAL TABLE INDEX" in row for row in plan))
        self.assertTrue(any("SEARCH m USING INTEGER PRIMARY KEY" in row for row in plan))

    def test_sidebar_filters_projects_and_reads_only_matching_summaries(self):
        project = str(Path(self.temp.name) / "folder")
        self.save("project", "nothing", meta={"workspace_dir": project, "workspace_source": "project"})
        self.save("chat", "nothing", title="目标标题")
        self.save("body", "目标正文")
        self.save("unrelated", "nothing")
        self.save("archived", "目标正文", meta={"archived": True})
        self.save("team", "目标正文", meta={"team_space_required": True})
        with patch.object(self.storage, "list_conversation_summaries", wraps=self.storage.list_conversation_summaries) as summaries:
            rows, _ = self.storage.search_sidebar_conversations("目标", [{"path": project, "name": "目标项目"}])
        self.assertEqual({row["id"] for row in rows}, {"project", "chat", "body"})
        self.assertEqual(set(summaries.call_args.kwargs["conversation_ids"]), {"project", "chat", "body"})
        rows, _ = self.storage.search_sidebar_conversations("目标", [{"path": project, "name": "目标项目", "hidden": True}])
        self.assertNotIn("project", {row["id"] for row in rows})

    def test_summary_filter_handles_more_than_sqlite_parameter_batch(self):
        with self.storage._connect() as conn:
            conn.executemany("INSERT INTO conversations(id,title,updated_at) VALUES (?,?,?)",
                             [(f"c{i}", "title", i) for i in range(420)])
        ids = [f"c{i}" for i in range(410)]
        rows = self.storage.list_conversation_summaries(conversation_ids=ids, offset=3, limit=5)
        self.assertEqual([row["id"] for row in rows], [f"c{i}" for i in range(406, 401, -1)])
        self.assertEqual(self.storage.list_conversation_summaries(conversation_ids=[]), [])
