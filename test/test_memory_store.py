import json
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

    def test_global_and_workspace_summaries_are_isolated(self):
        self.store.save_summary("global", "global")
        self.store.save_summary("project a", "workspace", "C:/A")
        self.store.save_summary("project b", "workspace", "C:/B")
        self.assertEqual(self.store.read_summary("global").strip(), "global")
        self.assertEqual(self.store.read_summary("workspace", "C:/A").strip(), "project a")
        self.assertEqual(self.store.read_summary("workspace", "C:/B").strip(), "project b")
        self.assertNotEqual(workspace_key("C:/A"), workspace_key("C:/B"))

    def test_initialization_removes_obsolete_module_data_and_backups(self):
        root = os.path.join(self.temp_dir.name, "memory")
        module_dir = os.path.join(root, "global", "modules")
        backup_dir = os.path.join(root, "backups")
        os.makedirs(module_dir, exist_ok=True)
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as handle:
            json.dump({"modules": []}, handle)
        with open(os.path.join(module_dir, "old.md"), "w", encoding="utf-8") as handle:
            handle.write("old module")
        with open(os.path.join(backup_dir, "old.md.1.bak"), "w", encoding="utf-8") as handle:
            handle.write("old module")
        with open(os.path.join(backup_dir, "summary.md.1.bak"), "w", encoding="utf-8") as handle:
            handle.write("summary")

        MemoryStore(self.temp_dir.name)

        self.assertFalse(os.path.exists(os.path.join(root, "index.json")))
        self.assertFalse(os.path.exists(module_dir))
        self.assertFalse(os.path.exists(os.path.join(backup_dir, "old.md.1.bak")))
        self.assertTrue(os.path.exists(os.path.join(backup_dir, "summary.md.1.bak")))


if __name__ == "__main__":
    unittest.main()
