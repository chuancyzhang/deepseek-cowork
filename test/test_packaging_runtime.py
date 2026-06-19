import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPackagedRuntimeContract(unittest.TestCase):
    def test_document_libraries_are_bundled_into_sandbox_runtime(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn(
            'SANDBOX_DOCUMENT_DISTS = ["openpyxl", "python-docx", "python-pptx", "pypdf"]',
            spec_text,
        )
        self.assertIn(
            "_collect_distribution_runtime_entries(site_packages, SANDBOX_RUNTIME_DISTS)",
            spec_text,
        )

    def test_webengine_is_a_required_packaging_dependency(self):
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn("PySide6-Addons>=6.0.0", requirements)
        self.assertIn('"PySide6.QtWebEngineCore"', spec_text)
        self.assertIn('"PySide6.QtWebEngineWidgets"', spec_text)
        excludes = spec_text.split("excludes=[", 1)[1].split("],", 1)[0]
        self.assertNotIn("PySide6.QtWebEngineCore", excludes)
        self.assertNotIn("PySide6.QtWebEngineWidgets", excludes)


if __name__ == "__main__":
    unittest.main()
