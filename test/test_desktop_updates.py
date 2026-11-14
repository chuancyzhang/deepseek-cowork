"""Exercise the Electron entry points, owned cache and source selection together."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import zipfile

import pytest

from core import updater
from core.app_version import APP_VERSION
from core.desktop_updates import DesktopUpdates
from core.platform_runtime import platform_runtime
from core.update_coordinator import UpdateCoordinator, UpdateWorker, load_prepared, prepare_online


def desktop_zip(path, version=APP_VERSION, connection=None, extra=None):
    identity = {"schema_version": 1, "app_layout": "electron", "target": "windows-x64", "version": version}
    files = {"deepseek-cowork.exe": b"fixture", "resources/app.asar": b"interface",
             "resources/service/cowork-service.exe": b"service", "desktop-release.json": json.dumps(identity).encode()}
    if connection:
        files["enterprise_connection.json"] = json.dumps(connection).encode()
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in files.items():
            archive.writestr("DeepSeek Cowork-win32-x64/" + name, contents)
    return {"schema_version": 1, "app_layout": "electron", "target_os": "windows", "arch": "x64", "version": version,
            "filename": f"cowork-desktop-v{version}-windows-x64.zip", "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "notes": "更新说明"}


def wait(coordinator):
    while worker := coordinator.worker:
        assert worker.join(10), "update worker did not finish"


def test_local_same_version_uses_owned_copy_and_restores_offline(tmp_path, monkeypatch):
    package = tmp_path / "任意文件名.zip"
    manifest = desktop_zip(package)
    (tmp_path / "latest-desktop-windows-x64.json").write_text(json.dumps(manifest))
    root = tmp_path / "cache"
    result = updater.prepare_desktop_local(str(package), str(root), APP_VERSION, None, threading.Event(), None)
    assert result["manifest_verified"] and result["update_source"] == "local"
    assert Path(result["zip_path"]) != package and package.exists()
    package.unlink()  # A removable USB/source folder is no longer required.
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kw: pytest.fail("local recovery must work offline"))
    restored = load_prepared(str(root), APP_VERSION, layout="electron")
    assert restored["sha256"] == manifest["sha256"]
    assert restored["release"]["tag_name"] == APP_VERSION
    assert not Path(result["staged_app_dir"]).exists()
    assert len(list(root.glob("staged-*"))) == 1


def test_local_rejects_downgrade_and_mismatched_sidecar(tmp_path):
    package = tmp_path / "renamed-as-new.zip"
    desktop_zip(package, "1.0.0")
    with pytest.raises(updater.UpdaterError, match="低于"):
        updater.prepare_desktop_local(str(package), str(tmp_path / "cache"), APP_VERSION, None, threading.Event(), None)
    manifest = desktop_zip(package)
    manifest["sha256"] = "0" * 64
    (tmp_path / "latest-desktop-windows-x64.json").write_text(json.dumps(manifest))
    with pytest.raises(updater.UpdaterError, match="不一致"):
        updater.prepare_desktop_local(str(package), str(tmp_path / "cache"), APP_VERSION, None, threading.Event(), None)


@pytest.mark.skipif(os.name != "nt", reason="Windows path boundary")
def test_extract_and_plan_support_paths_beyond_max_path(tmp_path):
    member = "resources/service/_internal/ai_skills/frontend-slides/plugins/frontend-slides/skills/frontend-slides/bold-template-pack/selection-index.json"
    package = tmp_path / "package.zip"
    desktop_zip(package, extra={member: b"kept"})
    root = tmp_path / ("long-directory-" * 6)
    staged = updater.extract_update_zip(str(package), str(root), layout="electron")
    path = Path(staged) / member
    assert len(str(path)) > 260
    assert platform_runtime.filesystem_path(path).read_bytes() == b"kept"
    install = tmp_path / "install"
    install.mkdir()
    plan = updater.build_update_plan(str(install), staged, layout="electron")
    assert next(item for item in plan["added"] if item["path"] == member)["sha256"] == hashlib.sha256(b"kept").hexdigest()
    import shutil
    shutil.rmtree(platform_runtime.filesystem_path(root))


def test_extraction_cancellation_removes_only_partial_staging(tmp_path):
    package = tmp_path / "package.zip"
    desktop_zip(package)
    cancelled = threading.Event()
    def progress(message, percent):
        if percent is not None:
            cancelled.set()
    with pytest.raises(updater.UpdateCancelled):
        updater.extract_update_zip(str(package), str(tmp_path / "cache"), progress, layout="electron", cancel_event=cancelled)
    assert package.exists()
    assert list((tmp_path / "cache").iterdir()) == []


def test_public_download_failure_switches_to_pinned_github_zip(tmp_path):
    package = tmp_path / "new.zip"
    manifest = desktop_zip(package, "99.0.0")
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(self.path)
            if self.path == "/public/updates/desktop-windows-x64/latest.json":
                contents = json.dumps(manifest).encode()
            elif self.path == "/github/download/v99.0.0/" + manifest["filename"]:
                contents = package.read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(contents)))
            self.end_headers()
            self.wfile.write(contents)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    events = []
    try:
        result = prepare_online(str(tmp_path / "cache"), APP_VERSION, True, threading.Event(),
            lambda phase, **fields: events.append(phase), retry_delays=(0, 0, 0, 0), layout="electron",
            source_config={"public_base_url": base + "/public", "github_releases_url": base + "/github", "manifest_schema": 1})
    finally:
        server.shutdown(); server.server_close(); thread.join()
    assert result["sha256"] == manifest["sha256"]
    assert result["source_name"] == "github" and "switching" in events
    assert not any("/github/latest" in path for path in seen)
    assert seen[-1] == "/github/download/v99.0.0/" + manifest["filename"]


def test_network_retry_budget_is_shared_and_validation_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def fetch(**options):
        calls.append(options)
        raise updater.UpdateNetworkError("offline")
    monkeypatch.setattr(updater, "fetch_latest_release", fetch)
    with pytest.raises(updater.UpdateNetworkError):
        prepare_online(str(tmp_path), APP_VERSION, True, threading.Event(), lambda *a, **kw: None,
                       retry_delays=(0, 0, 0, 0), layout="electron")
    assert len(calls) == 5
    calls.clear()
    def invalid(**options):
        calls.append(options)
        raise updater.UpdaterError("invalid manifest")
    monkeypatch.setattr(updater, "fetch_latest_release", invalid)
    with pytest.raises(updater.UpdaterError):
        prepare_online(str(tmp_path), APP_VERSION, True, threading.Event(), lambda *a, **kw: None,
                       retry_delays=(0, 0, 0, 0), layout="electron")
    assert len(calls) == 1


def test_enterprise_cache_rechecks_recommendation_and_never_uses_github(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    package = tmp_path / "local.zip"
    desktop_zip(package)
    result = updater.prepare_desktop_local(str(package), str(root), APP_VERSION, None, threading.Event(), None)
    connection = {"platform_id": "company", "platform_url": "https://company.example"}
    result.update(connection=connection, update_source="online", source_name="enterprise")
    result["release"]["tag_name"] = "99.0.0"
    (root / "prepared.json").write_text(json.dumps(result))
    monkeypatch.setattr(updater, "fetch_enterprise_release", lambda *a, **kw: None)
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kw: pytest.fail("enterprise must not switch to public"))
    with pytest.raises(updater.UpdaterError, match="变更或撤回"):
        load_prepared(str(root), APP_VERSION, connection, layout="electron")


def test_prepared_notice_dismissal_restart_and_install_failure_keeps_input_gate_open(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    for name in ("deepseek-cowork.exe", "resources/app.asar", "resources/service/cowork-service.exe", "desktop-release.json"):
        path = install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old fixture")
    monkeypatch.setenv("COWORK_DESKTOP_INSTALL_DIR", str(install))
    monkeypatch.setattr(updater, "update_connection", lambda path: None)
    events = []
    state = SimpleNamespace(lock=threading.RLock(), update_preparing=False, update_requests={})
    service = SimpleNamespace(config=SimpleNamespace(data_dir=str(tmp_path)), state=state,
        store=SimpleNamespace(active_runs=lambda: []), jobs=SimpleNamespace(active={}), publish=events.append, closing=False)
    updates = DesktopUpdates(service)
    package = tmp_path / "same-version.zip"
    desktop_zip(package)
    updates.dispatch("updates.local", {"path": str(package)})
    wait(updates.coordinator)
    snapshot = updates.snapshot()
    assert snapshot["ready"] and snapshot["notice"] and snapshot["reinstall"]
    updates.dispatch("updates.dismiss", {"identity": snapshot["identity"]})
    assert not updates.snapshot()["notice"] and updates.snapshot()["can_install"]
    recovered = DesktopUpdates(service)
    recovered.coordinator.start_automatic()
    wait(recovered.coordinator)
    assert recovered.snapshot()["ready"] and recovered.snapshot()["notice"], recovered.snapshot()
    from core.desktop_settings import DesktopSettings
    service.updates = recovered
    result = DesktopSettings(service).dispatch("settings.action", {"page": "update", "action": "update.install"})
    assert result == {"requested": True} and events[-1]["event"] == "update_install_requested"
    monkeypatch.setattr(updater, "fetch_latest_release", lambda **kw: None)
    recovered.dispatch("updates.check", {})
    wait(recovered.coordinator)
    assert recovered.snapshot()["ready"] and recovered.snapshot()["source"] == "local"
    monkeypatch.setattr(updater, "create_windows_update_script", lambda *a, **kw: (_ for _ in ()).throw(OSError("cannot create installer")))
    with pytest.raises(OSError, match="cannot create"):
        recovered.dispatch("updates.prepare_install", {"host_pid": os.getpid()})
    assert not state.update_preparing
    assert recovered.snapshot()["can_install"]
    updates.close(); recovered.close()
