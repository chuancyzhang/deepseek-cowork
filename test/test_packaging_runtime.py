import ast
import copy
import fnmatch
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bundled_runtime_checks


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPackagedRuntimeContract(unittest.TestCase):
    def test_data_analysis_lock_and_bundle_validation(self):
        lock = Path(ROOT, "requirements-data-analysis.txt")
        versions = bundled_runtime_checks.data_analysis_versions(lock)
        self.assertTrue(set(bundled_runtime_checks.DATA_ANALYSIS_DISTS).issubset(versions))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / lock.name).write_bytes(lock.read_bytes())
            import subprocess
            completed = subprocess.CompletedProcess([], 1, "", "ImportError: missing numpy DLL")
            with patch.object(bundled_runtime_checks.subprocess, "run", return_value=completed) as run:
                with self.assertRaisesRegex(ValueError, "missing numpy DLL"):
                    bundled_runtime_checks.validate_python_bundle(root)
            args, kwargs = run.call_args
            self.assertIn("-I", args[0])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertNotIn("PYTHONHOME", kwargs["env"])
            self.assertNotIn("site-packages", kwargs["env"]["PATH"])
            self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_data_analysis_versions_reject_incomplete_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory, "requirements.txt")
            lock.write_text("numpy==1.0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                bundled_runtime_checks.data_analysis_versions(lock)

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

    @staticmethod
    def _browser_skill_build_artifacts():
        tree = ast.parse(Path(ROOT, "deepseek-cowork.spec").read_text(encoding="utf-8"))
        return ast.literal_eval(next(
            node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "BROWSER_SKILL_BUILD_ARTIFACTS"
                    for target in node.targets)
        ))

    def test_bundled_skill_sources_satisfy_release_audit(self):
        from scripts import package_release

        collect = self._load_spec_function('_collect_tree_for_analysis')
        collect.__globals__['fnmatch'] = fnmatch
        rejected = []
        for source_name in ('skills', 'ai_skills'):
            for source, destination in collect(os.path.join(ROOT, source_name), source_name):
                relative = Path('resources/service/_internal', destination, Path(source).name)
                reason = package_release._forbidden_reason(relative)
                if reason:
                    rejected.append(f'{relative.as_posix()}: {reason}')
        self.assertEqual(rejected, [], 'Bundled Skill source tree violates the release audit')

    def test_browser_skill_release_pins_match_real_manifest(self):
        from core import browser_skill_component as component
        from scripts import package_release as release

        manifest = json.loads(Path(ROOT, "resources/browser_skill/bundle.json").read_text(encoding="utf-8"))
        build = self._browser_skill_build_artifacts()
        fetch = dict(re.findall(
            r'^\$(BROWSER_SKILL_\w+)\s*=\s*"([^"]+)"',
            Path(ROOT, "scripts/fetch_runtimes.ps1").read_text(encoding="utf-8"), re.MULTILINE,
        ))
        for kind, runtime_prefix, fetch_prefix in (
            ("cli", "BROWSER_SKILL_", "BROWSER_SKILL_CLI_"),
            ("extension", "BROWSER_SKILL_EXTENSION_", "BROWSER_SKILL_EXTENSION_"),
        ):
            expected = {key: manifest[kind][key] for key in ("version", "archive", "url", "sha256")}
            runtime = {key: getattr(component, runtime_prefix + suffix) for key, suffix in (
                ("version", "VERSION"), ("archive", "ARCHIVE"), ("url", "DOWNLOAD_URL"), ("sha256", "SHA256"),
            )}
            published = dict(release.BROWSER_SKILL_PINNED_ARTIFACTS[kind])
            published["sha256"] = release.BROWSER_SKILL_ARTIFACT_HASHES[published["archive"]]
            download = {key: re.sub(r'\$(\w+)', lambda match: fetch[match[1]], fetch[fetch_prefix + key.upper()])
                        for key in expected}
            for stage, actual in (("build", build[kind]), ("runtime", runtime),
                                  ("release", published), ("download", download)):
                with self.subTest(kind=kind, stage=stage):
                    self.assertEqual(actual, expected)
            self.assertIn(release.BROWSER_SKILL_ASSET_PREFIX + "artifacts/" + expected["archive"],
                          release.REQUIRED_PATHS)

    def _browser_skill_fixture(self, root):
        manifest = json.loads(Path(ROOT, "resources/browser_skill/bundle.json").read_text(encoding="utf-8"))
        (root / "artifacts").mkdir()
        for kind in ("cli", "extension"):
            payload = f"{kind}-archive-fixture".encode()
            (root / "artifacts" / manifest[kind]["archive"]).write_bytes(payload)
            manifest[kind]["sha256"] = hashlib.sha256(payload).hexdigest().upper()
        (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "LICENSE.txt").write_bytes(Path(ROOT, "resources/browser_skill/LICENSE.txt").read_bytes())
        collect = self._load_spec_function("_collect_browser_skill_bundle_for_analysis")
        collect.__globals__.update(
            json=json, hashlib=hashlib, BROWSER_SKILL_RESOURCE_ROOT=str(root),
            BROWSER_SKILL_BUILD_ARTIFACTS=copy.deepcopy({kind: manifest[kind] for kind in ("cli", "extension")}),
        )
        return manifest, collect

    def test_browser_skill_collection_excludes_unpinned_archives(self):
        scratch = Path(ROOT, ".tmp/browser-skill-packaging/tests")
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            manifest, collect = self._browser_skill_fixture(root)
            (root / "artifacts/bsk-v0.3.3-x86_64-pc-windows-msvc.zip").write_bytes(b"old")
            (root / "artifacts/unrelated.zip").write_bytes(b"unrelated")
            (root / "download.log").write_text("local diagnostic", encoding="utf-8")
            expected = {(str(root / name), "resources/browser_skill") for name in ("bundle.json", "LICENSE.txt")}
            expected.update((str(root / "artifacts" / manifest[kind]["archive"]), "resources/browser_skill/artifacts")
                            for kind in ("cli", "extension"))
            self.assertEqual(set(collect()), expected)

    def test_browser_skill_collection_rejects_mismatched_missing_or_tampered_artifacts(self):
        scratch = Path(ROOT, ".tmp/browser-skill-packaging/tests")
        scratch.mkdir(parents=True, exist_ok=True)
        for kind in ("cli", "extension"):
            for failure in ("version", "archive", "url", "sha256", "missing", "tampered"):
                with self.subTest(kind=kind, failure=failure), tempfile.TemporaryDirectory(dir=scratch) as directory:
                    root = Path(directory)
                    manifest, collect = self._browser_skill_fixture(root)
                    artifact = root / "artifacts" / manifest[kind]["archive"]
                    if failure == "missing":
                        artifact.unlink()
                        error, message = FileNotFoundError, "Missing BrowserSkill"
                    elif failure == "tampered":
                        artifact.write_bytes(b"tampered")
                        error, message = RuntimeError, "SHA256 mismatch"
                    else:
                        manifest[kind][failure] = "stale"
                        (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
                        error, message = RuntimeError, "manifest does not match the pinned release"
                    with self.assertRaisesRegex(error, message):
                        collect()

    def test_document_libraries_ship_with_app_and_sandbox_runtime(self):
        with open(os.path.join(ROOT, "deepseek-cowork.spec"), "r", encoding="utf-8") as handle:
            spec_text = handle.read()
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, "scripts", "package_release.py"), "r", encoding="utf-8") as handle:
            release_text = handle.read()

        self.assertIn(
            'SANDBOX_DOCUMENT_DISTS = ["openpyxl", "xlrd", "python-docx", "python-pptx", "Pillow", "pypdf", "pypdfium2", "reportlab"]',
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
            "core.skill_install",
            "core.skill_package",
            "core.capability_search",
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
