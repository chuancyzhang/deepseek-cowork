import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core import runtime_components


class TestRuntimeComponents(unittest.TestCase):
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
        self.assertIn("quantstats", runtime_components.TOOLKITS["finance"]["packages"])
        self.assertIn("reportlab", runtime_components.TOOLKITS["documents"]["packages"])
        self.assertIn("reportlab", runtime_components.TOOLKITS["documents"]["imports"])

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

            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"packages": runtime_components.TOOLKITS["documents"]["packages"]}, handle)

            current_status = runtime_components.toolkit_status("documents")
            self.assertFalse(current_status["needs_update"])
            self.assertEqual(current_status["missing_packages"], [])

    def test_installed_toolkit_paths_require_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(runtime_components, "toolkits_root", return_value=temp_dir):
            target = runtime_components.toolkit_path("documents")
            os.makedirs(target)
            self.assertNotIn(target, runtime_components.installed_toolkit_paths())
            marker = os.path.join(os.path.dirname(target), "toolkit.json")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertIn(target, runtime_components.installed_toolkit_paths())

    def test_node_source_uses_fixed_archive_and_hash(self):
        self.assertEqual(runtime_components.NODE_ARCHIVE, "node-v24.14.1-win-x64.zip")
        self.assertEqual(len(runtime_components.NODE_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
