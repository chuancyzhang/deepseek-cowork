from pathlib import Path
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
        self.assertIn("preparePdfExport", docx_source)
        self.assertIn("pixelRatio: 2", docx_source)
        self.assertIn("bridge.pdfExportReady", docx_source)
        self.assertIn("--cowork-font-size: 14px", shared_css)
        self.assertIn("--cowork-control-height: 32px", shared_css)
        self.assertIn("--cowork-toolbar-height: 44px", shared_css)


if __name__ == "__main__":
    unittest.main()
