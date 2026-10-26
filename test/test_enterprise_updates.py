"""Enterprise and community update routing, using real HTTP and synthetic ZIPs."""
import hashlib
import io
import json
import os
import threading
import time
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from core import updater
from core.update_coordinator import UpdateCoordinator, UpdateWorker, load_prepared, prepare_online, update_log_directory
from ui.update_panel import UpdatePanel


CONNECTION = {"schema_version": 1, "config_version": 1, "platform_id": "company",
              "platform_name": "示例企业", "platform_url": "https://company.invalid/cowork",
              "distribution_channel": "enterprise"}


def package(connection=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("deepseek-cowork/deepseek-cowork.exe", "new exe")
        archive.writestr("deepseek-cowork/_internal/data.txt", "data")
        if connection:
            archive.writestr("deepseek-cowork/enterprise_connection.json", json.dumps(connection))
    return data.getvalue()


def installed(path, connection=None):
    path.mkdir(exist_ok=True)
    (path / "_internal").mkdir(exist_ok=True)
    (path / "deepseek-cowork.exe").write_text("old exe")
    if connection:
        (path / "enterprise_connection.json").write_text(json.dumps(connection))
    return str(path)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@contextmanager
def platform():
    state = {"requests": [], "info": {"platform_id": "company", "supported_api_versions": [1]}}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            status = state.get("status", 200)
            if self.path.endswith("/platform-info"):
                data = json.dumps(state["info"]).encode()
            elif "/recommended?" in self.path:
                data = json.dumps({"release": state["release"]}).encode()
            else:
                data = state["zip"]
                status = state.get("download_status", status)
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    connection = {**CONNECTION, "platform_url": f"http://127.0.0.1:{server.server_port}/cowork"}
    state["zip"] = package(connection)
    state["release"] = {"id": "release-9", "state": "published", "recommended": True,
                        "app_version": "9.0.0", "target_os": "windows", "arch": "x64",
                        "notes": "企业发布说明", "bytes": len(state["zip"]),
                        "sha256": hashlib.sha256(state["zip"]).hexdigest()}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield connection, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def prepare(root, connection):
    return prepare_online(str(root), "5.3.0", True, threading.Event(), lambda *a, **k: None,
                          connection=connection, retry_delays=())


def test_http_enterprise_download_and_install_plan_bypass_loopback_proxy(tmp_path, monkeypatch, app):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with platform() as (connection, state), patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        install = installed(tmp_path / "install", connection)
        with patch.object(updater, "fetch_latest_release", side_effect=AssertionError("GitHub must not be used")):
            result = prepare(tmp_path / "updates", connection)
        assert Path(result["zip_path"]).read_bytes() == state["zip"]
        assert result["release"]["body"] == "企业发布说明"
        assert result["connection"] == connection
        assert state["requests"] == ["/cowork/api/v1/platform-info",
                                     "/cowork/api/v1/client-releases/recommended?target_os=windows&arch=x64",
                                     "/cowork/api/v1/client-releases/release-9/download"]
        worker = UpdateWorker("plan", str(tmp_path / "updates"), install, "5.3.0", True, result, connection=connection)
        worker.run()
        assert worker.error is None
        assert worker.result["change_summary"]["modified"] == 1
        assert Path(worker.result["change_plan_path"]).is_file()
        assert load_prepared(str(tmp_path / "updates"), "5.3.0") is None
        assert load_prepared(str(tmp_path / "updates"), "5.3.0", {**connection, "platform_id": "other"}) is None
        assert load_prepared(str(tmp_path / "updates"), "5.3.0", connection)
        # Recheck recommendation on restart, but reuse the verified matching ZIP.
        state["requests"].clear()
        prepare(tmp_path / "updates", connection)
        assert len(state["requests"]) == 2


@pytest.mark.parametrize("field,value", [("state", "withdrawn"), ("recommended", False), ("arch", "arm64"),
                                       ("sha256", "bad"), ("bytes", 0), ("id", "../other"), ("app_version", "next")])
def test_invalid_recommendation_never_downloads(tmp_path, field, value):
    with platform() as (connection, state):
        state["release"][field] = value
        with pytest.raises(updater.UpdaterError, match="企业推荐版本"):
            prepare(tmp_path, connection)
        assert len(state["requests"]) == 2


@pytest.mark.parametrize("info,message", [({"platform_id": "other", "supported_api_versions": [1]}, "平台标识"),
                                         ({"platform_id": "company", "supported_api_versions": [2]}, "协议不兼容")])
def test_platform_identity_and_protocol_fail_closed(tmp_path, info, message):
    with platform() as (connection, state), patch.object(updater, "fetch_latest_release") as github:
        state["info"] = info
        with pytest.raises(updater.UpdaterError, match=message):
            prepare(tmp_path, connection)
        assert len(state["requests"]) == 1
        github.assert_not_called()


def test_no_recommendation_and_no_newer_version_do_not_download(tmp_path):
    with platform() as (connection, state):
        state["release"]["app_version"] = "5.3.0"
        assert not prepare(tmp_path, connection)["update_available"]
        state["release"] = None
        result = prepare(tmp_path, connection)
        assert not result["update_available"]
        assert "暂无推荐版本" in result["message"]
        assert all(not path.endswith("/download") for path in state["requests"])


@pytest.mark.parametrize("failure", ["digest", "withdrawn", "redirect", "foreign_package"])
def test_invalid_download_cannot_be_prepared(tmp_path, failure):
    with platform() as (connection, state):
        if failure == "digest":
            state["release"]["sha256"] = "0" * 64
        elif failure == "foreign_package":
            state["zip"] = package({**connection, "platform_id": "other"})
            state["release"].update(bytes=len(state["zip"]), sha256=hashlib.sha256(state["zip"]).hexdigest())
        else:
            state["download_status"] = 410 if failure == "withdrawn" else 302
        with pytest.raises((updater.UpdaterError, ValueError)):
            prepare(tmp_path, connection)
        assert not (tmp_path / "prepared.json").exists()
        assert not list(tmp_path.rglob("*.download"))


def test_community_distribution_ignores_previous_enterprise_login(tmp_path, app):
    install = installed(tmp_path / "install")
    staged = installed(tmp_path / "staged")
    with patch("core.enterprise_client.EnterpriseStore.read", side_effect=AssertionError("community must not read enterprise state")):
        coordinator = UpdateCoordinator(install, True, root=str(tmp_path / "updates"))
        with patch.object(coordinator, "_start") as start:
            coordinator.check()
        assert coordinator.connection is None
        assert coordinator.cache_root == coordinator.root
        start.assert_called_once_with("online")
        updater.build_update_plan(install, staged)


def test_enterprise_source_cache_isolation_and_confirmed_connection(tmp_path, app):
    install = installed(tmp_path / "install", CONNECTION)
    coordinator = UpdateCoordinator(install, True, root=str(tmp_path / "updates"))
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={}), patch.object(coordinator, "_start"):
        coordinator.start_automatic()
        first_root = coordinator.cache_root
        assert coordinator.connection == CONNECTION
        assert first_root != coordinator.root
        assert coordinator.root == str(tmp_path / "updates")
        assert update_log_directory(first_root) == str(tmp_path)
    other = {**CONNECTION, "platform_id": "other", "platform_url": "https://other.invalid"}
    coordinator.prepared = {"staged_app_dir": "stale"}
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={"connection": other}), patch.object(coordinator, "_start"):
        coordinator.check()
    assert coordinator.connection == other
    assert coordinator.prepared is None
    assert coordinator.cache_root != first_root
    assert coordinator.root == str(tmp_path / "updates")


def test_enterprise_restart_acknowledges_installer_at_stable_root(tmp_path, monkeypatch, app):
    from ui.update_lifecycle import UpdateLifecycleMixin
    install = installed(tmp_path / "install", CONNECTION)
    root = str(tmp_path / "updates")
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        coordinator = UpdateCoordinator(install, True, root=root)
        with patch.object(coordinator, "_start"):
            coordinator.check()
        identity = "b" * 32
        receipt = Path(coordinator.root, "transactions", identity, "opened.json")
        # A package can raise config_version; restart must still acknowledge the
        # same transaction, independently of the new cache identity.
        installed(Path(install), {**CONNECTION, "config_version": 2})
        restarted = UpdateCoordinator(install, True, root=root)
        with patch.object(restarted, "_start"):
            restarted.check()
        assert restarted.cache_root != coordinator.cache_root
        monkeypatch.setenv("COWORK_UPDATE_ID", identity)
        monkeypatch.setenv("COWORK_UPDATE_RECEIPT", str(receipt))
        UpdateLifecycleMixin.acknowledge_update_startup(SimpleNamespace(update_coordinator=restarted))
        assert json.loads(receipt.read_text())["id"] == identity


def test_account_change_before_install_requires_new_check(tmp_path, app):
    install = installed(tmp_path / "install", CONNECTION)
    payload = {"update_source": "online", "connection": CONNECTION, "staged_app_dir": "unused"}
    other = {**CONNECTION, "platform_id": "other"}
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={"connection": other}), patch.object(updater, "build_update_plan") as plan:
        worker = UpdateWorker("plan", str(tmp_path), install, "5.3.0", True, payload, connection=CONNECTION)
        worker.run()
        assert "更新来源已改变" in str(worker.error)
        plan.assert_not_called()
        coordinator = UpdateCoordinator(install, True, root=str(tmp_path))
        coordinator.connection = CONNECTION
        coordinator.prepared = payload
        coordinator.worker = SimpleNamespace(operation="plan", cancel=threading.Event(), error=worker.error, deleteLater=lambda: None)
        coordinator._finished()
        assert coordinator.prepared is None
        assert coordinator.state["phase"] == "error"
        assert "更新来源已改变" in coordinator.state["message"]


def test_enterprise_button_waits_for_download_then_emits_install_and_keeps_local(tmp_path, app):
    with platform() as (connection, state), patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        install = installed(tmp_path / "install", connection)
        coordinator = UpdateCoordinator(install, True, root=str(tmp_path / "updates"))
        panel = UpdatePanel(coordinator)
        installation = []
        coordinator.installation_ready.connect(installation.append)
        coordinator.check()
        # The click expresses install intent while downloading; no browser is involved.
        coordinator.publish(phase="downloading", result={"update_available": True, "release": {"tag_name": "9.0.0"}})
        with patch("ui.update_panel.QDesktopServices.openUrl") as browser:
            panel.act()
            deadline = time.monotonic() + 5
            while coordinator.worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert coordinator.worker is None
            browser.assert_not_called()
        assert len(installation) == 1
        assert installation[0]["change_plan"]
        coordinator.install_aborted()
        assert not panel.local.isHidden()
        assert panel.local.isEnabled()
        assert panel.primary.text() == "重启并更新"
        panel.close()
        panel.deleteLater()


def test_local_enterprise_zip_needs_no_network(tmp_path, app):
    install = installed(tmp_path / "install", CONNECTION)
    path = tmp_path / "deepseek-cowork-v9.0.0.zip"
    path.write_bytes(package(CONNECTION))
    with patch.object(updater.requests, "get", side_effect=AssertionError("unexpected network")), patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        worker = UpdateWorker("local", str(tmp_path / "updates"), install, "5.3.0", True, str(path))
        worker.run()
        assert worker.error is None
        plan = UpdateWorker("plan", str(tmp_path / "updates"), install, "5.3.0", True, worker.result)
        plan.run()
        assert plan.error is None


def test_enterprise_outage_has_bounded_retry_without_github_fallback(tmp_path):
    with patch.object(updater, "fetch_enterprise_release", side_effect=updater.UpdateNetworkError("offline")) as fetch, patch.object(updater, "fetch_latest_release") as github:
        with pytest.raises(updater.UpdateNetworkError):
            prepare_online(str(tmp_path), "5.3.0", True, threading.Event(), lambda *a, **k: None,
                           connection=CONNECTION, retry_delays=(0, 0, 0, 0))
        assert fetch.call_count == 5
        github.assert_not_called()


def test_community_online_package_cannot_switch_to_enterprise(tmp_path):
    staged = installed(tmp_path / "staged", CONNECTION)
    with pytest.raises(updater.UpdaterError, match="社区更新服务返回了企业"):
        updater.validate_online_package(staged, None)


@pytest.mark.parametrize("candidate", [None, {**CONNECTION, "platform_url": "https://other.invalid"},
                                       {**CONNECTION, "platform_name": "changed"}])
def test_enterprise_local_package_still_enforces_connection(tmp_path, candidate):
    install = installed(tmp_path / "install", CONNECTION)
    staged = installed(tmp_path / "staged", candidate)
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        with pytest.raises(updater.UpdaterError):
            updater.build_update_plan(install, staged)
