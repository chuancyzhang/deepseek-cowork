import os
from pathlib import Path
import tempfile
import unittest



from core.preview_versions import read_preview_version


class PreviewVersionTest(unittest.TestCase):
    def test_content_version_ignores_timestamp_but_detects_same_size_same_timestamp_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            path.write_bytes(b"first")
            first = read_preview_version(path)
            os.utime(path, (100, 100))
            self.assertEqual(first, read_preview_version(path))
            path.write_bytes(b"other")
            os.utime(path, (100, 100))
            self.assertNotEqual(first, read_preview_version(path))

    def test_html_resources_include_missing_files_and_ignore_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            css = Path(directory) / "style.css"
            path.write_text('<link rel="stylesheet" href="style.css"><script src="code.js"></script>', encoding="utf-8")
            first = read_preview_version(path)
            self.assertEqual(len(first.resources), 2)
            (Path(directory) / "unrelated.css").write_text("body{}")
            self.assertEqual(first, read_preview_version(path))
            css.write_text("body{color:red}")
            second = read_preview_version(path)
            self.assertEqual(first.source, second.source)
            self.assertNotEqual(first, second)
            css.unlink()
            self.assertEqual(first, read_preview_version(path))






if __name__ == "__main__":
    unittest.main()
