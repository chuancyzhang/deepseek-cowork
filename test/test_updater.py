import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_version import compare_versions, is_newer_version, normalize_version
from core.updater import (
    APP_EXE_NAME,
    INTERNAL_DIR_NAME,
    UpdaterError,
    extract_update_zip,
    prepare_update,
    select_release_asset,
)


class TestAppVersion(unittest.TestCase):
    def test_normalize_version_accepts_release_tags(self):
        self.assertEqual(normalize_version("V4.7"), (4, 7, 0))
        self.assertEqual(normalize_version("v4.7.2"), (4, 7, 2))
        self.assertEqual(normalize_version("4.7.2"), (4, 7, 2))

    def test_compare_versions(self):
        self.assertEqual(compare_versions("V4.7.2", "4.7.2"), 0)
        self.assertEqual(compare_versions("V4.7.3", "4.7.2"), 1)
        self.assertEqual(compare_versions("V4.7", "4.7.1"), -1)
        self.assertTrue(is_newer_version("v4.8.0", "4.7.2"))


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_select_release_asset_prefers_app_zip(self):
        release = {
            "assets": [
                {"name": "source.zip", "browser_download_url": "https://example/source.zip", "size": 1},
                {"name": "deepseek-cowork-v4.7.2.zip", "browser_download_url": "https://example/app.zip", "size": 2},
                {"name": "notes.txt", "browser_download_url": "https://example/notes.txt", "size": 3},
            ]
        }

        asset = select_release_asset(release)

        self.assertEqual(asset.name, "deepseek-cowork-v4.7.2.zip")
        self.assertEqual(asset.size, 2)

    def test_select_release_asset_rejects_missing_zip(self):
        with self.assertRaises(UpdaterError):
            select_release_asset({"assets": [{"name": "source.zip", "browser_download_url": "https://example/source.zip"}]})

    def test_extract_update_zip_validates_structure(self):
        zip_path = os.path.join(self.temp_dir, "update.zip")
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr(f"deepseek-cowork/{APP_EXE_NAME}", "")
            archive.writestr(f"deepseek-cowork/{INTERNAL_DIR_NAME}/config.json", "{}")

        staged_dir = extract_update_zip(zip_path, target_dir=self.temp_dir)

        self.assertTrue(os.path.isfile(os.path.join(staged_dir, APP_EXE_NAME)))
        self.assertTrue(os.path.isdir(os.path.join(staged_dir, INTERNAL_DIR_NAME)))

    def test_extract_update_zip_rejects_invalid_structure(self):
        zip_path = os.path.join(self.temp_dir, "bad.zip")
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("README.txt", "bad")

        with self.assertRaises(UpdaterError):
            extract_update_zip(zip_path, target_dir=self.temp_dir)

    def test_extract_update_zip_rejects_unsafe_paths(self):
        zip_path = os.path.join(self.temp_dir, "unsafe.zip")
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("../escape.txt", "bad")

        with self.assertRaises(UpdaterError):
            extract_update_zip(zip_path, target_dir=self.temp_dir)

    def test_prepare_update_returns_latest_without_download(self):
        release = {
            "tag_name": "V4.8.0",
            "name": "V4.8.0",
            "html_url": "https://example/release",
            "body": "Release notes",
            "assets": [
                {
                    "name": "deepseek-cowork-v4.8.0.zip",
                    "browser_download_url": "https://example/app.zip",
                    "size": 100,
                }
            ],
        }
        with patch("core.updater.fetch_latest_release", return_value=release):
            result = prepare_update(current_version="4.7.2", download=False)

        self.assertTrue(result["update_available"])
        self.assertEqual(result["release"]["tag_name"], "V4.8.0")
        self.assertEqual(result["asset"]["name"], "deepseek-cowork-v4.8.0.zip")
        self.assertNotIn("zip_path", result)


if __name__ == "__main__":
    unittest.main()
