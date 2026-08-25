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
        defaults = runtime_components.normalize_download_sources({})
        self.assertEqual(defaults["python"]["selected"], "tsinghua")
        self.assertEqual(
            runtime_components.selected_source("python", defaults)["url"],
            "https://pypi.tuna.tsinghua.edu.cn/simple",
        )

        saved_pypi = runtime_components.normalize_download_sources({
            "python": {"selected": "pypi", "custom": []},
        })
        self.assertEqual(saved_pypi["python"]["selected"], "pypi")

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
            {"documents", "data-analysis", "finance", "web-research"},
        )
        self.assertIn("scikit-learn", runtime_components.TOOLKITS["data-analysis"]["packages"])
        self.assertEqual(runtime_components.TOOLKITS["finance"]["skills"], ["financial-data-akshare"])
        self.assertEqual(runtime_components.TOOLKITS["finance"]["packages"], ["pandas", "akshare"])
        self.assertIn("reportlab", runtime_components.TOOLKITS["documents"]["packages"])
        self.assertIn("Pillow", runtime_components.TOOLKITS["documents"]["packages"])
        self.assertIn("PIL.Image", runtime_components.TOOLKITS["documents"]["imports"])
        self.assertTrue(runtime_components.TOOLKITS["documents"]["bundled"])
        self.assertEqual(
            runtime_components.TOOLKITS["web-research"]["packages"],
            ["tavily-python==0.7.26"],
        )
        self.assertEqual(runtime_components.TOOLKITS["web-research"]["imports"], ["tavily"])

    def test_toolkit_status_marks_stale_package_catalog_for_update(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(runtime_components, "toolkits_root", return_value=temp_dir):
            target = runtime_components.toolkit_path("data-analysis")
            os.makedirs(target)
            marker = os.path.join(os.path.dirname(target), "toolkit.json")
            old_packages = [
                package
                for package in runtime_components.TOOLKITS["data-analysis"]["packages"]
                if package != "scikit-learn"
            ]
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"packages": old_packages}, handle)

            status = runtime_components.toolkit_status("data-analysis")

            self.assertTrue(status["installed"])
            self.assertTrue(status["needs_update"])
            self.assertEqual(status["missing_packages"], ["scikit-learn"])

            healthy_marker = self._healthy_marker("data-analysis")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(healthy_marker, handle)

            with patch("core.sandbox_runtime.get_runtime_executable", return_value=healthy_marker["python_executable"]):
                current_status = runtime_components.toolkit_status("data-analysis")
            self.assertFalse(current_status["needs_update"])
            self.assertFalse(current_status["needs_repair"])
            self.assertTrue(current_status["healthy"])
            self.assertEqual(current_status["missing_packages"], [])

    def test_toolkit_status_skips_directory_size_until_explicitly_requested(self):
        with patch.object(runtime_components, "_directory_size", return_value=321) as size_probe:
            status = runtime_components.toolkit_status("data-analysis")
            self.assertEqual(status["size"], 0)
            size_probe.assert_not_called()

            status = runtime_components.toolkit_status("data-analysis", include_size=True)
            self.assertEqual(status["size"], 321)
            size_probe.assert_called_once()

    def test_installed_toolkit_paths_require_verified_current_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(runtime_components, "toolkits_root", return_value=temp_dir):
            target = runtime_components.toolkit_path("data-analysis")
            os.makedirs(target)
            self.assertNotIn(target, runtime_components.installed_toolkit_paths())
            marker = os.path.join(os.path.dirname(target), "toolkit.json")
            healthy_marker = self._healthy_marker("data-analysis")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(healthy_marker, handle)
            with patch("core.sandbox_runtime.get_runtime_executable", return_value=healthy_marker["python_executable"]):
                self.assertIn(target, runtime_components.installed_toolkit_paths())

    def test_bundled_document_toolkit_reports_health_without_user_overlay(self):
        python_exe = "C:\\runtime\\python.exe"
        with patch(
            "core.sandbox_runtime.get_runtime_executable",
            return_value=python_exe,
        ), patch.object(
            runtime_components,
            "_verify_toolkit_candidate",
            return_value=runtime_components.TOOLKITS["documents"]["imports"],
        ) as verify:
            status = runtime_components.toolkit_status("documents", include_size=True)

        self.assertTrue(status["bundled"])
        self.assertTrue(status["installed"])
        self.assertTrue(status["healthy"])
        self.assertFalse(status["needs_repair"])
        self.assertEqual(status["source"], "随应用安装")
        self.assertEqual(status["size"], 0)
        verify.assert_called_once_with(
            python_exe,
            "documents",
            "C:\\runtime\\Lib\\site-packages",
        )

    def test_bundled_document_toolkit_surfaces_distribution_damage(self):
        with patch(
            "core.sandbox_runtime.get_runtime_executable",
            return_value="C:\\runtime\\python.exe",
        ), patch.object(
            runtime_components,
            "_verify_toolkit_candidate",
            side_effect=RuntimeError("missing reportlab"),
        ):
            status = runtime_components.toolkit_status("documents")

        self.assertFalse(status["healthy"])
        self.assertTrue(status["needs_repair"])
        self.assertIn("重新安装完整的 Cowork 分发包", status["health_error"])
        self.assertIn("missing reportlab", status["health_error"])

    def test_bundled_document_toolkit_cannot_be_installed_or_uninstalled(self):
        with self.assertRaisesRegex(RuntimeError, "不能单独安装或覆盖"):
            runtime_components.install_toolkit("documents", {})
        with self.assertRaisesRegex(RuntimeError, "不能单独卸载"):
            runtime_components.uninstall_toolkit("documents")

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
            target_root = os.path.join(temp_dir, "data-analysis")
            os.makedirs(os.path.join(target_root, "site-packages"))
            sentinel = os.path.join(target_root, "site-packages", "keep.txt")
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("old")

            with self.assertRaisesRegex(RuntimeError, "broken PIL"):
                runtime_components.install_toolkit(
                    "data-analysis",
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
