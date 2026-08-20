import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_version import APP_VERSION, compare_versions, is_newer_version, normalize_version
from core.updater import (
    APP_EXE_NAME,
    INTERNAL_DIR_NAME,
    UpdaterError,
    build_update_plan,
    cleanup_update_artifacts,
    create_windows_update_script,
    download_asset,
    extract_update_zip,
    expected_asset_path,
    launch_windows_update_script,
    local_update_package_version,
    prepare_local_update,
    prepare_update,
    select_release_asset,
    write_update_plan,
)


class TestAppVersion(unittest.TestCase):
    def test_current_app_version_is_canonical_semver(self):
        normalized = normalize_version(APP_VERSION)

        self.assertEqual(len(normalized), 3)
        self.assertEqual(APP_VERSION, ".".join(str(part) for part in normalized))

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

    def _write_local_update_package(self, path, *, safe=True, valid_structure=True):
        with zipfile.ZipFile(path, "w") as archive:
            if not safe:
                archive.writestr("../escape.txt", "bad")
            elif valid_structure:
                archive.writestr(f"deepseek-cowork/{APP_EXE_NAME}", "new exe")
                archive.writestr(
                    f"deepseek-cowork/{INTERNAL_DIR_NAME}/config.json",
                    '{"updated": true}',
                )
            else:
                archive.writestr("README.txt", "not an app package")

    def _write_local_install_dir(self):
        install_dir = os.path.join(self.temp_dir, "install")
        os.makedirs(os.path.join(install_dir, INTERNAL_DIR_NAME), exist_ok=True)
        with open(os.path.join(install_dir, APP_EXE_NAME), "w", encoding="utf-8") as handle:
            handle.write("old exe")
        with open(
            os.path.join(install_dir, INTERNAL_DIR_NAME, "config.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write('{"updated": false}')
        return install_dir

    def test_local_update_package_version_requires_standard_name(self):
        self.assertEqual(
            local_update_package_version("deepseek-cowork-v5.2.0.zip"),
            "5.2.0",
        )
        for name in ("update.zip", "deepseek-cowork-v5.2.zip", "deepseek-cowork-v5.2.0-beta.zip"):
            with self.subTest(name=name), self.assertRaises(UpdaterError):
                local_update_package_version(name)

    def test_prepare_local_update_builds_plan_without_network_or_copying_package(self):
        source_dir = os.path.join(self.temp_dir, "downloads")
        target_dir = os.path.join(self.temp_dir, "updates")
        os.makedirs(source_dir, exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)
        zip_path = os.path.join(source_dir, "deepseek-cowork-v5.2.0.zip")
        self._write_local_update_package(zip_path)
        install_dir = self._write_local_install_dir()

        with patch("core.updater.updates_dir", return_value=target_dir), \
             patch("core.updater.requests.get") as mocked_get:
            result = prepare_local_update(
                zip_path,
                current_version="5.1.6",
                install_dir=install_dir,
            )

        mocked_get.assert_not_called()
        self.assertEqual(result["update_source"], "local")
        self.assertEqual(result["release"]["tag_name"], "5.2.0")
        self.assertEqual(result["zip_path"], os.path.abspath(zip_path))
        self.assertTrue(os.path.isfile(zip_path))
        self.assertTrue(os.path.isfile(result["change_plan_path"]))
        self.assertGreaterEqual(result["change_summary"]["modified"], 2)
        self.assertFalse(os.path.exists(os.path.join(target_dir, os.path.basename(zip_path))))

    def test_prepare_local_update_keeps_selected_package_inside_update_cache(self):
        target_dir = os.path.join(self.temp_dir, "updates")
        os.makedirs(target_dir, exist_ok=True)
        zip_path = os.path.join(target_dir, "deepseek-cowork-v5.2.0.zip")
        old_zip = os.path.join(target_dir, "deepseek-cowork-v5.1.0.zip")
        self._write_local_update_package(zip_path)
        with open(old_zip, "wb") as handle:
            handle.write(b"old")

        with patch("core.updater.updates_dir", return_value=target_dir):
            prepare_local_update(
                zip_path,
                current_version="5.1.6",
                install_dir=self._write_local_install_dir(),
            )

        self.assertTrue(os.path.isfile(zip_path))
        self.assertFalse(os.path.exists(old_zip))

    def test_prepare_local_update_rejects_same_or_older_version(self):
        install_dir = self._write_local_install_dir()
        for version in ("5.1.6", "5.1.5"):
            zip_path = os.path.join(self.temp_dir, f"deepseek-cowork-v{version}.zip")
            self._write_local_update_package(zip_path)
            with self.subTest(version=version), self.assertRaisesRegex(UpdaterError, "不高于当前版本"):
                prepare_local_update(
                    zip_path,
                    current_version="5.1.6",
                    install_dir=install_dir,
                )

    def test_prepare_local_update_rejects_missing_or_invalid_package(self):
        install_dir = self._write_local_install_dir()
        missing = os.path.join(self.temp_dir, "deepseek-cowork-v5.2.0.zip")
        with self.assertRaisesRegex(UpdaterError, "不存在或已被删除"):
            prepare_local_update(missing, current_version="5.1.6", install_dir=install_dir)

        renamed = os.path.join(self.temp_dir, "update.zip")
        self._write_local_update_package(renamed)
        with self.assertRaisesRegex(UpdaterError, "名称无效"):
            prepare_local_update(renamed, current_version="5.1.6", install_dir=install_dir)

    def test_prepare_local_update_rejects_corrupt_unsafe_and_wrong_structure_zip(self):
        install_dir = self._write_local_install_dir()
        cases = (
            ("5.2.0", "corrupt", None),
            ("5.2.1", "unsafe", False),
            ("5.2.2", "structure", False),
        )
        for version, kind, package_flag in cases:
            case_dir = os.path.join(self.temp_dir, kind)
            target_dir = os.path.join(case_dir, "updates")
            os.makedirs(target_dir, exist_ok=True)
            zip_path = os.path.join(case_dir, f"deepseek-cowork-v{version}.zip")
            if kind == "corrupt":
                with open(zip_path, "wb") as handle:
                    handle.write(b"not a zip")
            elif kind == "unsafe":
                self._write_local_update_package(zip_path, safe=package_flag)
            else:
                self._write_local_update_package(zip_path, valid_structure=package_flag)
            with self.subTest(kind=kind), \
                 patch("core.updater.updates_dir", return_value=target_dir), \
                 self.assertRaises(UpdaterError):
                prepare_local_update(
                    zip_path,
                    current_version="5.1.6",
                    install_dir=install_dir,
                )

    def test_download_asset_reuses_existing_package_when_size_matches(self):
        asset = {
            "name": "deepseek-cowork-v4.8.0.zip",
            "browser_download_url": "https://example/app.zip",
            "size": 4,
        }
        zip_path = expected_asset_path(asset, target_dir=self.temp_dir)
        with open(zip_path, "wb") as handle:
            handle.write(b"1234")
        progress = []

        with patch("core.updater.requests.get") as mocked_get:
            result = download_asset(asset, target_dir=self.temp_dir, progress_callback=lambda msg, pct=None: progress.append((msg, pct)))

        self.assertEqual(result, zip_path)
        mocked_get.assert_not_called()
        self.assertTrue(any("跳过下载" in message for message, _percent in progress))

    def test_cleanup_update_artifacts_removes_old_update_traces_and_keeps_current_package(self):
        keep_zip = os.path.join(self.temp_dir, "deepseek-cowork-v4.8.0.zip")
        old_zip = os.path.join(self.temp_dir, "deepseek-cowork-v4.7.9.zip")
        temp_download = os.path.join(self.temp_dir, "deepseek-cowork-v4.8.0.zip.download")
        unrelated = os.path.join(self.temp_dir, "notes.txt")
        for path in (keep_zip, old_zip, temp_download, unrelated):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x")
        for dirname in ("staged-20260615-120000", "backup-20260615-120000"):
            os.makedirs(os.path.join(self.temp_dir, dirname), exist_ok=True)
        for name in ("apply-update-20260615-120000.ps1", "apply-update-20260615-120000.cmd", "update.log", "update-launch.log"):
            with open(os.path.join(self.temp_dir, name), "w", encoding="utf-8") as handle:
                handle.write("x")

        removed = cleanup_update_artifacts(target_dir=self.temp_dir, keep_paths=[keep_zip])

        self.assertGreaterEqual(removed, 8)
        self.assertTrue(os.path.exists(keep_zip))
        self.assertTrue(os.path.exists(unrelated))
        self.assertFalse(os.path.exists(old_zip))
        self.assertFalse(os.path.exists(temp_download))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "staged-20260615-120000")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "backup-20260615-120000")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "apply-update-20260615-120000.ps1")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "update.log")))

    def test_prepare_update_cleans_artifacts_before_download_and_staging(self):
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
        old_zip = os.path.join(self.temp_dir, "deepseek-cowork-v4.7.9.zip")
        with open(old_zip, "w", encoding="utf-8") as handle:
            handle.write("old")
        os.makedirs(os.path.join(self.temp_dir, "staged-20260615-120000"), exist_ok=True)
        expected_zip = os.path.join(self.temp_dir, "deepseek-cowork-v4.8.0.zip")
        staged_dir = os.path.join(self.temp_dir, "staged-current")

        with patch("core.updater.fetch_latest_release", return_value=release), \
             patch("core.updater.updates_dir", return_value=self.temp_dir), \
             patch("core.updater.download_asset", return_value=expected_zip) as mocked_download, \
             patch("core.updater.extract_update_zip", return_value=staged_dir) as mocked_extract:
            result = prepare_update(current_version="4.7.2", download=True)

        self.assertFalse(os.path.exists(old_zip))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "staged-20260615-120000")))
        mocked_download.assert_called_once()
        mocked_extract.assert_called_once_with(expected_zip, target_dir=self.temp_dir, progress_callback=None)
        self.assertEqual(result["zip_path"], expected_zip)
        self.assertEqual(result["staged_app_dir"], staged_dir)

    def test_build_update_plan_detects_added_modified_deleted_and_unchanged_files(self):
        install_dir = os.path.join(self.temp_dir, "install")
        staged_dir = os.path.join(self.temp_dir, "staged")
        os.makedirs(os.path.join(install_dir, INTERNAL_DIR_NAME), exist_ok=True)
        os.makedirs(os.path.join(staged_dir, INTERNAL_DIR_NAME), exist_ok=True)
        for root in (install_dir, staged_dir):
            with open(os.path.join(root, APP_EXE_NAME), "w", encoding="utf-8") as handle:
                handle.write("same exe")
        with open(os.path.join(install_dir, INTERNAL_DIR_NAME, "same.txt"), "w", encoding="utf-8") as handle:
            handle.write("same")
        with open(os.path.join(staged_dir, INTERNAL_DIR_NAME, "same.txt"), "w", encoding="utf-8") as handle:
            handle.write("same")
        with open(os.path.join(install_dir, INTERNAL_DIR_NAME, "changed.txt"), "w", encoding="utf-8") as handle:
            handle.write("old")
        with open(os.path.join(staged_dir, INTERNAL_DIR_NAME, "changed.txt"), "w", encoding="utf-8") as handle:
            handle.write("new")
        with open(os.path.join(install_dir, INTERNAL_DIR_NAME, "removed.txt"), "w", encoding="utf-8") as handle:
            handle.write("removed")
        with open(os.path.join(staged_dir, INTERNAL_DIR_NAME, "added.txt"), "w", encoding="utf-8") as handle:
            handle.write("added")
        os.makedirs(os.path.join(install_dir, "user_data"), exist_ok=True)
        with open(os.path.join(install_dir, "user_data", "keep.json"), "w", encoding="utf-8") as handle:
            handle.write("private")

        plan = build_update_plan(install_dir, staged_dir)

        self.assertEqual([item["path"] for item in plan["added"]], ["_internal/added.txt"])
        self.assertEqual([item["path"] for item in plan["modified"]], ["_internal/changed.txt"])
        self.assertEqual([item["path"] for item in plan["deleted"]], ["_internal/removed.txt"])
        self.assertEqual(plan["summary"]["unchanged"], 2)
        self.assertFalse(any("user_data" in item["path"] for key in ("added", "modified", "deleted") for item in plan[key]))

    def _create_change_plan(self, install_dir, staged_dir):
        plan = build_update_plan(install_dir, staged_dir)
        return write_update_plan(plan, target_dir=self.temp_dir)

    def test_create_windows_update_script_generates_observable_gui_and_fallback(self):
        install_dir = os.path.join(self.temp_dir, "install")
        staged_dir = os.path.join(self.temp_dir, "staged")
        os.makedirs(os.path.join(staged_dir, INTERNAL_DIR_NAME), exist_ok=True)
        os.makedirs(install_dir, exist_ok=True)
        with open(os.path.join(staged_dir, APP_EXE_NAME), "w", encoding="utf-8") as handle:
            handle.write("")

        with patch("core.updater.sys.platform", "win32"):
            plan_path = self._create_change_plan(install_dir, staged_dir)
            script_path = create_windows_update_script(
                install_dir=install_dir,
                staged_app_dir=staged_dir,
                change_plan_path=plan_path,
                current_pid=12345,
                extra_wait_pids=[23456],
                target_dir=self.temp_dir,
            )

        fallback_path = os.path.splitext(script_path)[0] + ".cmd"
        self.assertTrue(script_path.endswith(".ps1"))
        self.assertTrue(os.path.exists(script_path))
        self.assertTrue(os.path.exists(fallback_path))
        with open(script_path, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xef\xbb\xbf")
        with open(script_path, "r", encoding="utf-8-sig") as handle:
            ps_content = handle.read()
        with open(fallback_path, "r", encoding="utf-8") as handle:
            cmd_content = handle.read()

        self.assertIn("System.Windows.Forms", ps_content)
        self.assertIn("DeepSeek Cowork Update", ps_content)
        self.assertIn("ProgressBar", ps_content)
        self.assertIn("$form.MinimizeBox = $true", ps_content)
        self.assertIn("$form.TopMost = $false", ps_content)
        self.assertIn("$form.WindowState = 'Normal'", ps_content)
        self.assertIn("$PidsToWait = @(12345, 23456)", ps_content)
        self.assertIn("ConvertFrom-Json", ps_content)
        self.assertIn("Get-FileHash", ps_content)
        self.assertIn("Attempting differential rollback", ps_content)
        self.assertNotIn("robocopy", ps_content.lower())
        self.assertNotIn("/MIR", ps_content)
        self.assertIn("10", ps_content)
        self.assertIn("25", ps_content)
        self.assertIn("55", ps_content)
        self.assertIn("70", ps_content)
        self.assertIn("82", ps_content)
        self.assertIn("86", ps_content)
        self.assertIn("90", ps_content)
        self.assertIn("100", ps_content)
        self.assertIn("user_data", ps_content)
        self.assertIn("update.log", ps_content)
        self.assertIn("-WindowStyle Hidden", ps_content)
        self.assertIn("powershell.exe", cmd_content)
        self.assertIn(os.path.basename(script_path), cmd_content)

    def test_create_windows_update_script_can_start_minimized_for_background_install(self):
        install_dir = os.path.join(self.temp_dir, "install")
        staged_dir = os.path.join(self.temp_dir, "staged")
        os.makedirs(os.path.join(staged_dir, INTERNAL_DIR_NAME), exist_ok=True)
        os.makedirs(install_dir, exist_ok=True)
        with open(os.path.join(staged_dir, APP_EXE_NAME), "w", encoding="utf-8") as handle:
            handle.write("")

        with patch("core.updater.sys.platform", "win32"):
            plan_path = self._create_change_plan(install_dir, staged_dir)
            script_path = create_windows_update_script(
                install_dir=install_dir,
                staged_app_dir=staged_dir,
                change_plan_path=plan_path,
                current_pid=12345,
                target_dir=self.temp_dir,
                background_install=True,
            )

        with open(script_path, "r", encoding="utf-8-sig") as handle:
            ps_content = handle.read()

        self.assertIn("$RunInBackground = $true", ps_content)
        self.assertIn("$form.WindowState = 'Minimized'", ps_content)
        self.assertIn("$form.WindowState = 'Normal'", ps_content)
        self.assertIn("$form.Activate()", ps_content)


    def test_create_windows_update_script_rejects_non_windows(self):
        staged_dir = os.path.join(self.temp_dir, "staged")
        os.makedirs(os.path.join(staged_dir, INTERNAL_DIR_NAME), exist_ok=True)
        with open(os.path.join(staged_dir, APP_EXE_NAME), "w", encoding="utf-8") as handle:
            handle.write("")

        with patch("core.updater.sys.platform", "linux"):
            with self.assertRaises(UpdaterError):
                create_windows_update_script(
                    install_dir=os.path.join(self.temp_dir, "install"),
                    staged_app_dir=staged_dir,
                    change_plan_path=os.path.join(self.temp_dir, "missing.json"),
                    target_dir=self.temp_dir,
                )

    def test_launch_windows_update_script_does_not_use_windowstyle_hidden(self):
        script_path = os.path.join(self.temp_dir, "apply-update.ps1")
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write("")

        with patch("core.updater.sys.platform", "win32"), \
             patch("core.updater.subprocess.Popen") as popen:
            launch_windows_update_script(script_path)

        args = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertIn("-STA", args)
        self.assertIn("-File", args)
        self.assertNotIn("-WindowStyle", args)
        self.assertNotIn("Hidden", args)
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertNotIn("creationflags", kwargs)
        self.assertEqual(kwargs.get("cwd"), self.temp_dir)
        self.assertIsNotNone(kwargs.get("stdout"))
        self.assertIsNotNone(kwargs.get("stderr"))

    def test_create_windows_update_script_hides_relaunch_window(self):
        install_dir = os.path.join(self.temp_dir, "install")
        staged_dir = os.path.join(self.temp_dir, "staged")
        os.makedirs(os.path.join(staged_dir, INTERNAL_DIR_NAME), exist_ok=True)
        os.makedirs(install_dir, exist_ok=True)
        with open(os.path.join(staged_dir, APP_EXE_NAME), "w", encoding="utf-8") as handle:
            handle.write("")

        with patch("core.updater.sys.platform", "win32"):
            plan_path = self._create_change_plan(install_dir, staged_dir)
            script_path = create_windows_update_script(
                install_dir=install_dir,
                staged_app_dir=staged_dir,
                change_plan_path=plan_path,
                current_pid=12345,
                target_dir=self.temp_dir,
            )

        with open(script_path, "r", encoding="utf-8-sig") as handle:
            ps_content = handle.read()

        self.assertIn("-WindowStyle Hidden", ps_content)


if __name__ == "__main__":
    unittest.main()
