import threading
import time
import unittest
import os
import tempfile
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.skill_catalog import DependencyCoordinator, SkillCatalogService, SkillChangeEvent
from core.skill_manager import SkillManager
from core.agent import LLMWorker


class _Config:
    def __init__(self):
        self.loaded = 0

    def load_config(self):
        self.loaded += 1

    def get(self, key, default=None):
        return default


class TestSkillCatalogService(unittest.TestCase):
    def test_display_inventory_is_owned_and_changes_only_with_snapshot(self):
        first = MagicMock(skill_records={"alpha": {}}, skills_dirs=[])
        first.get_all_skills.return_value = [
            {"name": "alpha", "enabled": True, "config_status": {"configured": True}},
            {"name": "disabled", "enabled": False},
        ]
        second = MagicMock(skill_records={"alpha": {}}, skills_dirs=[])
        second.get_all_skills.return_value = [{"name": "alpha", "enabled": False}]
        with patch("core.skill_catalog.SkillManager", side_effect=[first, second]):
            service = SkillCatalogService(_Config())
            old = service.preload()
            display = old.display_skills()
            self.assertEqual(display, first.get_all_skills.return_value)
            display[0]["config_status"]["configured"] = False
            display.pop()
            self.assertTrue(old.display_skills()[0]["config_status"]["configured"])
            self.assertEqual(len(old.display_skills()), 2)
            first.get_all_skills.assert_called_once()
            runtime = old.runtime(workspace_dir="isolated")
            self.assertIs(runtime, first.clone_for_runtime.return_value)
            self.assertEqual(set(first.skill_records), {"alpha"})
            service.publish_change(SkillChangeEvent.create("disabled", ["alpha"], source="ui"))
            self.assertFalse(service.snapshot().display_skills()[0]["enabled"])
            self.assertTrue(old.display_skills()[0]["enabled"])

    def test_slow_rebuild_does_not_block_reading_last_snapshot(self):
        first = MagicMock(skill_records={}, skills_dirs=[])
        first.get_all_skills.return_value = []
        replacement = MagicMock(skill_records={}, skills_dirs=[])
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        errors, observed = [], []

        def slow_signature(manager):
            if manager is replacement:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release catalog rebuild")
            return {}

        with patch("core.skill_catalog.SkillManager", side_effect=[first, replacement]), \
                patch.object(SkillCatalogService, "_build_filesystem_signature", side_effect=slow_signature):
            service = SkillCatalogService(_Config())
            old = service.preload()

            def rebuild():
                try:
                    service.reload()
                except Exception as exc:
                    errors.append(exc)

            def read():
                observed.append(service.snapshot())
                read_done.set()

            worker = threading.Thread(target=rebuild)
            reader = threading.Thread(target=read)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                reader.start()
                self.assertTrue(read_done.wait(.5), "UI snapshot read waited for filesystem work")
                self.assertIs(observed[0], old)
            finally:
                release.set()
                worker.join(3)
                if reader.ident is not None:
                    reader.join(3)
            self.assertEqual(errors, [])
            self.assertIs(service.snapshot().manager, replacement)

    def test_failed_display_rebuild_preserves_snapshot_and_revision(self):
        first = MagicMock(skill_records={}, skills_dirs=[])
        replacement = MagicMock(skill_records={}, skills_dirs=[])
        replacement.get_all_skills.side_effect = OSError("unreadable inventory")
        with patch("core.skill_catalog.SkillManager", side_effect=[first, replacement, first]):
            service = SkillCatalogService(_Config())
            old = service.preload()
            with self.assertRaises(OSError):
                service.reload()
            self.assertIs(service.snapshot(), old)
            self.assertEqual(service.reload().revision, old.revision + 1)

    def test_disabled_install_refresh_stays_out_of_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            config = _Config()
            config.is_skill_enabled = lambda name, default_enabled=True: False
            config.set_skill_enabled = lambda *args, **kwargs: None
            source = os.path.join(root, 'source')
            os.mkdir(source)
            with open(os.path.join(source, 'SKILL.md'), 'w', encoding='utf-8') as handle:
                handle.write('---\nname: internal-name\ndescription: Test\n---\nTest body')
            target = os.path.join(root, 'installed')
            os.mkdir(target)
            def build(*args, **kwargs):
                manager = SkillManager(config_manager=config, auto_load=False, prepare_dependencies=False)
                manager.skills_dirs = [target]
                with patch.object(manager, '_register_builtin_tools'), patch.object(manager, '_load_mcp_servers'):
                    manager.load_skills(load_mcp_tools=False)
                return manager
            manager = build()
            with patch.object(manager, '_default_writable_skill_root', return_value=target):
                ok, message = manager.import_skill(source, enabled=False, prepare_dependencies=False,
                    origin={'source': 'skillhub', 'slug': 'remote-name', 'version': '1.0'})
            self.assertTrue(ok, message)
            with patch('core.skill_catalog.SkillManager', side_effect=build):
                service = SkillCatalogService(config)
                service.preload()
                for action in ('created', 'updated'):
                    service.publish_change(SkillChangeEvent.create(action, ['internal-name'], source='ui'))
                    snapshot = service.snapshot()
                    self.assertNotIn('internal-name', snapshot.manager.skill_records)
                    self.assertNotIn('internal-name', snapshot.manager.skill_prompts_full)
                    self.assertEqual(snapshot.manager.get_all_skills()[0]['name'], 'internal-name')
                revision = service.snapshot().revision
                for action, name in [('created', 'missing'), ('enabled', 'internal-name')]:
                    with self.assertRaises(RuntimeError):
                        service.publish_change(SkillChangeEvent.create(action, [name], source='ui'))
                    self.assertEqual(service.snapshot().revision, revision)

    def test_snapshot_is_reused_until_explicit_change(self):
        config = _Config()
        first = MagicMock(skill_records={"alpha": {}})
        second = MagicMock(skill_records={"alpha": {}, "beta": {}})
        with patch("core.skill_catalog.SkillManager", side_effect=[first, second]) as factory:
            service = SkillCatalogService(config)
            snapshot = service.preload()
            self.assertIs(snapshot, service.snapshot())
            self.assertEqual(factory.call_count, 1)
            applied = service.publish_change(SkillChangeEvent.create("created", ["beta"], source="ai"))
            self.assertEqual(factory.call_count, 2)
            self.assertGreater(applied.revision, snapshot.revision)
            self.assertIs(service.snapshot().manager, second)

    def test_duplicate_change_event_is_idempotent(self):
        config = _Config()
        manager = MagicMock(skill_records={}, skills_dirs=[])
        replacement = MagicMock(skill_records={"alpha": {}}, skills_dirs=[])
        listener = MagicMock()
        event = SkillChangeEvent.create("updated", ["alpha"], source="ui")
        with patch("core.skill_catalog.SkillManager", side_effect=[manager, replacement]) as factory:
            service = SkillCatalogService(config)
            service.preload()
            service.subscribe(listener)
            first = service.publish_change(event)
            second = service.publish_change(event)
        self.assertEqual(first, second)
        self.assertEqual(factory.call_count, 2)
        listener.assert_called_once()

    def test_catalog_passes_dependency_coordinator_to_snapshot_manager(self):
        config = _Config()
        coordinator = object()
        manager = MagicMock(skill_records={}, skills_dirs=[])
        with patch("core.skill_catalog.SkillManager", return_value=manager) as factory:
            service = SkillCatalogService(config, dependency_coordinator=coordinator)
            service.preload()

        self.assertIs(factory.call_args.kwargs["dependency_coordinator"], coordinator)

    def test_declarative_tool_does_not_import_until_first_call(self):
        root = tempfile.mkdtemp()
        try:
            marker = os.path.join(root, "imported.txt")
            impl_path = os.path.join(root, "impl.py")
            with open(impl_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "from pathlib import Path\n"
                    f"Path({marker!r}).write_text('yes', encoding='utf-8')\n"
                    "def hello(name=None):\n    return f'hello {name}'\n"
                )
            manager = SkillManager(auto_load=False, load_mcp_tools=False)
            manager._register_declarative_tools(
                "lazy-demo",
                root,
                [
                    {
                        "name": "hello",
                        "description": "Say hello.",
                        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
                        "binding": "impl.py:hello",
                    }
                ],
            )
            self.assertFalse(os.path.exists(marker))
            self.assertEqual(manager.call_tool("hello", {"name": "Codex"}), "hello Codex")
            self.assertTrue(os.path.exists(marker))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_worker_applies_new_catalog_only_at_request_boundary(self):
        runtime = MagicMock(catalog_revision=1)
        event = SkillChangeEvent.create("created", ["same-turn-tool"], source="ai")
        snapshot = SimpleNamespace(revision=2)
        worker = SimpleNamespace(
            skill_manager=runtime,
            run_context={"browser_backend": {"backend": "external", "generation": 0}},
            _pending_skill_snapshot=None,
            _skill_snapshot_lock=threading.Lock(),
            _refresh_tool_definitions=MagicMock(),
            observability_signal=MagicMock(),
            agent_state_signal=MagicMock(),
            step_signal=MagicMock(),
        )
        LLMWorker._on_skill_catalog_changed(worker, event, snapshot)
        runtime.apply_snapshot.assert_not_called()
        LLMWorker._apply_pending_skill_snapshot(worker)
        runtime.apply_snapshot.assert_called_once_with(snapshot)
        self.assertEqual(runtime.browser_run_context, worker.run_context)
        worker._refresh_tool_definitions.assert_called_once()
        worker.agent_state_signal.emit.assert_called_once()

    def test_dependency_install_is_single_flight(self):
        config = _Config()
        coordinator = DependencyCoordinator(config)
        calls = []

        def install(*args, **kwargs):
            calls.append((args, kwargs))
            time.sleep(0.05)
            return {"ok": True, "hash": "expected", "installed": True}

        results = []
        with patch("core.sandbox_runtime.read_skill_dependency_status", return_value={}), patch(
            "core.sandbox_runtime.skill_dependency_hash", return_value="expected"
        ), patch("core.sandbox_runtime.install_skill_dependencies", side_effect=install):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        coordinator.ensure_ready("demo", python_dependencies=["requests"])
                    )
                )
                for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.get("ok") for result in results))

    def test_failed_dependency_hash_is_not_retried_automatically(self):
        coordinator = DependencyCoordinator(_Config())
        failed = {"ok": False, "hash": "same", "message": "broken"}
        with patch("core.sandbox_runtime.read_skill_dependency_status", return_value=failed), patch(
            "core.sandbox_runtime.skill_dependency_hash", return_value="same"
        ), patch("core.sandbox_runtime.install_skill_dependencies") as installer:
            result = coordinator.ensure_ready("demo", python_dependencies=["missing"])
        installer.assert_not_called()
        self.assertEqual(result, failed)

    def test_dependency_hash_change_reinstalls_previous_success(self):
        coordinator = DependencyCoordinator(_Config())
        refreshed = {"ok": True, "hash": "new", "message": "refreshed", "installed": True}
        with patch("core.sandbox_runtime.read_skill_dependency_status", return_value={"ok": True, "hash": "old"}), patch(
            "core.sandbox_runtime.skill_dependency_hash", return_value="new"
        ), patch("core.sandbox_runtime.install_skill_dependencies", return_value=refreshed) as installer:
            result = coordinator.ensure_ready("demo", python_dependencies=["requests"])

        installer.assert_called_once_with(
            "demo",
            python_dependencies=["requests"],
            node_dependencies=[],
            force=False,
            timeout_seconds=300,
        )
        self.assertEqual(result, refreshed)


if __name__ == "__main__":
    unittest.main()
