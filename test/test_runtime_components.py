import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core import runtime_components


class TestRuntimeComponents(unittest.TestCase):
    def _healthy_marker(self, toolkit_id, python_exe="C:\\runtime\\python.exe"):
        spec = runtime_components.TOOLKITS[toolkit_id]
        return {
            "schema": runtime_components.TOOLKIT_MARKER_SCHEMA,
            "id": toolkit_id,
            "packages": spec["packages"],
            "imports": spec["imports"],
            "definition_hash": runtime_components._toolkit_definition_hash(toolkit_id),
            "python_executable": python_exe,
            "verified": True,
        }

    def test_download_source_defaults_and_custom_https(self):
        normalized = runtime_components.normalize_download_sources({
            "python": {
                "selected": "corp",
                "custom": [{"id": "corp", "name": "Corp", "url": "https://packages.example.com/simple"}],
            }
        })
        self.assertEqual(normalized["python"]["selected"], "corp")
        self.assertEqual(normalized["python"]["custom"][0]["url"], "https://packages.example.com/simple/")
        self.assertFalse(runtime_components.valid_https_source("http://packages.example.com/simple"))
        self.assertFalse(runtime_components.valid_https_source("https://user:secret@example.com/simple"))

    def test_toolkit_catalog_has_expected_groups(self):
        self.assertEqual(
            set(runtime_components.TOOLKITS),
            {"documents", "data-analysis", "finance", "browser-automation", "web-research"},
        )
        self.assertIn("scikit-learn", runtime_components.TOOLKITS["data-analysis"]["packages"])
        self.assertEqual(runtime_components.TOOLKITS["finance"]["skills"], ["financial-data-akshare"])
        self.assertEqual(runtime_components.TOOLKITS["finance"]["packages"], ["pandas", "akshare"])
        self.assertIn("reportlab", runtime_components.TOOLKITS["documents"]["packages"])
        self.assertIn("Pillow", runtime_components.TOOLKITS["documents"]["packages"])
        self.assertIn("PIL.Image", runtime_components.TOOLKITS["documents"]["imports"])

    def test_toolkit_status_marks_stale_package_catalog_for_update(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(runtime_components, "toolkits_root", return_value=temp_dir):
            target = runtime_components.toolkit_path("documents")
            os.makedirs(target)
            marker = os.path.join(os.path.dirname(target), "toolkit.json")
            old_packages = [
                package
                for package in runtime_components.TOOLKITS["documents"]["packages"]
                if package != "reportlab"
            ]
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"packages": old_packages}, handle)

            status = runtime_components.toolkit_status("documents")

            self.assertTrue(status["installed"])
            self.assertTrue(status["needs_update"])
            self.assertEqual(status["missing_packages"], ["reportlab"])

            healthy_marker = self._healthy_marker("documents")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(healthy_marker, handle)

            with patch("core.sandbox_runtime.get_runtime_executable", return_value=healthy_marker["python_executable"]):
                current_status = runtime_components.toolkit_status("documents")
            self.assertFalse(current_status["needs_update"])
            self.assertFalse(current_status["needs_repair"])
            self.assertTrue(current_status["healthy"])
            self.assertEqual(current_status["missing_packages"], [])

    def test_installed_toolkit_paths_require_verified_current_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(runtime_components, "toolkits_root", return_value=temp_dir):
            target = runtime_components.toolkit_path("documents")
            os.makedirs(target)
            self.assertNotIn(target, runtime_components.installed_toolkit_paths())
            marker = os.path.join(os.path.dirname(target), "toolkit.json")
            healthy_marker = self._healthy_marker("documents")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(healthy_marker, handle)
            with patch("core.sandbox_runtime.get_runtime_executable", return_value=healthy_marker["python_executable"]):
                self.assertIn(target, runtime_components.installed_toolkit_paths())

    def test_verify_toolkit_candidate_uses_isolated_pythonpath(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "checked": runtime_components.TOOLKITS["documents"]["imports"]}),
            "stderr": "",
        })()
        with patch("core.sandbox_runtime.build_sandbox_env", return_value={
            "PYTHONPATH": os.pathsep.join(["C:\\sandbox\\python_bootstrap", "C:\\other-toolkit"])
        }), patch.object(runtime_components.subprocess, "run", return_value=completed) as run:
            checked = runtime_components._verify_toolkit_candidate(
                "C:\\runtime\\python.exe",
                "documents",
                "C:\\candidate",
            )

        env = run.call_args.kwargs["env"]
        self.assertEqual(
            env["PYTHONPATH"].split(os.pathsep),
            ["C:\\sandbox\\python_bootstrap", "C:\\candidate"],
        )
        self.assertIn("PIL.Image", checked)

    def test_install_toolkit_keeps_existing_version_when_verification_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(runtime_components, "toolkits_root", return_value=temp_dir), \
             patch("core.sandbox_runtime.get_runtime_executable", return_value="C:\\runtime\\python.exe"), \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={}), \
             patch.object(runtime_components.subprocess, "run", return_value=type("Completed", (), {
                 "returncode": 0, "stdout": "installed", "stderr": ""
             })()), \
             patch.object(runtime_components, "_verify_toolkit_candidate", side_effect=RuntimeError("broken PIL")), \
             patch.object(runtime_components, "_repair_python_runner_import_conflicts"):
            target_root = os.path.join(temp_dir, "documents")
            os.makedirs(os.path.join(target_root, "site-packages"))
            sentinel = os.path.join(target_root, "site-packages", "keep.txt")
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("old")

            with self.assertRaisesRegex(RuntimeError, "broken PIL"):
                runtime_components.install_toolkit(
                    "documents",
                    {"name": "PyPI", "url": "https://pypi.org/simple"},
                )

            self.assertTrue(os.path.isfile(sentinel))
            self.assertFalse(os.path.isfile(os.path.join(target_root, "toolkit.json")))

    def test_repair_python_runner_conflict_removes_only_broken_top_level_module(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(runtime_components, "get_app_data_dir", return_value=temp_dir), \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={
                 "PYTHONPATH": "C:\\sandbox\\python_bootstrap"
             }):
            skill_path = os.path.join(
                temp_dir,
                "runtime_sandbox",
                "v1",
                "skills",
                "python-runner",
                "python",
                "site-packages",
            )
            broken_pil = os.path.join(skill_path, "PIL")
            unrelated = os.path.join(skill_path, "requests")
            os.makedirs(broken_pil)
            os.makedirs(unrelated)
            results = [
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "broken PIL"})(),
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            ]
            with patch.object(runtime_components.subprocess, "run", side_effect=results):
                repaired = runtime_components._repair_python_runner_import_conflicts(
                    "C:\\runtime\\python.exe",
                    "documents",
                    "C:\\candidate",
                )

            self.assertEqual(repaired, ["PIL"])
            self.assertFalse(os.path.exists(broken_pil))
            self.assertTrue(os.path.isdir(unrelated))

    def test_node_source_uses_fixed_archive_and_hash(self):
        self.assertEqual(runtime_components.NODE_ARCHIVE, "node-v24.14.1-win-x64.zip")
        self.assertEqual(len(runtime_components.NODE_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
