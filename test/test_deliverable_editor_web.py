import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeliverableEditorWebTest(unittest.TestCase):
    def test_offline_editors_round_trip_in_isolated_process(self):
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["QT_QUICK_BACKEND"] = "software"
        environment["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "smoke_deliverable_editors.py"),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=150,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
