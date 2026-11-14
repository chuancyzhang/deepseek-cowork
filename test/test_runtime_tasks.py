"""Native lifecycle regression: subscribers cannot lose completion or cleanup."""
import threading
import unittest

from core.runtime_tasks import EventSource, TaskWorker


class NativeTaskTests(unittest.TestCase):
    def test_failed_observer_does_not_lose_other_deliveries(self):
        source = EventSource()
        seen = []
        def fail(value):
            raise OSError("simulated journal failure")
        source.subscribe(fail)
        source.subscribe(seen.append)
        with self.assertLogs("core.runtime_tasks", level="ERROR"):
            with self.assertRaises(OSError):
                source.emit("accepted content")
        self.assertEqual(seen, ["accepted content"])

    def test_join_includes_completion_handlers_and_reports_timeout(self):
        entered, release = threading.Event(), threading.Event()
        class Worker(TaskWorker):
            def run(self):
                pass
        worker = Worker()
        def finish():
            entered.set()
            release.wait(2)
        worker.finished.subscribe(finish)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(worker.join(0.01))
            self.assertFalse(worker.is_finished())
            self.assertTrue(worker.is_running())
        finally:
            release.set()
            self.assertTrue(worker.join(2))
        self.assertTrue(worker.is_finished())
        with self.assertRaises(RuntimeError):
            worker.start()

    def test_unexpected_failure_still_releases_task_and_is_observable(self):
        finished, failures = [], []
        class Worker(TaskWorker):
            def run(self):
                raise RuntimeError("unexpected worker failure")
        worker = Worker()
        worker.failed.subscribe(failures.append)
        worker.finished.subscribe(lambda: finished.append(True))
        with self.assertLogs("core.runtime_tasks", level="ERROR"):
            worker.start()
            self.assertTrue(worker.join(2))
        self.assertEqual(len(failures), 1)
        self.assertIs(worker.failure, failures[0])
        self.assertEqual(finished, [True])

    def test_subscriptions_are_owned_and_unsubscribe_is_idempotent(self):
        source, seen = EventSource(), []
        release = source.subscribe(seen.append)
        source.emit("first")
        release()
        release()
        source.emit("stale")
        self.assertEqual(seen, ["first"])
        self.assertFalse(source.has_subscribers)
