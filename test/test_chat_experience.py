import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from core.workbench_terminal import terminal_launch_args


class TerminalPromptTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "PowerShell on Windows")
    def test_prompt_shortens_only_display_and_handles_unicode(self):
        args = terminal_launch_args({"id": "powershell", "args": ["-NoLogo"]})
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "含空格的目录 " + "long-name-" * 8)
            path.mkdir()
            script += "\n[Console]::OutputEncoding = [Text.Encoding]::UTF8; prompt; (Get-Location).Path"
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-EncodedCommand", encoded], cwd=path, capture_output=True, check=True)
            output = result.stdout.decode("utf-8-sig").splitlines()
            self.assertLess(len(output[0]), 55)
            self.assertIn("…", output[0])
            self.assertEqual(output[1], str(path))
