import os
import tempfile
import unittest
from unittest.mock import patch

from core.process_utils import (
    ProcessSingletonLock,
    build_process_singleton_lock_path,
    runtime_debug_logging_enabled,
    subprocess_kwargs_no_window,
)


class TestProcessUtils(unittest.TestCase):
    def test_subprocess_kwargs_no_window_preserves_non_windows_kwargs(self):
        kwargs = subprocess_kwargs_no_window(stdout="demo")
        self.assertEqual(kwargs["stdout"], "demo")
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertNotIn("creationflags", kwargs)
            self.assertNotIn("startupinfo", kwargs)

    def test_runtime_debug_logging_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(runtime_debug_logging_enabled())
        with patch.dict(os.environ, {"COWORK_RUNTIME_DEBUG_LOG": "1"}, clear=True):
            self.assertTrue(runtime_debug_logging_enabled())

    def test_process_singleton_lock_blocks_second_acquire_until_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = build_process_singleton_lock_path(temp_dir, "daemon-23333")
            first = ProcessSingletonLock(lock_path)
            second = ProcessSingletonLock(lock_path)
            third = ProcessSingletonLock(lock_path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(third.acquire())
            third.release()

    def test_process_singleton_lock_is_scoped_by_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "daemon-23333"))
            second = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "im-gateway"))

            self.assertTrue(first.acquire())
            self.assertTrue(second.acquire())

            first.release()
            second.release()


if __name__ == "__main__":
    unittest.main()
