import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPackagedRuntimeContract(unittest.TestCase):
    def test_document_preview_libraries_ship_with_packaged_app(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn('SANDBOX_DOCUMENT_DISTS = []', spec_text)
        self.assertIn('SANDBOX_RUNTIME_DISTS = MCP_RUNTIME_DISTS', spec_text)
        self.assertIn('node_env = []', spec_text)
        excludes = spec_text.split("excludes=[", 1)[1].split("],", 1)[0]
        for module_name in ("docx", "pptx", "openpyxl", "pypdf"):
            self.assertNotIn(f"'{module_name}'", excludes)

    def test_webengine_is_a_required_packaging_dependency(self):
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn("PySide6-Addons>=6.0.0", requirements)
        required_modules = [
            "PySide6.QtPositioning",
            "PySide6.QtPdf",
            "PySide6.QtPdfWidgets",
            "PySide6.QtWebChannel",
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets",
        ]
        excludes = spec_text.split("excludes=[", 1)[1].split("],", 1)[0]
        hidden_imports = spec_text.split("pyside6_hidden = [", 1)[1].split("]", 1)[0]
        for module_name in required_modules:
            self.assertIn(f'"{module_name}"', hidden_imports)
            self.assertNotIn(f"'{module_name}'", excludes)
        for module_name in (
            "PySide6.QtQml",
            "PySide6.QtQuick",
            "PySide6.QtQuickWidgets",
        ):
            self.assertNotIn(f'"{module_name}"', hidden_imports)

    def test_packaging_filters_debug_qt_resources_locales_and_python_development_files(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn("QT_TRANSLATION_ALLOWLIST", spec_text)
        self.assertIn("QTWEBENGINE_LOCALE_ALLOWLIST", spec_text)
        self.assertIn('lowered.startswith("pyside6/qml/")', spec_text)
        self.assertIn('".debug." in basename', spec_text)
        self.assertIn('relative.startswith("pythonwin/")', spec_text)
        self.assertIn('for source_name in ("skills", "ai_skills", "images")', spec_text)
        self.assertIn("datas=application_datas + qt_minimal_datas", spec_text)
        self.assertIn("a.datas = _filter_entries", spec_text)
        self.assertIn("a.binaries = _filter_entries", spec_text)

    def test_office_preview_does_not_require_qtaxcontainer(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        hidden_imports = spec_text.split("pyside6_hidden = [", 1)[1].split("]", 1)[0]
        self.assertNotIn('"PySide6.QtAxContainer"', hidden_imports)

    def test_remote_skill_installer_core_module_is_packaged(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        analysis_hidden_imports = spec_text.split("hiddenimports=", 1)[1].split("],", 1)[0]
        self.assertIn("'core.remote_skill_installer'", analysis_hidden_imports)


if __name__ == "__main__":
    unittest.main()
