"""Actual bundled CLI under a Windows Job Object that forbids breakaway."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from core.process_utils import subprocess_kwargs_no_window


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class BrowserServiceWindowsTests(unittest.TestCase):
    def test_hosted_service_works_in_non_breakaway_job(self):
        from core.workbench_terminal import WindowsProcessJob
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            gate = Path(directory) / "start"
            process = subprocess.Popen(
                [sys.executable, str(root / "scripts/smoke_browser_service.py"), "--start-gate", str(gate)],
                cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", **subprocess_kwargs_no_window(),
            )
            job = None
            try:
                job = WindowsProcessJob(process.pid)
                gate.touch()
                output, error = process.communicate(timeout=55)
                self.assertEqual(process.returncode, 0, output + error)
                result = json.loads(output.strip().splitlines()[-1])
                self.assertTrue(all(result.values()), result)
            finally:
                if job is not None:
                    job.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
