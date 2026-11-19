import json
import os
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from core.memory_store import MemoryStore, workspace_key, resolve_memory_project
from types import SimpleNamespace


class TestMemoryStore(unittest.TestCase):
    def test_concurrent_initialization_imports_once_without_rewriting_layout(self):
        from core.memory_store import _atomic_write
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, 'memories.md'), 'w', encoding='utf-8') as handle:
                handle.write('existing memory')
            gate = threading.Barrier(8)
            def initialize(_):
                gate.wait()
                return MemoryStore(root).read_summary()
            with patch('core.memory_store._atomic_write', wraps=_atomic_write) as write:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    self.assertEqual(list(pool.map(initialize, range(8))), ['existing memory\n'] * 8)
                self.assertEqual(write.call_count, 2)

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

    def test_experience_merge_preserves_other_sections_and_is_idempotent(self):
        original = "# 事实\n用户偏好中文。\n\n## 可复用经验\n\n- 旧方法\n\n## 其他约定\n保留这里。\n"
        self.store.save_summary(original)
        self.assertEqual(self.store.update_experience("新方法", previous_experience="旧方法"), "updated")
        self.assertEqual(self.store.read_summary(), original.replace("旧方法", "新方法"))
        backups = os.listdir(os.path.join(self.store.root, "backups"))
        self.assertEqual(self.store.update_experience("新方法", previous_experience="旧方法"), "unchanged")
        self.assertEqual(self.store.update_experience("新方法"), "unchanged")
        self.assertEqual(os.listdir(os.path.join(self.store.root, "backups")), backups)
        self.store.update_experience("第二条方法")
        content = self.store.read_summary()
        self.assertLess(content.index("第二条方法"), content.index("## 其他约定"))
        self.assertIn("用户偏好中文。", content)
        self.assertTrue(content.endswith("## 其他约定\n保留这里。\n"))

    def test_stale_or_ambiguous_lesson_cannot_overwrite_memory(self):
        for original in ("事实", "## 可复用经验\n- 旧\n- 旧\n", "## 可复用经验\n\n## 可复用经验\n"):
            with self.subTest(original=original):
                self.store.save_summary(original)
                before = self.store.read_summary()
                with self.assertRaises(ValueError):
                    self.store.update_experience("新", previous_experience="旧")
                self.assertEqual(self.store.read_summary(), before)

    def test_markdown_examples_are_not_experience_entries(self):
        example = "# 文档示例\n```md\n## 可复用经验\n- 示例\n```\n"
        self.store.save_summary(example)
        self.store.update_experience("真实经验")
        self.assertTrue(self.store.read_summary().startswith(example))
        with self.assertRaises(ValueError):
            self.store.update_experience("错误覆盖", previous_experience="示例")
        self.store.save_summary("## 可复用经验\n```md\n- 示例\n```\n- 已验证\n")
        with self.assertRaises(ValueError):
            self.store.update_experience("错误覆盖", previous_experience="示例")
        self.assertEqual(self.store.update_experience("更新已验证", previous_experience="已验证"), "updated")

    def test_concurrent_lessons_and_explicit_append_do_not_lose_content(self):
        self.store.save_summary("用户事实")
        gate = threading.Barrier(10)
        def write(index):
            gate.wait()
            if index == 0:
                self.store.append_summary("用户明确补充")
            else:
                MemoryStore(self.temp_dir.name).update_experience(f"方法 {index}")
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(write, range(10)))
        content = self.store.read_summary()
        self.assertIn("用户事实", content)
        self.assertIn("用户明确补充", content)
        self.assertEqual(content.count("## 可复用经验"), 1)
        for index in range(1, 10):
            self.assertEqual(content.count(f"- 方法 {index}\n"), 1)

    def test_editor_compare_and_save_rejects_changed_memory(self):
        self.store.save_summary("原始内容")
        opened = self.store.read_summary()
        self.store.update_experience("已验证经验")
        latest = self.store.read_summary()
        with self.assertRaisesRegex(ValueError, "编辑已保留"):
            self.store.save_summary("人工编辑", expected_content=opened)
        self.assertEqual(self.store.read_summary(), latest)
        self.store.save_summary("合并后的人工编辑", expected_content=latest)
        self.assertEqual(self.store.read_summary(), "合并后的人工编辑\n")

    def test_settings_save_detects_a_write_after_revision_check(self):
        from core.desktop_settings import DesktopSettings
        config = SimpleNamespace(get_chat_history_dir=lambda: self.temp_dir.name, get_projects=lambda: [])
        settings = DesktopSettings(SimpleNamespace(config=config))
        self.store.save_summary("已打开的原文")
        page = settings.page("memory")
        read_page = settings.page
        def changed_page(*args, **kwargs):
            result = read_page(*args, **kwargs)
            self.store.update_experience("并发写入的经验")
            return result
        with patch.object(settings, "page", side_effect=changed_page):
            with self.assertRaisesRegex(ValueError, "编辑已保留"):
                settings.save("memory", {"revision": page["revision"], "value": {"summary": "人工草稿"}})
        self.assertIn("并发写入的经验", self.store.read_summary())
        self.assertNotIn("人工草稿", self.store.read_summary())

    def test_failed_atomic_write_preserves_original_and_backup(self):
        self.store.save_summary("原始内容")
        with patch("core.memory_store.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.update_experience("新经验")
        self.assertEqual(self.store.read_summary(), "原始内容\n")
        backup = os.path.join(self.store.root, "backups", os.listdir(os.path.join(self.store.root, "backups"))[0])
        with open(backup, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "原始内容\n")

    def test_project_resolution_excludes_chat_creation_and_other_projects(self):
        project = os.path.join(self.temp_dir.name, "project")
        config = SimpleNamespace(get_projects=lambda: [{"path": project}])
        storage = SimpleNamespace(get_conversation_meta=lambda _cid: {"workspace_source": "project", "workspace_dir": project})
        self.assertTrue(resolve_memory_project(config, project, storage=storage, conversation_id="a"))
        self.assertFalse(resolve_memory_project(config, self.temp_dir.name))
        self.assertFalse(resolve_memory_project(config, project, run_context={"creation_session": True}))
        storage.get_conversation_meta = lambda _cid: {"workspace_source": "chat", "workspace_dir": project}
        self.assertFalse(resolve_memory_project(config, project, storage=storage, conversation_id="chat"))

    def test_project_edits_keep_memory_bound_to_the_original_workspace(self):
        original, replacement, inferred = (os.path.join(self.temp_dir.name, folder) for folder in ("original", "new", "inferred"))
        config = SimpleNamespace(get_projects=lambda: [
            {"path": replacement, "previous_paths": [original]},
            {"path": inferred, "inferred_path": inferred},
        ])
        storage = SimpleNamespace(get_conversation_meta=lambda _cid: {"workspace_source": "project", "workspace_dir": original})
        self.assertEqual(resolve_memory_project(config, original, storage=storage, conversation_id="old"), os.path.normcase(original))
        self.assertFalse(resolve_memory_project(config, replacement, storage=storage, conversation_id="old"))
        self.assertFalse(resolve_memory_project(config, inferred))

    def test_project_experience_cannot_change_global_memory(self):
        self.store.save_summary("全局事实")
        self.store.update_experience("项目 A 的方法", "workspace", "C:/A")
        self.assertEqual(self.store.read_summary(), "全局事实\n")
        self.assertEqual(self.store.read_summary("workspace", "C:/B"), "")
        self.assertIn("项目 A 的方法", self.store.read_summary("workspace", "C:/A"))

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
