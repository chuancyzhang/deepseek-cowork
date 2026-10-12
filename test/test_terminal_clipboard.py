import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TerminalClipboardTest(unittest.TestCase):
    def test_native_clipboard_in_isolated_webengine(self):
        for scale in ("1", "1.5"):
            with self.subTest(scale=scale):
                environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                   QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu", QT_SCALE_FACTOR=scale)
                # Let each PowerShell edition resolve its own built-in modules;
                # the invoking host may inject a different PSReadLine version.
                environment = {key: value for key, value in environment.items() if key.upper() != "PSMODULEPATH"}
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", str(ROOT / "scripts/smoke_terminal_clipboard.py")],
                    cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
