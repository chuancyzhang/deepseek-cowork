import os
import sys
import tempfile
import unittest


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import RUN_MODE_EXECUTION
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
            store.upsert("token", VARIABLE_KIND_SECRET, "never-write-this")
            with open(store.path, "r", encoding="utf-8") as stream:
                disk_text = stream.read()
            self.assertNotIn("never-write-this", disk_text)
            self.assertEqual(store.get_text_exact("workspace"), "D:/alpha")
            self.assertIsNone(store.get_text_exact("token"))
            self.assertEqual(store.resolve_for_ai("workspace")["value"], "D:/alpha")
            self.assertEqual(store.resolve_for_ai("token")["status"], "restricted")
            store.upsert("workspace", VARIABLE_KIND_TEXT, "D:/beta", variable_id=text["id"])
            self.assertEqual(store.get_text_exact("workspace"), "D:/beta")
            store.restore_previous()
            self.assertEqual(store.get_text_exact("workspace"), "D:/alpha")

    def test_secret_ai_permission_can_change_without_reentering_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir, protector=_TestProtector())
            secret = store.upsert("地图AK", VARIABLE_KIND_SECRET, "encrypted-ak")
            self.assertFalse(secret["allow_ai_read"])
            updated = store.upsert(
                "地图AK",
                VARIABLE_KIND_SECRET,
                value=None,
                variable_id=secret["id"],
                allow_ai_read=True,
            )
            self.assertTrue(updated["allow_ai_read"])
            self.assertEqual(store.resolve_for_ai("地图AK")["value"], "encrypted-ak")
            with open(store.path, "r", encoding="utf-8") as stream:
                self.assertNotIn("encrypted-ak", stream.read())

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
            store.upsert(
                "real-token",
                VARIABLE_KIND_SECRET,
                "dpapi-secret",
                allow_ai_read=True,
            )
            self.assertEqual(store.resolve_for_ai("real-token")["value"], "dpapi-secret")
            with open(store.path, "r", encoding="utf-8") as stream:
                self.assertNotIn("dpapi-secret", stream.read())

    def test_lookup_is_fixed_and_denied_outside_local_main_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VariableStore(temp_dir, protector=_TestProtector())
            store.upsert("plain", VARIABLE_KIND_TEXT, "visible")
            store.upsert("secret", VARIABLE_KIND_SECRET, "hidden")
            store.upsert("allowed-secret", VARIABLE_KIND_SECRET, "allowed", allow_ai_read=True)
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
            self.assertIn("lookup_app_variable", initial_names)
            initial_definitions = manager.get_tool_definitions(run_context=local_context["run_context"])
            store.upsert("later", VARIABLE_KIND_TEXT, "does-not-change-tool-prefix")
            self.assertEqual(
                manager.get_tool_definitions(run_context=local_context["run_context"]),
                initial_definitions,
            )
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "plain"}, context=local_context)["value"],
                "visible",
            )
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "secret"}, context=local_context)["status"],
                "restricted",
            )
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "allowed-secret"}, context=local_context)["value"],
                "allowed",
            )
            self.assertEqual(
                manager.call_tool("lookup_app_variable", {"name": "missing"}, context=local_context)["status"],
                "not_found",
            )
            denied_names = {
                item["function"]["name"]
                for item in manager.get_tool_definitions(
                    run_context={"mode": RUN_MODE_EXECUTION, "app_variable_access": False}
                )
            }
            self.assertNotIn("lookup_app_variable", denied_names)
            subagent = manager.call_tool(
                "lookup_app_variable",
                {"name": "plain"},
                context={**local_context, "is_subagent": True},
            )
            self.assertEqual(subagent["status"], "denied")


if __name__ == "__main__":
    unittest.main()
