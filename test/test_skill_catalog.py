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
