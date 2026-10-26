"""Real HTTP downloads against an isolated release server, never public services."""
import hashlib
import io
import json
import threading
import time
import zipfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import updater
from core.update_coordinator import prepare_online, load_prepared, UpdateWorker
from core.update_manifest import validate_manifest


@pytest.fixture
def release_server(monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("deepseek-cowork/deepseek-cowork.exe", b"fixture")
        bundle.writestr("deepseek-cowork/_internal/data.txt", b"new")
    package = archive.getvalue()
    manifest = {"schema_version": 1, "version": "9.0.0", "target_os": "windows", "arch": "x64",
                "filename": "deepseek-cowork-v9.0.0.zip", "size": len(package),
                "sha256": hashlib.sha256(package).hexdigest(), "notes": "更新说明"}
    paths = {"public_manifest": "/updates/windows-x64/latest.json", "public_zip": "/updates/windows-x64/" + manifest["filename"],
             "github_manifest": "/releases/latest/download/latest.json", "github_zip": "/releases/download/v9.0.0/" + manifest["filename"],
             "api": "/api/releases/latest"}
    routes = {paths["public_manifest"]: manifest, paths["github_manifest"]: manifest,
              paths["public_zip"]: package, paths["github_zip"]: package}
    calls = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls[self.path] += 1
            payload = routes.get(self.path, 404)
            headers = {}
            if isinstance(payload, tuple):
                status, payload, headers = payload
            else:
                status = payload if isinstance(payload, int) else 200
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(updater, "GITHUB_RELEASES_URL", base + "/releases")
    monkeypatch.setattr(updater, "GITHUB_MANIFEST_URL", base + paths["github_manifest"])
    monkeypatch.setattr(updater, "GITHUB_LATEST_RELEASE_URL", base + paths["api"])
    monkeypatch.setattr(updater, "PUBLIC_UPDATE_BASE_URL", "")
    monkeypatch.setattr(updater, "_github_api_retry_at", 0)
    try:
        yield base, paths, routes, calls, manifest
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def prepare(tmp_path, config=None, cancel=None, emit=None):
    return prepare_online(str(tmp_path), "5.3.2", True, cancel or threading.Event(), emit or (lambda *a, **k: None),
                          retry_delays=(0, 0, 0, 0), source_config=config)


def public_config(server):
    return {**updater.community_source_config(), "public_base_url": server[0]}


def test_empty_public_address_uses_github_static_and_pins_zip_version(tmp_path, release_server):
    _, paths, _, calls, manifest = release_server
    result = prepare(tmp_path)
    assert result["source_name"] == "github"
    assert result["sha256"] == manifest["sha256"]
    assert calls == {paths["github_manifest"]: 1, paths["github_zip"]: 1}
    assert load_prepared(str(tmp_path), "5.3.2")["asset"] == result["asset"]


def test_public_success_and_current_version_never_query_github(tmp_path, release_server):
    _, paths, _, calls, _ = release_server
    config = public_config(release_server)
    result = prepare(tmp_path, config)
    assert result["source_name"] == "public"
    assert calls == {paths["public_manifest"]: 1, paths["public_zip"]: 1}
    current = prepare_online(str(tmp_path), "9.0.0", True, threading.Event(), lambda *a, **k: None, source_config=config)
    assert not current["update_available"]
    assert calls[paths["github_manifest"]] == 0
    assert load_prepared(str(tmp_path), "5.3.2", source_config=config)
    assert load_prepared(str(tmp_path), "5.3.2") is None


@pytest.mark.parametrize("failure", ["public_manifest", "public_zip"])
def test_public_failure_switches_once_and_keeps_selected_digest(tmp_path, release_server, failure):
    _, paths, routes, calls, manifest = release_server
    routes[paths[failure]] = 503
    # A newer GitHub recommendation must never replace the public version mid-download.
    if failure == "public_zip":
        routes[paths["github_manifest"]] = {**manifest, "version": "10.0.0", "filename": "deepseek-cowork-v10.0.0.zip"}
    events = []
    result = prepare(tmp_path, public_config(release_server), emit=lambda phase, **fields: events.append((phase, fields)))
    assert result["source_name"] == "github"
    assert result["fallback_reason"]
    assert result["sha256"] == manifest["sha256"]
    assert result["release"]["tag_name"] == "9.0.0"
    assert calls[paths[failure]] == 1
    assert calls[paths["github_zip"]] == 1
    assert calls[paths["github_manifest"]] == (failure == "public_manifest")
    assert sum(phase == "switching" for phase, _ in events) == 1


def test_mismatched_fallback_package_is_rejected(tmp_path, release_server):
    _, paths, routes, calls, _ = release_server
    routes[paths["public_zip"]] = 503
    original = routes[paths["github_zip"]]
    routes[paths["github_zip"]] = b"x" * len(original)
    with pytest.raises(updater.UpdaterError, match="校验失败"):
        prepare(tmp_path, public_config(release_server))
    assert calls[paths["github_zip"]] == 1
    assert not (tmp_path / "prepared.json").exists()


def test_same_package_downloaded_from_fallback_is_reused_on_next_public_check(tmp_path, release_server):
    _, paths, routes, calls, _ = release_server
    routes[paths["public_zip"]] = 503
    config = public_config(release_server)
    first = prepare(tmp_path, config)
    second = prepare(tmp_path, config)
    assert second["sha256"] == first["sha256"]
    assert second["source_name"] == "github"
    assert second["cache_reused"]
    assert calls[paths["public_zip"]] == calls[paths["github_zip"]] == 1


@pytest.mark.parametrize("failure", ["invalid_manifest", "corrupt_zip"])
def test_validation_failure_never_switches_source(tmp_path, release_server, failure):
    _, paths, routes, calls, manifest = release_server
    if failure == "invalid_manifest":
        routes[paths["public_manifest"]] = {**manifest, "sha256": "missing"}
    else:
        routes[paths["public_zip"]] = b"x" * manifest["size"]
    with pytest.raises(updater.UpdaterError):
        prepare(tmp_path, public_config(release_server))
    assert calls[paths["github_manifest"]] == calls[paths["github_zip"]] == 0


def test_five_attempt_budget_includes_switch_and_download(tmp_path, release_server):
    _, paths, routes, calls, _ = release_server
    routes[paths["public_manifest"]] = routes[paths["github_zip"]] = 503
    with pytest.raises(updater.UpdateNetworkError):
        prepare(tmp_path, public_config(release_server))
    assert calls == {paths["public_manifest"]: 1, paths["github_manifest"]: 1, paths["github_zip"]: 4}


def test_cancel_during_switch_does_not_contact_github(tmp_path, release_server):
    _, paths, routes, calls, _ = release_server
    routes[paths["public_manifest"]] = 503
    cancel = threading.Event()
    def emit(phase, **_):
        if phase == "switching":
            cancel.set()
    with pytest.raises(updater.UpdateCancelled):
        prepare(tmp_path, public_config(release_server), cancel, emit)
    assert calls == {paths["public_manifest"]: 1}


def test_only_missing_github_manifest_uses_legacy_api(release_server):
    base, paths, routes, calls, _ = release_server
    routes[paths["github_manifest"]] = 404
    routes[paths["api"]] = {"tag_name": "v8.0.0", "assets": []}
    result = updater.fetch_latest_release()
    assert result["tag_name"] == "v8.0.0"
    assert calls[paths["api"]] == 1
    for failure in (503, b"not json", {"schema_version": 2}):
        routes[paths["github_manifest"]] = failure
        with pytest.raises(updater.UpdaterError):
            updater.fetch_latest_release()
    assert calls[paths["api"]] == 1


def test_github_rate_limit_retains_cooldown_and_cancelable_wait(tmp_path, release_server):
    _, paths, routes, calls, _ = release_server
    routes[paths["github_manifest"]] = 404
    routes[paths["api"]] = (403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": time.time() + 120})
    with pytest.raises(updater.UpdateNetworkError) as caught:
        updater.fetch_latest_release()
    assert caught.value.retry_after > 110
    with pytest.raises(updater.UpdateNetworkError):
        updater.fetch_latest_release()
    assert calls[paths["api"]] == 1
    class CancelWait:
        def is_set(self):
            return False
        def wait(self, seconds):
            assert seconds > 110
            return True
    with pytest.raises(updater.UpdateCancelled):
        prepare(tmp_path, cancel=CancelWait())
    assert calls[paths["api"]] == 1


@pytest.mark.parametrize("field,value", [("schema_version", True), ("schema_version", 2), ("version", "9.0.0-beta"),
    ("target_os", "linux"), ("arch", "arm64"), ("filename", "../app.zip"), ("size", True), ("size", 0), ("sha256", "123"), ("notes", [])])
def test_manifest_contract_rejects_invalid_fields(release_server, field, value):
    with pytest.raises(ValueError):
        validate_manifest({**release_server[4], field: value})


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://example.com?a=1", "https://example.com/#x", "https://example.com:invalid"])
def test_public_configuration_rejects_unsafe_or_invalid_addresses(monkeypatch, url):
    monkeypatch.setattr(updater, "PUBLIC_UPDATE_BASE_URL", url)
    with pytest.raises(updater.UpdaterError):
        updater.community_source_config()


def test_install_rejects_source_changed_after_preparation(tmp_path, release_server, monkeypatch):
    result = prepare(tmp_path)
    monkeypatch.setattr(updater, "PUBLIC_UPDATE_BASE_URL", "https://new.example.com")
    worker = UpdateWorker("plan", str(tmp_path), str(tmp_path), "5.3.2", True, result)
    worker.run()
    assert "来源已改变" in str(worker.error)
