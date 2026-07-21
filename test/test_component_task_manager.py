import unittest
import os
import tempfile

from main import ComponentTaskManager


class TestComponentTaskManager(unittest.TestCase):
    def test_component_status_cache_round_trips_without_probing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = os.path.join(temp_dir, "component_status_cache.json")
            manager = ComponentTaskManager()
            manager._status_cache_path = cache_path
            manager._component_statuses = {}
            manager._record_component_status("node", {"installed": True, "path": "node.exe"})

            restored = ComponentTaskManager()
            restored._status_cache_path = cache_path
            restored._component_statuses = {}
            restored._status_cache_error = ""
            restored._load_status_cache()

            snapshot = restored.component_status_snapshot()
            self.assertTrue(snapshot["components"]["node"]["known"])
            self.assertEqual(snapshot["components"]["node"]["path"], "node.exe")

    def test_corrupt_status_cache_is_reported_without_fabricating_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = os.path.join(temp_dir, "component_status_cache.json")
            with open(cache_path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            manager = ComponentTaskManager()
            manager._status_cache_path = cache_path
            manager._component_statuses = {}
            manager._status_cache_error = ""

            manager._load_status_cache()

            snapshot = manager.component_status_snapshot()
            self.assertEqual(snapshot["components"], {})
            self.assertTrue(snapshot["cache_error"])

    def test_cache_write_failure_keeps_successful_status_in_memory(self):
        manager = ComponentTaskManager()
        manager._component_statuses = {}
        manager._save_status_cache = lambda: (_ for _ in ()).throw(OSError("disk full"))

        status = manager._record_component_status("node", {"installed": True})

        self.assertTrue(manager.component_status_snapshot()["components"]["node"]["installed"])
        self.assertTrue(status["known"])

    def test_multiple_components_queue_in_click_order_without_duplicates(self):
        manager = ComponentTaskManager()
        manager._worker = object()

        self.assertTrue(manager.enqueue("install", "documents", {"url": "https://example.com/simple"}))
        self.assertTrue(manager.enqueue("install", "data-analysis", {"url": "https://example.com/simple"}))
        self.assertFalse(manager.enqueue("repair", "documents", {"url": "https://example.com/simple"}))

        snapshot = manager.snapshot()
        self.assertEqual(
            [task["component_id"] for task in manager._queue],
            ["documents", "data-analysis"],
        )
        self.assertEqual(snapshot["tasks"]["documents"]["queue_position"], 1)
        self.assertEqual(snapshot["tasks"]["data-analysis"]["queue_position"], 2)

    def test_failed_task_is_removed_before_next_queue_item_starts(self):
        manager = ComponentTaskManager()
        first = {
            "action": "install",
            "component_id": "documents",
            "source": {},
            "state": "running",
            "message": "",
            "progress": 0,
            "error": "",
        }
        second = {
            "action": "install",
            "component_id": "data-analysis",
            "source": {},
            "state": "queued",
            "message": "",
            "progress": 0,
            "error": "",
        }
        manager._current = first
        manager._worker = object()
        manager._tasks = {"documents": first, "data-analysis": second}
        manager._queue = [second]
        manager._pending_result = {"ok": False, "error": "download failed"}
        started = []
        manager._start_next = lambda: started.append(True)

        manager._worker_finished()

        self.assertNotIn("documents", manager._tasks)
        self.assertIn("data-analysis", manager._tasks)
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
