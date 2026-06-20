import os
import tempfile
import unittest

from core.memory_store import MemoryStore, workspace_key


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_imports_legacy_memory_without_removing_source(self):
        other = tempfile.TemporaryDirectory()
        try:
            legacy = os.path.join(other.name, "memories.md")
            with open(legacy, "w", encoding="utf-8") as handle:
                handle.write("legacy memory")
            store = MemoryStore(other.name)
            self.assertEqual(store.read_summary("global").strip(), "legacy memory")
            self.assertTrue(os.path.exists(legacy))
        finally:
            other.cleanup()

    def test_global_and_workspace_modules_are_isolated(self):
        self.store.save_module("Global", "global content")
        self.store.save_module("Project A", "a content", scope="workspace", workspace_dir="C:/A")
        self.store.save_module("Project B", "b content", scope="workspace", workspace_dir="C:/B")
        titles = [item["title"] for item in self.store.list_modules("C:/A")]
        self.assertEqual(set(titles), {"Global", "Project A"})
        self.assertNotEqual(workspace_key("C:/A"), workspace_key("C:/B"))

    def test_edit_creates_restorable_version(self):
        module = self.store.save_module("Preference", "first")
        self.store.save_module("Preference", "second", module_id=module["id"])
        self.assertTrue(self.store.list_module_versions(module["id"]))
        restored = self.store.restore_latest_module_version(module["id"])
        self.assertEqual(restored.strip(), "first")
        self.assertEqual(self.store.get_module(module["id"])["content"].strip(), "first")

    def test_search_ignores_disabled_and_other_workspace_modules(self):
        self.store.save_module("Python style", "prefer pathlib", tags=["python"])
        self.store.save_module("Disabled", "prefer pathlib", enabled=False)
        self.store.save_module("Other", "prefer pathlib", scope="workspace", workspace_dir="C:/Other")
        results = self.store.search_modules("pathlib", "C:/Current")
        self.assertEqual([item["title"] for item in results], ["Python style"])


if __name__ == "__main__":
    unittest.main()
