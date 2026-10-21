"""Retirement is bounded, credential-blind, and cannot resurrect another account."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from unittest.mock import patch

from core import connection_retirement as retirement
from core.config_manager import ConfigManager
from core.knowledge_library import KnowledgeStore, KnowledgeService, KnowledgeError
from core.mcp_client import call_mcp_tool, list_mcp_server_tools, test_mcp_server_connection as check_mcp
from test_knowledge_library import TestProtector, WeKnoraFixture


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name
        self.root = Path(self.directory)
        self.patches = [patch("core.config_manager.get_app_data_dir", return_value=self.directory),
                        patch("core.config_manager.get_base_dir", return_value=self.directory)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def seed_legacy(self, selections):
        db = sqlite3.connect(self.root / "connections.sqlite3")
        try:
            db.execute("CREATE TABLE selections(capability TEXT, requirement TEXT, connection_id TEXT)")
            db.execute("CREATE TABLE connections(id TEXT, public TEXT, secret BLOB)")
            db.execute("INSERT INTO connections VALUES('old', '{}', ?)", (b"unreadable-private-credential",))
            db.executemany("INSERT INTO selections VALUES(?, ?, 'old')", selections)
            db.commit()
        finally:
            db.close()

    def seed_native(self):
        # Seed before first access, as an old installation would be found on disk.
        store = KnowledgeStore(self.directory, TestProtector())
        with store.connect(write=True) as db:
            for source in ("weknora", "tencent-docs"):
                public = json.dumps({"source": source, "connection_id": source, "user_id": "old-user", "tenant_id": "one"})
                db.execute("INSERT INTO source_connections VALUES(?,?,?)", (source, public, b"native-encrypted"))
            db.execute("INSERT INTO connection VALUES(1, '{}', ?)", (b"old-singleton-encrypted",))
            db.execute("INSERT INTO references_store VALUES('chat', '[{\"title\":\"keep\"}]')")
            db.execute("INSERT INTO uploads VALUES('upload', 'fingerprint', '{}', 1)")
        return store

    def test_retire_only_selected_source_and_never_decrypt(self):
        self.seed_legacy([("library:weknora", "weknora"), ("mcp:old-server", "default")])
        store = self.seed_native()
        original = (self.root / "connections.sqlite3").read_bytes()
        with patch.object(store, "crypt", side_effect=AssertionError("must not decrypt")):
            self.assertIsNone(store.connection())
            self.assertEqual(store.connection(source="tencent-docs")["user_id"], "old-user")
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT secret FROM retired_source_logins WHERE origin='source_connections'").fetchone()[0], b"native-encrypted")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM connection").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0], 1)
        self.assertEqual(store.references("chat"), [{"title": "keep"}])
        self.assertEqual(original, (self.root / "connections.sqlite3").read_bytes())
        self.assertIsNotNone(retirement.pending(self.directory, "mcp:old-server"))

    def test_native_login_survives_without_old_bindings(self):
        store = self.seed_native()
        self.assertEqual(store.connection()["user_id"], "old-user")
        self.assertFalse((self.root / "connections.sqlite3").exists())

    def test_native_mcp_transport_does_not_inherit_a_saved_server_binding(self):
        self.seed_legacy([("mcp:knowledge-lexiang", "default")])
        server = {"id": "knowledge-lexiang", "url": "https://example.invalid/mcp"}
        self.assertTrue(retirement.mcp_issue(server, ConfigManager()))
        with patch("core.connection_retirement.data_directory", return_value=self.directory):
            self.assertEqual(retirement.mcp_issue(server), "")
            self.assertEqual(retirement.mcp_issue({**server, "connection_ref": ""}), retirement.RECONFIGURE)

    def test_dangling_skill_binding_retires_its_source_without_old_credentials(self):
        self.seed_legacy([("knowledge-library", "tencent-docs")])
        store = self.seed_native()
        with closing(sqlite3.connect(self.root / "connections.sqlite3")) as db:
            db.execute("DELETE FROM connections")
            db.commit()
        self.assertIsNone(store.connection(source="tencent-docs"))
        self.assertIsNotNone(store.connection(source="weknora"))

    def test_repeated_and_concurrent_retirement_never_removes_new_login(self):
        self.seed_legacy([("library:weknora", "weknora")])
        store = self.seed_native()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: retirement.ensure_retired(self.directory), range(8)))
        service = KnowledgeService(store, WeKnoraFixture())
        service.login("http://localhost", "new@example.test", "password")
        current = service.snapshot()
        retirement.ensure_retired(self.directory)
        self.assertEqual(KnowledgeStore(self.directory, TestProtector()).connection(), store.connection())
        self.assertEqual(service.snapshot(), current)
        self.assertIsNone(retirement.pending(self.directory, "library:weknora"))

    def test_corrupt_legacy_fails_closed_then_can_retry(self):
        (self.root / "connections.sqlite3").write_bytes(b"invalid sqlite")
        store = self.seed_native()
        scope = KnowledgeService(store, WeKnoraFixture()).snapshot()
        self.assertTrue(scope["unavailable"])
        self.assertIn("原文件", scope["error"])
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM source_connections").fetchone()[0], 2)
        (self.root / "connections.sqlite3").unlink()
        self.seed_legacy([("library:weknora", "weknora")])
        retirement.ensure_retired(self.directory)
        self.assertIsNone(store.connection())

    def test_mid_transaction_failure_rolls_back_and_retry_preserves_history(self):
        self.seed_legacy([("library:weknora", "weknora")])
        store = self.seed_native()
        connect = sqlite3.connect
        class Interrupted(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == "INSERT INTO connection_retirement VALUES(1)":
                    raise sqlite3.OperationalError("controlled interruption")
                return super().execute(sql, *args, **kwargs)
        def interrupted_connect(*args, **kwargs):
            kwargs["factory"] = Interrupted
            return connect(*args, **kwargs)
        with patch.object(retirement.sqlite3, "connect", side_effect=interrupted_connect):
            with self.assertRaises(retirement.RetirementError):
                retirement.ensure_retired(self.directory)
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM source_connections").fetchone()[0], 2)
        retirement.ensure_retired(self.directory)
        self.assertIsNone(store.connection())
        self.assertEqual(store.references("chat"), [{"title": "keep"}])

    def mcp_config(self, *, explicit=False):
        config = ConfigManager()
        server = {"id": "old-server", "name": "Old MCP", "transport": "streamable_http", "url": "https://example.invalid/mcp"}
        if explicit:
            server["connection_ref"] = "old"
        config.set_mcp_servers([server])
        return config

    def test_mcp_blocks_discovery_call_and_only_explicit_saved_test_restores(self):
        self.seed_legacy([("mcp:old-server", "default")])
        config = self.mcp_config()
        server = config.get_mcp_servers()[0]
        async def discover(_server):
            return [{"name": "read", "input_schema": {"type": "object"}}]
        with patch("core.mcp_client._list_mcp_server_tools_async", side_effect=discover) as discovery:
            self.assertFalse(list_mcp_server_tools(server, config)["ok"])
            self.assertFalse(call_mcp_tool(server, "read", {}, config)["executed"])
            self.assertFalse(check_mcp(server, config, reconfiguration_test=True)["ok"])
            discovery.assert_not_called()
            config.prepare_mcp_reconfiguration("old-server")
            self.assertFalse(list_mcp_server_tools(server, config)["ok"])
            self.assertTrue(check_mcp(server, config, reconfiguration_test=True)["ok"])
            self.assertTrue(list_mcp_server_tools(server, config)["ok"])
        self.assertIsNone(retirement.pending(self.directory, "mcp:old-server"))

    def test_imported_ref_and_generated_config_cannot_bypass_reconfiguration(self):
        config = self.mcp_config(explicit=True)
        server = config.get_mcp_servers()[0]
        self.assertTrue(server["connection_setup_required"])
        self.assertNotIn("connection_ref", server)
        server.pop("connection_setup_required")
        config.upsert_mcp_servers([server])
        self.assertTrue(retirement.mcp_issue(server, config))
        config.prepare_mcp_reconfiguration("old-server")
        self.assertFalse(retirement.mcp_issue(config.get_mcp_servers()[0], config, testing=True))

    def test_failed_or_stale_mcp_test_never_unblocks(self):
        self.seed_legacy([("mcp:old-server", "default")])
        config = self.mcp_config()
        config.prepare_mcp_reconfiguration("old-server")
        server = config.get_mcp_servers()[0]
        async def fail(_server):
            raise RuntimeError("controlled outage")
        with patch("core.mcp_client._list_mcp_server_tools_async", side_effect=fail):
            self.assertFalse(check_mcp(server, config, reconfiguration_test=True)["ok"])
        self.assertTrue(retirement.mcp_issue(server, config))
        async def change_during_test(_server):
            config.set_mcp_servers([{**server, "url": "https://new.example.invalid/mcp"}])
            config.prepare_mcp_reconfiguration("old-server")
            return []
        with patch("core.mcp_client._list_mcp_server_tools_async", side_effect=change_during_test):
            self.assertFalse(check_mcp(server, config, reconfiguration_test=True)["ok"])
        self.assertIsNotNone(retirement.pending(self.directory, "mcp:old-server"))

    def test_retired_skill_declaration_is_not_a_prompt_patch_or_auth_bypass(self):
        from core.skill_manager import SkillManager
        manager = SkillManager.__new__(SkillManager)
        meta = {"name": "sample", "description": "Read a service"}
        original = {"name": "sample", "workflow": ["Read status"]}
        retired = {**original, "connection_requirements": [{"auth": "required"}]}
        self.assertEqual(manager._build_skill_prompt("sample", meta, "body", original, ["status"]),
                         manager._build_skill_prompt("sample", meta, "body", retired, ["status"]))
        self.assertEqual(retirement.skill_issue("sample", retired, ConfigManager(), draft=True), retirement.INCOMPATIBLE_SKILL)
        self.assertEqual(retirement.skill_issue("sample", {"connection_requirements": []}, ConfigManager()), retirement.INCOMPATIBLE_SKILL)

    def test_corrupt_journal_during_reconfiguration_reports_recovery_message(self):
        config = self.mcp_config(explicit=True)
        with patch.object(retirement.sqlite3, "connect", side_effect=sqlite3.OperationalError("disk is full")):
            self.assertIn("原文件和资料已保留", retirement.mcp_issue(config.config["mcp_servers"][0], config))

    def test_late_test_cannot_approve_a_config_saved_before_journal_commit(self):
        config = self.mcp_config(explicit=True)
        config.prepare_mcp_reconfiguration("old-server")
        tested = config.get_mcp_servers()[0]
        journal = retirement._journal
        @contextmanager
        def change_before_commit(directory):
            config.set_mcp_servers([{**tested, "url": "https://changed.example.invalid/mcp"}])
            with journal(directory) as db:
                yield db
        with patch.object(retirement, "_journal", change_before_commit):
            with self.assertRaises(retirement.RetirementError):
                retirement.complete_mcp_reconfiguration(tested, config)
        self.assertIsNotNone(retirement.pending(self.directory, "mcp:old-server"))

    def test_save_reinstates_verification_when_previous_test_finished_concurrently(self):
        config = self.mcp_config(explicit=True)
        config.prepare_mcp_reconfiguration("old-server")
        server = config.get_mcp_servers()[0]
        config.set_mcp_servers([{**server, "url": "https://new.example.invalid/mcp"}])
        journal = retirement._journal
        @contextmanager
        def finish_previous_test(directory):
            with journal(directory) as db:
                db.execute("DELETE FROM retired_capabilities WHERE capability='mcp:old-server'")
                yield db
        with patch.object(retirement, "_journal", finish_previous_test):
            config.prepare_mcp_reconfiguration("old-server")
        self.assertEqual(retirement.pending(self.directory, "mcp:old-server")["stage"], "verify")

    def test_logout_during_cloud_authentication_cannot_restore_login(self):
        from core.knowledge_sources import TencentDocsProvider, LexiangProvider
        from test_knowledge_library import CloudMcpFixture
        store = KnowledgeStore(self.directory, TestProtector())
        remote = CloudMcpFixture()
        for source, cls in (("tencent-docs", TencentDocsProvider), ("lexiang", LexiangProvider)):
            with self.subTest(source=source):
                provider = cls(store, remote, remote.call, remote.discover)
                call = provider._call
                def logout_in_flight(*args, **kwargs):
                    value = call(*args, **kwargs)
                    provider.logout()
                    return value
                if source == "tencent-docs":
                    provider.start_authorization()
                with patch.object(provider, "_call", side_effect=logout_in_flight):
                    with self.assertRaises(KnowledgeError) as caught:
                        if source == "tencent-docs":
                            provider.finish_authorization(confirmed=True)
                        else:
                            provider.connect("acme", "secret-token")
                self.assertEqual(caught.exception.code, "cancelled")
                self.assertIsNone(store.connection(source=source))

    def test_old_background_route_snapshot_is_rejected(self):
        from core.execution_authorization import ExecutionAuthorization, authorization_from_host_snapshot
        snapshot = ExecutionAuthorization("chat", "run", True, "").runtime_snapshot()
        self.assertNotIn("connection_routes", snapshot)
        authorization_from_host_snapshot(snapshot, session_id="chat", run_id="run")
        snapshot["connection_routes"] = {"mcp:old-server": "old"}
        with self.assertRaisesRegex(ValueError, "旧版后台任务"):
            authorization_from_host_snapshot(snapshot, session_id="chat", run_id="run")

    def test_cancelled_or_superseded_login_cannot_overwrite_account(self):
        store = KnowledgeStore(self.directory, TestProtector())
        fixture = WeKnoraFixture()
        service = KnowledgeService(store, fixture)
        service.login("http://localhost", "first@example.test", "password")
        original = store.connection()
        cancelled = threading.Event()
        request = fixture.request
        def cancel_in_flight(*args, **kwargs):
            result = request(*args, **kwargs)
            cancelled.set()
            return result
        with patch.object(fixture, "request", side_effect=cancel_in_flight):
            with self.assertRaises(KnowledgeError) as caught:
                service.login("http://localhost", "second@example.test", "password", cancelled=cancelled.is_set)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(store.connection(), original)
        first = store.begin_login("weknora")
        store.begin_login("weknora")
        with self.assertRaises(KnowledgeError):
            store.save_connection(original, {"token": "late"}, attempt=first)
        self.assertEqual(store.connection(), original)


if __name__ == "__main__":
    unittest.main()
