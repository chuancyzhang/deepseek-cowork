import unittest

from main import ComponentTaskManager


class TestComponentTaskManager(unittest.TestCase):
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
