import ast
import os
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPackagedRuntimeContract(unittest.TestCase):
    @staticmethod
    def _load_spec_function(function_name):
        spec_path = os.path.join(ROOT, "deepseek-cowork.spec")
        with open(spec_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=spec_path)
        function_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        namespace = {"ast": ast, "os": os}
        module = ast.Module(body=[function_node], type_ignores=[])
        exec(compile(module, spec_path, "exec"), namespace)
        return namespace[function_name]

    def test_document_libraries_ship_with_app_and_sandbox_runtime(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, "scripts", "package_release.py"), "r", encoding="utf-8") as handle:
            release_text = handle.read()

        self.assertIn(
            'SANDBOX_DOCUMENT_DISTS = ["openpyxl", "python-docx", "python-pptx", "Pillow", "pypdf", "reportlab"]',
            spec_text,
        )
        self.assertIn('SANDBOX_RUNTIME_DISTS = MCP_RUNTIME_DISTS + SANDBOX_DOCUMENT_DISTS', spec_text)
        self.assertIn('node_env = []', spec_text)
        excludes = spec_text.split("excludes=[", 1)[1].split("],", 1)[0]
        for module_name in ("docx", "pptx", "openpyxl", "pypdf"):
            self.assertNotIn(f"'{module_name}'", excludes)
            self.assertIn(f"'{module_name}'", spec_text.split("hiddenimports=", 1)[1].split("hookspath=", 1)[0])
        self.assertNotIn("'lxml'", excludes)
        for package_name in ("Pillow", "reportlab"):
            self.assertIn(package_name, requirements)
        for import_path in ("openpyxl", "docx", "pptx", "PIL", "pypdf", "reportlab"):
            self.assertIn(f"_internal/python_env/Lib/site-packages/{import_path}/__init__.py", release_text)

    def test_webengine_is_a_required_packaging_dependency(self):
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn("PySide6>=6.2.0", requirements)
        self.assertIn("PySide6-Addons>=6.2.0", requirements)
        required_modules = [
            "PySide6.QtPositioning",
            "PySide6.QtPdf",
            "PySide6.QtPdfWidgets",
            "PySide6.QtSvg",
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
        self.assertIn('for source_name in ("skills", "ai_skills", "images", os.path.join("web", "editors", "dist"))', spec_text)
        self.assertIn("datas=application_datas + qt_minimal_datas", spec_text)
        self.assertIn("a.datas = _filter_entries", spec_text)
        self.assertIn("a.binaries = _filter_entries", spec_text)

    def test_office_preview_does_not_require_qtaxcontainer(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        hidden_imports = spec_text.split("pyside6_hidden = [", 1)[1].split("]", 1)[0]
        self.assertNotIn('"PySide6.QtAxContainer"', hidden_imports)

    def test_dynamic_skill_core_modules_are_packaged(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()

        self.assertIn("+ DYNAMIC_SKILL_CORE_HIDDENIMPORTS", spec_text)
        self.assertIn(
            'REQUIRED_WORKSPACE_SKILL_MODULES = {"core.apply_patch", "core.filesystem_ops"}',
            spec_text,
        )
        self.assertIn("[Packaging] Dynamic Skill core hidden imports:", spec_text)
        collect_hiddenimports = self._load_spec_function(
            "_collect_dynamic_skill_core_hiddenimports"
        )
        hiddenimports = collect_hiddenimports(
            os.path.join(ROOT, "skills"),
            os.path.join(ROOT, "ai_skills"),
        )
        for module_name in (
            "core.apply_patch",
            "core.filesystem_ops",
            "core.remote_skill_installer",
        ):
            self.assertIn(module_name, hiddenimports)

    def test_dynamic_skill_import_analysis_fails_loudly_for_invalid_impl(self):
        collect_hiddenimports = self._load_spec_function(
            "_collect_dynamic_skill_core_hiddenimports"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = os.path.join(temp_dir, "broken-skill")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as handle:
                handle.write("from core.\n")

            with self.assertRaisesRegex(RuntimeError, "Cannot analyze bundled Skill"):
                collect_hiddenimports(temp_dir)

    def test_wecom_cli_and_skill_are_pinned_and_packaged(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()
        with open(os.path.join(ROOT, "scripts", "fetch_runtimes.ps1"), "r", encoding="utf-8") as handle:
            fetch_text = handle.read()
        with open(os.path.join(ROOT, "scripts", "package_release.py"), "r", encoding="utf-8") as handle:
            release_text = handle.read()

        for text in (spec_text, fetch_text, release_text):
            self.assertIn("1.1.0", text)
            self.assertIn("51CCCBA7A9F84E1995C0AB284DD664A2F79E9ABA0C1FF8782AB9B93540297F1B", text)
        self.assertIn("_collect_wecom_cli_bundle_for_analysis", spec_text)
        self.assertIn("_audit_wecom_cli_assets", release_text)
        self.assertIn("_internal/resources/wecom_cli/bin/wecom-cli.exe", release_text)
        self.assertIn("_internal/ai_skills/wecom-unified/skill.json", release_text)
        self.assertIn("DEFAULT_MAX_DIST_MB = 865", release_text)


if __name__ == "__main__":
    unittest.main()
