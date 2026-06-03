import os
import unittest
from unittest.mock import patch

from core.process_utils import runtime_debug_logging_enabled, subprocess_kwargs_no_window


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


if __name__ == "__main__":
    unittest.main()
