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
            'SANDBOX_DOCUMENT_DISTS = ["openpyxl", "xlrd", "python-docx", "python-pptx", "Pillow", "pypdf", "reportlab"]',
            spec_text,
        )
        self.assertIn('SANDBOX_RUNTIME_DISTS = MCP_RUNTIME_DISTS + SANDBOX_DOCUMENT_DISTS', spec_text)
        self.assertIn('node_env = []', spec_text)
        excludes = spec_text.split("excludes=[", 1)[1].split("],", 1)[0]
        for module_name in ("docx", "pptx", "openpyxl", "xlrd", "pypdf"):
            self.assertNotIn(f"'{module_name}'", excludes)
            self.assertIn(f"'{module_name}'", spec_text.split("hiddenimports=", 1)[1].split("hookspath=", 1)[0])
        self.assertNotIn("'lxml'", excludes)
        for package_name in ("Pillow", "reportlab", "xlrd"):
            self.assertIn(package_name, requirements)
        for import_path in ("openpyxl", "xlrd", "docx", "pptx", "PIL", "pypdf", "reportlab"):
            self.assertIn(f"_internal/python_env/Lib/site-packages/{import_path}/__init__.py", release_text)

    def test_native_service_and_electron_replace_qt_dependencies(self):
        from pathlib import Path
        import json
        spec = ast.parse(Path(ROOT, "deepseek-cowork.spec").read_text(encoding="utf-8"))
        analysis = next(node.value for node in spec.body if isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "Analysis")
        excludes = ast.literal_eval(next(item.value for item in analysis.keywords if item.arg == "excludes"))
        self.assertEqual(ast.literal_eval(analysis.args[0]), ["scripts/desktop_service_entry.py"])
        requirements = Path(ROOT, "requirements.txt").read_text(encoding="utf-8")
        for name in ("PySide6", "PyQt5", "PyQt6", "shiboken6", "ui"):
            self.assertIn(name, excludes)
            self.assertNotIn(name + ">=", requirements)
        package = json.loads(Path(ROOT, "desktop/package.json").read_text(encoding="utf-8"))
        self.assertTrue(package["devDependencies"]["electron"])

    def test_packaging_filters_retired_gui_and_python_development_files(self):
        normalize = self._load_spec_function("_normalized_bundle_path")
        native = self._load_spec_function("_allow_native_bundle_entry")
        python = self._load_spec_function("_allow_python_env_entry")
        for function in (native, python):
            function.__globals__["_normalized_bundle_path"] = normalize
        for path in ("PySide6/QtCore.pyd", "PyQt6/Qt6/bin/Qt6Core.dll", "shiboken6/module.pyd"):
            self.assertFalse(native((path, "source", "BINARY")))
        self.assertTrue(native(("winpty/conpty.dll", "source", "BINARY")))
        for path in ("python_env/Lib/site-packages/pythonwin/Pythonwin.exe",
                     "python_env/Lib/site-packages/docx/tests/test.py", "python_env/Lib/a.pyc"):
            self.assertFalse(python((path, "source", "DATA")))
        self.assertTrue(python(("python_env/Lib/site-packages/docx/__init__.py", "source", "DATA")))


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


if __name__ == "__main__":
    unittest.main()
