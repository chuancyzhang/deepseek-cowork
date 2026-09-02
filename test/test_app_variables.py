import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import RUN_MODE_EXECUTION
from core.config_manager import normalize_mcp_servers
from core.mcp_client import _redact_exact, call_mcp_tool, prepare_mcp_server_config
from core.skill_manager import SkillManager
from core.variable_store import (
    VARIABLE_KIND_SECRET,
    VARIABLE_KIND_TEXT,
    VariableStore,
    VariableStoreError,
)


class _TestProtector:
    def protect(self, value):
        return b"protected:" + bytes(value)[::-1]

    def unprotect(self, value):
        raw = bytes(value)
        if not raw.startswith(b"protected:"):
            raise ValueError("corrupt")
        return raw[len(b"protected:"):][::-1]


class _ConfigStub:
    def __init__(self, store):
        self.store = store

    def get_variable_store(self):
        return self.store


class TestAppVariables(unittest.TestCase):
    def test_text_secret_storage_and_previous_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir, protector=_TestProtector())
            text = store.upsert("workspace", VARIABLE_KIND_TEXT, "D:/alpha")
            secret = store.upsert("token", VARIABLE_KIND_SECRET, "never-write-this")
            with open(store.path, "r", encoding="utf-8") as stream:
                disk_text = stream.read()
            self.assertNotIn("never-write-this", disk_text)
            self.assertEqual(store.get_text_exact("workspace"), "D:/alpha")
            self.assertIsNone(store.get_text_exact("token"))
            self.assertEqual(store.resolve_for_binding(secret["id"]), "never-write-this")
            with self.assertRaisesRegex(ValueError, "仍被引用"):
                store.delete(secret["id"], ["MCP Remote · headers.Authorization"])

            store.upsert("workspace", VARIABLE_KIND_TEXT, "D:/beta", variable_id=text["id"])
            self.assertEqual(store.get_text_exact("workspace"), "D:/beta")
            store.restore_previous()
            self.assertEqual(store.get_text_exact("workspace"), "D:/alpha")

    def test_corrupt_store_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "app_variables.json")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("{broken")
            with self.assertRaisesRegex(VariableStoreError, "读取失败"):
                VariableStore(temp_dir, protector=_TestProtector()).list_public()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_windows_dpapi_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir)
            saved = store.upsert("real-token", VARIABLE_KIND_SECRET, "dpapi-secret")
            self.assertEqual(store.resolve_for_binding(saved["id"]), "dpapi-secret")
            with open(store.path, "r", encoding="utf-8") as stream:
                self.assertNotIn("dpapi-secret", stream.read())

    def test_mcp_binding_normalization_resolution_and_redaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir, protector=_TestProtector())
            secret = store.upsert("api-token", VARIABLE_KIND_SECRET, "secret-value")
            server = normalize_mcp_servers([{
                "name": "Remote",
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "variable_bindings": {
                    "headers": {
                        "Authorization": {"variable_id": secret["id"], "scheme": "bearer"}
                    }
                },
            }])[0]
            prepared = prepare_mcp_server_config(server, _ConfigStub(store))
            self.assertEqual(prepared["headers"]["Authorization"], "Bearer secret-value")
            self.assertNotIn("secret-value", json.dumps(server))
            redacted = _redact_exact("stderr: Bearer secret-value", prepared["_resolved_secrets"])
            self.assertNotIn("secret-value", redacted)
            with patch(
                "core.mcp_client._call_mcp_tool_async",
                new=AsyncMock(return_value={"text": "secret-value", "is_error": False}),
            ):
                result = call_mcp_tool(server, "demo", config_manager=_ConfigStub(store))
            self.assertEqual(result["text"], "<redacted-secret>")
            server["variable_bindings"]["headers"]["Authorization"]["variable_id"] = "missing"
            with self.assertRaisesRegex(ValueError, "引用不存在"):
                prepare_mcp_server_config(server, _ConfigStub(store))

    def test_mcp_rejects_literal_binding_conflict(self):
        with self.assertRaisesRegex(ValueError, "冲突"):
            normalize_mcp_servers([{
                "name": "Local",
                "transport": "stdio",
                "command": "demo",
                "env": {"TOKEN": "literal"},
                "variable_bindings": {"env": {"TOKEN": {"variable_id": "v1"}}},
            }])

    def test_lookup_is_deferred_and_denied_outside_local_main_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir, protector=_TestProtector())
            store.upsert("plain", VARIABLE_KIND_TEXT, "visible")
            store.upsert("secret", VARIABLE_KIND_SECRET, "hidden")
            manager = SkillManager(
                workspace_dir=temp_dir,
                config_manager=_ConfigStub(store),
                auto_load=False,
                load_mcp_tools=False,
            )
            local_context = {
                "run_context": {"mode": RUN_MODE_EXECUTION, "app_variable_access": True},
                "discovered_tool_names": set(),
            }
            initial_names = {
                item["function"]["name"]
                for item in manager.get_tool_definitions(run_context=local_context["run_context"])
            }
            self.assertNotIn("lookup_app_variable", initial_names)
            initial_definitions = manager.get_tool_definitions(run_context=local_context["run_context"])
            store.upsert("later", VARIABLE_KIND_TEXT, "does-not-change-tool-prefix")
            self.assertEqual(
                manager.get_tool_definitions(run_context=local_context["run_context"]),
                initial_definitions,
            )
            search = manager.call_tool("tool_search", {"query": "application variable"}, context=local_context)
            self.assertIn("lookup_app_variable", search["discovered_tools"])
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "plain"}, context=local_context)["value"],
                "visible",
            )
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "secret"}, context=local_context)["status"],
                "restricted",
            )
            denied = manager.call_tool(
                "tool_search",
                {"query": "application variable"},
                context={"run_context": {"mode": RUN_MODE_EXECUTION}, "discovered_tool_names": set()},
            )
            self.assertNotIn("lookup_app_variable", denied["discovered_tools"])
            subagent = manager.call_tool(
                "tool_search",
                {"query": "application variable"},
                context={**local_context, "discovered_tool_names": set(), "is_subagent": True},
            )
            self.assertNotIn("lookup_app_variable", subagent["discovered_tools"])


if __name__ == "__main__":
    unittest.main()
