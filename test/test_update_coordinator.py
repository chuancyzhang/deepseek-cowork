import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from core import updater
from core.update_coordinator import UpdateCoordinator, UpdateWorker, prepare_online, release_notes_text, load_prepared
from ui.update_panel import UpdatePanel


class UpdatePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def package(self, name="deepseek-cowork-v9.0.0.zip"):
        path = Path(self.root, name)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("deepseek-cowork/deepseek-cowork.exe", b"new exe")
            archive.writestr("deepseek-cowork/_internal/data.txt", b"data")
        return path

    def release(self):
        return {"tag_name": "v9.0.0", "body": "新功能", "assets": [{"name": "deepseek-cowork-v9.0.0.zip", "browser_download_url": "https://example.invalid/app.zip", "id": 9}]}

    def test_only_five_network_attempts_and_cancel_interrupts_backoff(self):
        cancel = threading.Event()
        events = []
        with patch.object(updater, "fetch_latest_release", side_effect=updater.UpdateNetworkError("offline")) as fetch:
            with self.assertRaises(updater.UpdateNetworkError):
                prepare_online(self.root, "1.0.0", True, cancel, lambda *a, **k: events.append(k), retry_delays=(0, 0, 0, 0))
        self.assertEqual(fetch.call_count, 5)
        self.assertEqual([e["attempt"] for e in events if "attempt" in e][-1], 5)
        def emit(phase, **fields):
            if phase == "retrying":
                cancel.set()
        with patch.object(updater, "fetch_latest_release", side_effect=updater.UpdateNetworkError("offline")) as fetch:
            with self.assertRaises(updater.UpdateCancelled):
                prepare_online(self.root, "1.0.0", True, cancel, emit)
        self.assertEqual(fetch.call_count, 1)

    def test_check_and_download_share_budget(self):
        with patch.object(updater, "fetch_latest_release", side_effect=[updater.UpdateNetworkError("offline"), self.release()]) as fetch, patch.object(updater, "download_asset", side_effect=updater.UpdateNetworkError("offline")) as download:
            with self.assertRaises(updater.UpdateNetworkError):
                prepare_online(self.root, "1.0.0", True, threading.Event(), lambda *a, **k: None, retry_delays=(0, 0, 0, 0))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(download.call_count, 4)

    def test_non_network_failure_does_not_retry(self):
        with patch.object(updater, "fetch_latest_release", return_value=self.release()), patch.object(updater, "download_asset", side_effect=OSError("disk full")) as download:
            with self.assertRaises(OSError):
                prepare_online(self.root, "1.0.0", True, threading.Event(), lambda *a, **k: None)
        self.assertEqual(download.call_count, 1)

    def test_latest_zip_cache_survives_restart_without_network(self):
        downloads = Path(self.root, "downloads"); downloads.mkdir()
        old = downloads / "deepseek-cowork-v8.0.0.zip"; old.write_bytes(b"old")
        package = self.package()
        target = downloads / package.name
        target.write_bytes(package.read_bytes())
        asset = updater.select_release_asset(self.release()).as_dict()
        (downloads / "asset.json").write_text(json.dumps({"asset": asset, "sha256": updater._hash_file(target)}))
        backup = Path(self.root, "transactions", "old", "backup"); backup.mkdir(parents=True)
        (backup / "important").write_text("old app")
        with patch.object(updater, "fetch_latest_release", return_value=self.release()), patch.object(updater.requests, "get") as request:
            prepared = prepare_online(self.root, "1.0.0", True, threading.Event(), lambda *a, **k: None)
            restored = load_prepared(self.root, "1.0.0")
        request.assert_not_called()
        self.assertEqual(restored["zip_path"], prepared["zip_path"])
        self.assertEqual(list(downloads.glob("*.zip")), [target])
        self.assertTrue(package.exists())  # A user-provided package outside downloads.
        self.assertTrue((backup / "important").exists())
        Path(restored["staged_app_dir"], "_internal", "data.txt").write_text("corrupted stage")
        fresh = load_prepared(self.root, "1.0.0")
        self.assertEqual(Path(fresh["staged_app_dir"], "_internal", "data.txt").read_text(), "data")

    def test_same_size_corruption_and_changed_digest_not_reused(self):
        package = self.package()
        asset = {"size": package.stat().st_size, "digest": "sha256:" + "0" * 64}
        self.assertFalse(updater.is_cached_asset_valid(asset, str(package)))
        package.write_bytes(b"x" * asset["size"])
        asset.pop("digest")
        self.assertFalse(updater.is_cached_asset_valid(asset, str(package)))

    def test_download_cancel_removes_partial_zip(self):
        cancel = threading.Event()
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        def chunks(**_):
            yield b"part"
            cancel.set()
            yield b"rest"
        response.iter_content.side_effect = chunks
        with patch.object(updater.requests, "get", return_value=response):
            with self.assertRaises(updater.UpdateCancelled):
                updater.download_asset(self.release()["assets"][0], self.root, cancel_event=cancel)
        self.assertFalse(list(Path(self.root).glob("*.download")))
        self.assertFalse(list(Path(self.root).glob("*.zip")))

    def test_notes_strip_all_embedded_resources_without_network(self):
        source = '# 改进\n\n- 更快\n- 更稳定\n\n![image](https://a/x.png)\n![ref][x]\n[x]: https://a/y.png\n\n<p>文字<img src="file:///C:/secret"></p><svg><text>hidden</text></svg><video src="remote">hidden</video><iframe>hidden</iframe><script>alert(1)</script><style>x</style>'
        with patch.object(updater.requests, "get") as request:
            text = release_notes_text(source)
        self.assertIn("改进", text); self.assertIn("更快", text); self.assertIn("文字", text)
        for removed in ("https://", "file://", "hidden", "alert", "<img"):
            self.assertNotIn(removed, text)
        request.assert_not_called()

    def test_coordinator_cancels_download_before_local_and_no_duplicate_install(self):
        coordinator = UpdateCoordinator(self.root, True, root=self.root)
        worker = SimpleNamespace(cancel=threading.Event(), deleteLater=lambda: None, error=None, result={})
        coordinator.worker = worker
        with patch.object(coordinator, "_start") as start:
            coordinator.select_local(self.package())
            self.assertTrue(worker.cancel.is_set())
            start.assert_not_called()
            coordinator._finished()
            self.assertEqual(start.call_args.args[0], "local")
        coordinator.prepared = {"staged_app_dir": self.root}
        coordinator.worker = None
        with patch.object(coordinator, "_start") as start:
            coordinator.request_install(); coordinator.request_install()
        self.assertEqual(start.call_count, 1)

    def test_cancel_after_worker_completes_never_installs(self):
        coordinator = UpdateCoordinator(self.root, True, root=self.root)
        cancel = threading.Event(); cancel.set()
        coordinator.worker = SimpleNamespace(cancel=cancel, error=None, result={"staged_app_dir": self.root}, operation="plan", deleteLater=lambda: None)
        installed = []
        coordinator.installation_ready.connect(installed.append)
        coordinator._finished()
        self.assertEqual(installed, [])

    def test_local_selection_wins_over_delayed_startup_and_stale_cache_event(self):
        coordinator = UpdateCoordinator(self.root, True, root=self.root)
        with patch.object(coordinator, "_start") as start:
            coordinator.select_local(self.package())
            coordinator.start_automatic()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0], "local")
        coordinator.pending = ("local", "selected.zip")
        coordinator._event({"phase": "ready", "result": {"staged_app_dir": "stale"}})
        self.assertIsNone(coordinator.prepared)

    def test_download_retry_does_not_grant_install_permission(self):
        coordinator = UpdateCoordinator(self.root, True, root=self.root)
        coordinator.publish(phase="error", result={"update_available": True, "release": {"tag_name": "9.0.0"}})
        panel = UpdatePanel(coordinator)
        with patch.object(coordinator, "check") as check, patch.object(coordinator, "request_install") as install:
            panel.act()
        check.assert_called_once()
        install.assert_not_called()
        panel.close(); panel.deleteLater()

    def test_panel_recreated_uses_same_job_and_plain_text_notes(self):
        coordinator = UpdateCoordinator(self.root, True, root=self.root)
        result = {"release": {"tag_name": "9.0.0", "body": "文字 ![图](https://example.invalid/img)"}, "update_available": True}
        coordinator.publish(phase="downloading", result=result, percent=45)
        with patch.object(coordinator, "_start") as start:
            panel = UpdatePanel(coordinator)
            panel.close(); panel.deleteLater()
            other = UpdatePanel(coordinator)
            self.assertEqual(other.notes.text(), "文字")
            self.assertEqual(other.progress.value(), 45)
            self.assertTrue(other.details.isHidden())
            start.assert_not_called()
            other.close(); other.deleteLater()

    def test_source_mode_checks_without_download(self):
        with patch.object(updater, "fetch_latest_release", return_value=self.release()), patch.object(updater, "download_asset") as download:
            result = prepare_online(self.root, "1.0.0", False, threading.Event(), lambda *a, **k: None)
        self.assertTrue(result["update_available"])
        download.assert_not_called()
