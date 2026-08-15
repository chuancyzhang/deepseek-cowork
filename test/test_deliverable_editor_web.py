import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeliverableEditorWebTest(unittest.TestCase):
    def test_editor_sources_keep_readability_and_force_string_configuration(self):
        editor_root = ROOT / "web" / "editors"
        sheet_source = (editor_root / "src" / "sheet-editor.js").read_text(
            encoding="utf-8"
        )
        docx_source = (editor_root / "src" / "docx-editor.js").read_text(
            encoding="utf-8"
        )
        shared_css = (editor_root / "static" / "editor.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("disableForceStringAlert: true", sheet_source)
        self.assertIn("disableForceStringMark: true", sheet_source)
        self.assertIn("serializedWorkbook() !== cleanSnapshot", sheet_source)
        self.assertIn("command.type === 2", sheet_source)
        self.assertIn("markClean", sheet_source)
        self.assertIn("scale: 1.1", docx_source)
        self.assertIn("--cowork-font-size: 14px", shared_css)
        self.assertIn("--cowork-control-height: 32px", shared_css)
        self.assertIn("--cowork-toolbar-height: 44px", shared_css)

    def test_offline_editors_round_trip_in_isolated_process(self):
        for scale in ("1", "1.25", "1.5"):
            with self.subTest(scale=scale):
                environment = os.environ.copy()
                environment["QT_QPA_PLATFORM"] = "offscreen"
                environment["QT_QUICK_BACKEND"] = "software"
                environment["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
                environment["QT_SCALE_FACTOR"] = scale
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
                    msg=(
                        f"scale={scale}\nstdout:\n{completed.stdout}"
                        f"\nstderr:\n{completed.stderr}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
