import json
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

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

    def test_speech_component_uses_versioned_windows_release_package(self):
        self.assertEqual(runtime_components.SPEECH_TO_TEXT_PACKAGE_SCHEMA, 1)
        self.assertEqual(runtime_components.SPEECH_TO_TEXT_PACKAGE_PLATFORM, "win32-x64")
        self.assertEqual(
            runtime_components.SPEECH_TO_TEXT_PACKAGE_FILENAME,
            "deepseek-cowork-speech-to-text-v1-win-x64.zip",
        )

    def test_speech_component_package_verifies_local_payload_without_network(self):
        def digest(data):
            return hashlib.sha256(data).hexdigest()

        asset_payloads = {
            "sensevoice_model": ("model.onnx", b"model"),
            "sensevoice_tokens": ("tokens.txt", b"tokens"),
            "segmentation": ("segmentation.tar.bz2", b"segmentation"),
            "embedding": ("embedding.onnx", b"embedding"),
        }
        specs = {
            key: {"filename": filename, "size": len(data), "sha256": digest(data)}
            for key, (filename, data) in asset_payloads.items()
        }
        node_buffer = io.BytesIO()
        with zipfile.ZipFile(node_buffer, "w") as nested:
            nested.writestr("node-test/node.exe", b"node")
        node_bytes = node_buffer.getvalue()
        runtime_package = json.dumps(
            {
                "dependencies": {
                    "ffmpeg-static": "5.3.0",
                    "sherpa-onnx-node": "1.12.33",
                }
            }
        ).encode("utf-8")
        payload = {
            **{f"assets/{filename}": data for filename, data in asset_payloads.values()},
            f"node-runtime/{runtime_components.NODE_ARCHIVE}": node_bytes,
            "skill-runtime/node/package.json": runtime_package,
            "skill-runtime/node/node_modules/ffmpeg-static/package.json": b'{"version":"5.3.0"}',
            "skill-runtime/node/node_modules/sherpa-onnx-node/package.json": b'{"version":"1.12.33"}',
        }
        records = [
            {"path": name, "size": len(data), "sha256": digest(data)}
            for name, data in sorted(payload.items())
        ]
        manifest = {
            "schema": runtime_components.SPEECH_TO_TEXT_PACKAGE_SCHEMA,
            "component_id": runtime_components.SPEECH_TO_TEXT_COMPONENT_ID,
            "platform": runtime_components.SPEECH_TO_TEXT_PACKAGE_PLATFORM,
            "definition_hash": "test-definition",
            "node_version": runtime_components.NODE_VERSION,
            "node_dependencies": runtime_components.SPEECH_TO_TEXT_NODE_DEPENDENCIES,
            "files": records,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = os.path.join(temp_dir, "speech.zip")
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr(runtime_components.SPEECH_TO_TEXT_PACKAGE_MANIFEST, json.dumps(manifest))
                for name, data in payload.items():
                    archive.writestr(name, data)
            extract_root = os.path.join(temp_dir, "extract")
            os.makedirs(extract_root)
            with patch.object(runtime_components, "SPEECH_TO_TEXT_ASSETS", specs), patch.object(
                runtime_components, "NODE_SHA256", digest(node_bytes).upper()
            ), patch.object(
                runtime_components, "_speech_to_text_definition_hash", return_value="test-definition"
            ), patch.object(runtime_components.platform, "system", return_value="Windows"), patch.object(
                runtime_components.platform, "machine", return_value="AMD64"
            ), patch.object(runtime_components.requests, "get") as network:
                verified = runtime_components._verify_speech_package_archive(package_path, extract_root)

        self.assertEqual(verified["component_id"], runtime_components.SPEECH_TO_TEXT_COMPONENT_ID)
        network.assert_not_called()

    def test_speech_component_missing_local_package_never_uses_network_or_npm(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            runtime_components, "get_app_data_dir", return_value=temp_dir
        ), patch.object(runtime_components.requests, "get") as network, patch(
            "core.sandbox_runtime.install_skill_dependencies"
        ) as install_dependencies:
            with self.assertRaisesRegex(RuntimeError, "安装包不存在"):
                runtime_components.install_speech_to_text_component(
                    {"package_path": os.path.join(temp_dir, "missing.zip")}
                )

        network.assert_not_called()
        install_dependencies.assert_not_called()

    def test_speech_component_status_does_not_claim_partial_files_are_ready(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.object(
            runtime_components,
            "get_app_data_dir",
            return_value=data_dir,
        ):
            paths = runtime_components.speech_to_text_component_paths()
            os.makedirs(os.path.dirname(paths["sensevoice_model"]), exist_ok=True)
            with open(paths["sensevoice_model"], "wb") as handle:
                handle.write(b"partial")

            status = runtime_components.speech_to_text_component_status()

        self.assertFalse(status["ready"])
        self.assertFalse(status["installed"])
        self.assertTrue(status["needs_repair"])
        self.assertIn("健康标记", status["health_error"])

    def test_speech_component_health_does_not_share_skill_dependency_hash(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.object(
            runtime_components,
            "get_app_data_dir",
            return_value=data_dir,
        ), patch.object(
            runtime_components,
            "node_runtime_status",
            return_value={"installed": True, "version": runtime_components.NODE_VERSION},
        ):
            paths = runtime_components.speech_to_text_component_paths()
            file_records = {}
            for key in ("sensevoice_model", "sensevoice_tokens", "segmentation", "embedding"):
                path = paths[key]
                os.makedirs(os.path.dirname(path), exist_ok=True)
                payload = key.encode("utf-8")
                with open(path, "wb") as handle:
                    handle.write(payload)
                file_records[key] = {
                    "path": os.path.relpath(path, paths["root"]).replace("\\", "/"),
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            marker = {
                "schema": runtime_components.SPEECH_TO_TEXT_COMPONENT_SCHEMA,
                "definition_hash": runtime_components._speech_to_text_definition_hash(),
                "files": file_records,
            }
            with open(paths["marker"], "w", encoding="utf-8") as handle:
                json.dump(marker, handle)
            skill_root = runtime_components.speech_to_text_skill_runtime_root()
            for package_name in ("ffmpeg-static", "sherpa-onnx-node"):
                manifest = os.path.join(skill_root, "node", "node_modules", package_name, "package.json")
                os.makedirs(os.path.dirname(manifest), exist_ok=True)
                with open(manifest, "w", encoding="utf-8") as handle:
                    handle.write("{}")
            with open(os.path.join(skill_root, "dependency_status.json"), "w", encoding="utf-8") as handle:
                json.dump({"ok": True, "hash": "python-dependency-hash"}, handle)

            status = runtime_components.speech_to_text_component_status()

        self.assertTrue(status["ready"], status)
        self.assertEqual(status["health_error"], "")

    def test_uninstalling_speech_component_preserves_python_skill_dependencies(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.object(
            runtime_components,
            "get_app_data_dir",
            return_value=data_dir,
        ), patch.object(
            runtime_components,
            "speech_to_text_component_status",
            return_value={"ready": False},
        ):
            component_root = runtime_components.speech_to_text_component_root()
            skill_root = runtime_components.speech_to_text_skill_runtime_root()
            node_file = os.path.join(skill_root, "node", "node_modules", "module.js")
            python_file = os.path.join(skill_root, "python", "site-packages", "requests", "__init__.py")
            dependency_status = os.path.join(skill_root, "dependency_status.json")
            for path in (os.path.join(component_root, "component.json"), node_file, python_file):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("test")
            with open(dependency_status, "w", encoding="utf-8") as handle:
                json.dump({"ok": True, "hash": "python-dependency-hash"}, handle)

            runtime_components.uninstall_speech_to_text_component()

            self.assertFalse(os.path.exists(component_root))
            self.assertFalse(os.path.exists(os.path.join(skill_root, "node")))
            self.assertTrue(os.path.isfile(python_file))
            self.assertTrue(os.path.isfile(dependency_status))


if __name__ == "__main__":
    unittest.main()
