import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "package_release.py"
SPEC = importlib.util.spec_from_file_location("package_release", MODULE_PATH)
package_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_release)


class PackageReleaseTests(unittest.TestCase):
    def _create_minimal_dist(self, root):
        dist = Path(root) / "deepseek-cowork"
        for relative in package_release.REQUIRED_PATHS:
            path = dist / Path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"fixture:{relative}".encode("utf-8"))
        return dist

    def test_audit_reports_components_and_required_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = self._create_minimal_dist(temp_dir)

            report = package_release.audit_distribution(dist, max_dist_mb=1)

            self.assertEqual(report["file_count"], len(package_release.REQUIRED_PATHS))
            self.assertTrue(any(item["name"] == "_internal/PySide6" for item in report["components"]))
            self.assertIn("_internal/python_env/python.exe", report["required_paths"])

    def test_audit_rejects_debug_qt_resources_and_extra_locales(self):
        cases = (
            "_internal/PySide6/resources/qtwebengine_resources.debug.pak",
            "_internal/PySide6/translations/qtbase_fr.qm",
            "_internal/PySide6/translations/qtwebengine_locales/fr.pak",
            "_internal/PySide6/qml/QtQuick/qmldir",
            "_internal/python_env/Lib/site-packages/pythonwin/Pythonwin.exe",
        )
        for relative in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp_dir:
                dist = self._create_minimal_dist(temp_dir)
                target = dist / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"forbidden")

                with self.assertRaises(package_release.PackageAuditError):
                    package_release.audit_distribution(dist, max_dist_mb=1)

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
            output = root / "release.zip"
            report_path = root / "report.json"

            report = package_release.package_release(
                dist,
                output,
                report_path,
                max_dist_mb=1,
                max_zip_mb=1,
            )

            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["zip_sha256"], report["zip_sha256"])
            self.assertEqual(saved["zip_bytes"], output.stat().st_size)

    def test_failed_size_gate_preserves_existing_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist = self._create_minimal_dist(root)
            output = root / "release.zip"
            output.write_bytes(b"previous-release")

            with self.assertRaises(package_release.PackageAuditError):
                package_release.package_release(
                    dist,
                    output,
                    root / "report.json",
                    max_dist_mb=1,
                    max_zip_mb=0,
                )

            self.assertEqual(output.read_bytes(), b"previous-release")
            self.assertFalse((root / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
