import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from scripts import bundled_runtime_checks


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "package_release.py"
SPEC = importlib.util.spec_from_file_location("package_release", MODULE_PATH)
package_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_release)


class PackageReleaseTests(unittest.TestCase):
    CLI_FIXTURE = b"browser-skill-cli-fixture"
    EXTENSION_FIXTURE = b"browser-skill-extension-fixture"
    WECOM_FIXTURE = b"wecom-cli-fixture"

    def setUp(self):
        self.fixture_hashes = {
            package_release.BROWSER_SKILL_CLI_ARCHIVE: hashlib.sha256(
                self.CLI_FIXTURE
            ).hexdigest().upper(),
            package_release.BROWSER_SKILL_EXTENSION_ARCHIVE: hashlib.sha256(
                self.EXTENSION_FIXTURE
            ).hexdigest().upper(),
        }
        self.hash_patch = patch.dict(
            package_release.BROWSER_SKILL_ARTIFACT_HASHES,
            self.fixture_hashes,
            clear=True,
        )
        self.hash_patch.start()
        self.wecom_hash_patch = patch.object(package_release, "WECOM_CLI_SHA256", hashlib.sha256(self.WECOM_FIXTURE).hexdigest().upper())
        self.wecom_hash_patch.start()
        self.rg_fixtures = {name: f"ripgrep:{name}".encode() for name in bundled_runtime_checks.RIPGREP_FILE_HASHES}
        self.rg_hash_patch = patch.dict(bundled_runtime_checks.RIPGREP_FILE_HASHES, {
            name: hashlib.sha256(data).hexdigest().upper() for name, data in self.rg_fixtures.items()
        })
        self.rg_hash_patch.start()
        self.git_config_patch = patch.object(
            bundled_runtime_checks, "_git_config_values",
            side_effect=lambda _root, key: ["manager"] if key == "credential.helper" else [],
        )
        self.git_config_patch.start()

    def tearDown(self):
        self.hash_patch.stop()
        self.wecom_hash_patch.stop()
        self.rg_hash_patch.stop()
        self.git_config_patch.stop()

    def _create_minimal_dist(self, root):
        dist = Path(root) / "deepseek-cowork"
        for relative in package_release.REQUIRED_PATHS:
            path = dist / Path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.startswith("_internal/ripgrep_env/"):
                path.write_bytes(self.rg_fixtures[path.name])
            elif relative == "_internal/enterprise_capabilities.json":
                path.write_text(json.dumps({"supported_connection_schemas": [1], "supported_platform_api_versions": [1]}), encoding="utf-8")
            elif relative.endswith("wecom_cli/bundle.json"):
                manifest = json.loads((ROOT / "resources/wecom_cli/bundle.json").read_text(encoding="utf-8"))
                manifest["executable_sha256"] = package_release.WECOM_CLI_SHA256
                path.write_text(json.dumps(manifest), encoding="utf-8")
            elif relative.endswith("wecom-cli.exe"):
                path.write_bytes(self.WECOM_FIXTURE)
            elif relative.endswith("wecom_cli/LICENSE.txt"):
                path.write_text("MIT License\nCopyright (c) 2026 WeCom\n", encoding="utf-8")
            elif relative.endswith("/bundle.json"):
                path.write_text(
                    json.dumps({
                        "schema": 1,
                        "component_id": "browser-skill",
                        "license": "LICENSE.txt",
                        "cli": {
                            "version": "0.3.3",
                            "archive": package_release.BROWSER_SKILL_CLI_ARCHIVE,
                            "url": package_release.BROWSER_SKILL_PINNED_ARTIFACTS["cli"]["url"],
                            "sha256": self.fixture_hashes[
                                package_release.BROWSER_SKILL_CLI_ARCHIVE
                            ],
                        },
                        "extension": {
                            "version": "0.3.3",
                            "archive": package_release.BROWSER_SKILL_EXTENSION_ARCHIVE,
                            "url": package_release.BROWSER_SKILL_PINNED_ARTIFACTS["extension"]["url"],
                            "sha256": self.fixture_hashes[
                                package_release.BROWSER_SKILL_EXTENSION_ARCHIVE
                            ],
                        },
                    }),
                    encoding="utf-8",
                )
            elif relative.endswith("/LICENSE.txt") and "browser_skill" in relative:
                path.write_text(
                    "MIT License\n\nCopyright (c) 2026 Tencent\n",
                    encoding="utf-8",
                )
            elif relative.endswith(package_release.BROWSER_SKILL_CLI_ARCHIVE):
                path.write_bytes(self.CLI_FIXTURE)
            elif relative.endswith(package_release.BROWSER_SKILL_EXTENSION_ARCHIVE):
                path.write_bytes(self.EXTENSION_FIXTURE)
            else:
                path.write_bytes(f"fixture:{relative}".encode("utf-8"))
        (dist / "_internal/winpty/_winpty.cp314-win_amd64.pyd").write_bytes(b"native-extension-fixture")
        return dist

    def test_audit_reports_components_and_required_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = self._create_minimal_dist(temp_dir)

            report = package_release.audit_distribution(dist)

            self.assertEqual(report["file_count"], len(package_release.REQUIRED_PATHS) + 1)
            self.assertTrue(any(item["name"] == "_internal/PySide6" for item in report["components"]))
            self.assertIn("_internal/python_env/python.exe", report["required_paths"])
            self.assertLessEqual(
                report["editor_assets"]["unpacked_bytes"],
                package_release.EDITOR_MAX_UNPACKED_BYTES,
            )
            self.assertEqual(
                report["browser_skill_assets"]["cli"]["sha256"],
                self.fixture_hashes[package_release.BROWSER_SKILL_CLI_ARCHIVE],
            )
            self.assertEqual(report["git_runtime"]["credential_helper"], "manager")
            self.assertEqual(report["ripgrep_runtime"]["version"], "15.1.0")

    def test_audit_rejects_missing_or_changed_ripgrep_and_licenses(self):
        for name in self.rg_fixtures:
            for missing in (False, True):
                with self.subTest(name=name, missing=missing), tempfile.TemporaryDirectory() as temp_dir:
                    dist = self._create_minimal_dist(temp_dir)
                    target = dist / "_internal/ripgrep_env" / name
                    if missing:
                        target.unlink()
                    else:
                        target.write_bytes(b"wrong-version-or-corrupt")
                    with self.assertRaises(package_release.PackageAuditError):
                        package_release.audit_distribution(dist)

    def test_audit_rejects_selector_even_when_manager_is_also_configured(self):
        for helpers in (["helper-selector"], ["helper-selector", "manager"], []):
            with self.subTest(helpers=helpers), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                with patch.object(bundled_runtime_checks, "_git_config_values", return_value=helpers):
                    with self.assertRaisesRegex(package_release.PackageAuditError, "credential.helper=manager"):
                        package_release.audit_distribution(dist)

    def test_audit_rejects_disabled_authentication_and_missing_gui_dependency(self):
        for key in ("credential.interactive", "credential.guiPrompt"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                with patch.object(bundled_runtime_checks, "_git_config_values", side_effect=lambda _root, name: (
                    ["manager"] if name == "credential.helper" else ["false"] if name == key else []
                )):
                    with self.assertRaisesRegex(package_release.PackageAuditError, "disables account authorization"):
                        package_release.audit_distribution(dist)
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = self._create_minimal_dist(temp_dir)
            (dist / "_internal/git_bash_env/mingw64/bin/libSkiaSharp.dll").unlink()
            with self.assertRaisesRegex(package_release.PackageAuditError, "libSkiaSharp"):
                package_release.audit_distribution(dist)

    def test_audit_rejects_tampered_browser_skill_artifacts(self):
        for archive_name in (
            package_release.BROWSER_SKILL_CLI_ARCHIVE,
            package_release.BROWSER_SKILL_EXTENSION_ARCHIVE,
        ):
            with self.subTest(archive=archive_name), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                artifact = (
                    dist
                    / "_internal"
                    / "resources"
                    / "browser_skill"
                    / "artifacts"
                    / archive_name
                )
                artifact.write_bytes(artifact.read_bytes() + b"tampered")
                with self.assertRaisesRegex(package_release.PackageAuditError, "SHA256"):
                    package_release.audit_distribution(dist)

    def test_audit_rejects_debug_qt_resources_and_extra_locales(self):
        cases = (
            "_internal/PySide6/resources/qtwebengine_resources.debug.pak",
            "_internal/PySide6/translations/qtbase_fr.qm",
            "_internal/PySide6/translations/qtwebengine_locales/fr.pak",
            "_internal/PySide6/qml/QtQuick/qmldir",
            "_internal/icuuc.dll",
            "_internal/python_env/Lib/site-packages/pythonwin/Pythonwin.exe",
        )
        for relative in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                target = dist / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"forbidden")

                with self.assertRaises(package_release.PackageAuditError):
                    package_release.audit_distribution(dist)

    def test_native_terminal_extension_is_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = self._create_minimal_dist(temp_dir)
            (dist / "_internal/winpty/_winpty.cp314-win_amd64.pyd").unlink()
            with self.assertRaisesRegex(package_release.PackageAuditError, "pywinpty"):
                package_release.audit_distribution(dist)

    def test_audit_rejects_editor_source_maps_node_modules_and_cdn_imports(self):
        cases = (
            ("_internal/web/editors/dist/sheet-editor.js.map", b"{}"),
            ("_internal/web/editors/node_modules/runtime.js", b"dev"),
            (
                "_internal/web/editors/dist/remote.html",
                b'<script src="https://cdn.example.test/editor.js"></script>',
            ),
        )
        for relative, content in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                target = dist / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

                with self.assertRaises(package_release.PackageAuditError):
                    package_release.audit_distribution(dist)

    def test_checked_in_editor_notices_match_pinned_runtime_dependencies(self):
        editor_root = ROOT / "web" / "editors"
        manifest = json.loads((editor_root / "package.json").read_text(encoding="utf-8"))
        notices = (editor_root / "dist" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        licenses = editor_root / "dist" / "THIRD_PARTY_LICENSES.txt"

        for dependency, version in (
            ("@hufe921/canvas-editor", "0.9.137"),
            ("@hufe921/canvas-editor-plugin-docx", "0.0.6"),
            ("@univerjs/preset-sheets-core", "0.25.1"),
            ("@univerjs/presets", "0.25.1"),
        ):
            self.assertEqual(manifest["dependencies"][dependency], version)
            self.assertIn(f"`{dependency}@{version}`", notices)
        self.assertNotIn("UNDECLARED", notices)
        self.assertGreater(licenses.stat().st_size, 50_000)

    def test_release_zip_is_deterministic_and_uses_expected_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = self._create_minimal_dist(root)
            translation = dist / "_internal/PySide6/translations/qtbase_zh_CN.qm"
            translation.parent.mkdir(parents=True, exist_ok=True)
            translation.write_bytes(b"zh")
            first = root / "first.zip"
            second = root / "second.zip"

            first_hash = package_release.create_deterministic_zip(dist, first)
            os.utime(dist / "deepseek-cowork.exe", None)
            second_hash = package_release.create_deterministic_zip(dist, second)

            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertTrue(names)
                self.assertTrue(all(name.startswith("deepseek-cowork/") for name in names))
                self.assertEqual(
                    archive.getinfo(names[0]).date_time,
                    package_release.FIXED_ZIP_TIMESTAMP,
                )

    def test_package_release_writes_json_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = self._create_minimal_dist(root)
            output = root / f"deepseek-cowork-v{package_release.APP_VERSION}.zip"
            report_path = root / "report.json"

            report = package_release.package_release(
                dist,
                output,
                report_path,
            )

            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["zip_sha256"], report["zip_sha256"])
            self.assertEqual(saved["zip_bytes"], output.stat().st_size)
            manifest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(manifest["size"], output.stat().st_size)
            self.assertEqual(manifest["filename"], output.name)
            self.assertEqual(manifest["version"], package_release.APP_VERSION)
            self.assertEqual(manifest["notes"], (ROOT / "docs/releases" / f"{package_release.APP_VERSION}.md").read_text(encoding="utf-8"))

    def test_failed_archive_write_preserves_existing_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = self._create_minimal_dist(root)
            output = root / f"deepseek-cowork-v{package_release.APP_VERSION}.zip"
            output.write_bytes(b"previous-release")

            with patch.object(package_release, "create_deterministic_zip", side_effect=OSError("disk full")), self.assertRaises(OSError):
                package_release.package_release(
                    dist,
                    output,
                    root / "report.json",
                )

            self.assertEqual(output.read_bytes(), b"previous-release")
            self.assertFalse((root / "report.json").exists())

    def test_release_filename_and_notes_must_match_build_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(package_release.PackageAuditError, "filename"):
                package_release.package_release(root, root / "wrong.zip", root / "report.json")
            with patch.object(package_release, "ROOT", root), self.assertRaisesRegex(package_release.PackageAuditError, "notes"):
                package_release.package_release(root, root / f"deepseek-cowork-v{package_release.APP_VERSION}.zip", root / "report.json")
            self.assertFalse((root / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
