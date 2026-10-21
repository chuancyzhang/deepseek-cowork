"""Application-owned update preparation. No widget owns a download or retry timer."""
import copy
import json
import os
import threading
import time
import uuid

import markdown
from bs4 import BeautifulSoup
from PySide6.QtCore import QObject, QThread, Signal

from core.app_version import APP_VERSION, is_newer_version
from core import updater as backend
from core.runtime_logs import append_log

RETRY_DELAYS = (15, 30, 60, 120)


def release_notes_text(source):
    # Parsing never executes HTML or downloads resources. Only plain text leaves here.
    soup = BeautifulSoup(markdown.markdown(str(source or ""), extensions=["tables", "fenced_code"]), "html.parser")
    for node in soup.find_all(["img", "picture", "svg", "video", "audio", "source", "iframe", "object", "embed", "script", "style", "link", "meta"]):
        node.decompose()
    for node in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "li", "tr", "pre", "blockquote"]):
        node.insert_before("\n")
        node.insert_after("\n")
    return "\n".join(line.strip() for line in soup.get_text().splitlines() if line.strip())


def atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def update_log_directory(root):
    # Production uses data_dir/updates; explicitly supplied test/cache roots
    # remain isolated instead of writing to the current user's app data.
    root = os.path.normpath(root)
    return os.path.dirname(root) if os.path.basename(root) == "updates" else root


def update_log(root, phase, **fields):
    append_log("app_update", json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "phase": phase, **fields}, ensure_ascii=False), update_log_directory(root))


def load_prepared(root, current_version):
    manifest = os.path.join(root, "prepared.json")
    if not os.path.isfile(manifest):
        return None
    with open(manifest, encoding="utf-8") as handle:
        result = json.load(handle)
    if not is_newer_version(result["release"]["tag_name"], current_version):
        return None
    zip_path = result.get("zip_path", "")
    # The manifest is a cache, never authority to read/delete arbitrary paths.
    expected = backend.expected_asset_path(result["asset"], os.path.join(root, "downloads"))
    if os.path.normcase(os.path.abspath(zip_path)) != os.path.normcase(expected):
        raise backend.UpdaterError("更新缓存路径无效。")
    if not backend.is_cached_asset_valid(result["asset"], zip_path):
        return None
    if result.get("sha256") != backend._hash_file(zip_path):
        return None
    # The ZIP is verified; staging may have been modified since the previous run.
    result["staged_app_dir"] = backend.extract_update_zip(zip_path, target_dir=root)
    return result


def prepare_online(root, current_version, install_enabled, cancel, emit, retry_delays=RETRY_DELAYS, update_id=""):
    """One retry budget across check and download; successful checks do not reset it."""
    result = None
    downloads = os.path.join(root, "downloads")
    for attempt in range(1, len(retry_delays) + 2):
        backend.check_cancel(cancel)
        try:
            if result is None:
                emit("checking", message="正在检查更新…", attempt=attempt)
                release = backend.fetch_latest_release()
                backend.check_cancel(cancel)
                version = str(release.get("tag_name") or release.get("name") or "")
                result = {"release": {"tag_name": version, "body": release.get("body") or "", "html_url": release.get("html_url") or backend.GITHUB_RELEASES_URL},
                          "current_version": current_version, "update_available": is_newer_version(version, current_version),
                          "update_source": "online", "install_enabled": install_enabled, "updates_dir": root}
                if not result["update_available"]:
                    return result
                result["asset"] = backend.select_release_asset(release).as_dict()
                emit("available", message="发现新版本", result=result)
            if not install_enabled:
                return result
            expected = backend.expected_asset_path(result["asset"], downloads)
            identity_path = os.path.join(downloads, "asset.json")
            try:
                with open(identity_path, encoding="utf-8") as handle:
                    identity = json.load(handle)
            except (OSError, ValueError):
                identity = {}
            matches = identity.get("asset") == result["asset"] and backend.is_cached_asset_valid(result["asset"], expected)
            if matches:
                matches = identity.get("sha256") == backend._hash_file(expected)
            backend.cleanup_update_artifacts(downloads, keep_paths=[expected] if matches else [])
            progress = lambda message, percent: emit("downloading", message="正在下载更新…", percent=percent if percent is not None else -1, attempt=attempt)
            zip_path = backend.download_asset(result["asset"], target_dir=downloads, progress_callback=progress, cancel_event=cancel)
            atomic_json(identity_path, {"asset": result["asset"], "sha256": backend._hash_file(zip_path)})
            backend.check_cancel(cancel)
            emit("validating", message="正在准备安装包…", percent=-1)
            staged = backend.extract_update_zip(zip_path, target_dir=root)
            backend.check_cancel(cancel)
            result.update(zip_path=zip_path, staged_app_dir=staged, sha256=backend._hash_file(zip_path))
            atomic_json(os.path.join(root, "prepared.json"), result)
            return result
        except backend.UpdateNetworkError as exc:
            update_log(root, "network_error", update_id=update_id, attempt=attempt, error=str(exc))
            if attempt > len(retry_delays):
                raise
            emit("retrying", message=f"暂时无法连接更新服务，将重试（{attempt}/5）", attempt=attempt, percent=-1)
            if cancel.wait(retry_delays[attempt - 1]):
                raise backend.UpdateCancelled("已取消本次更新。")
    raise AssertionError("retry budget exhausted")


class UpdateWorker(QThread):
    event = Signal(object)

    def __init__(self, operation, root, install_dir, current_version, install_enabled, payload=None, parent=None):
        super().__init__(parent)
        self.operation, self.root, self.install_dir = operation, root, install_dir
        self.current_version, self.install_enabled = current_version, install_enabled
        self.payload = payload
        self.cancel = threading.Event()
        self.result = None
        self.error = None
        self.update_id = uuid.uuid4().hex
        self._last_phase = ""

    def emit_state(self, phase, **fields):
        if phase != self._last_phase:
            update_log(self.root, phase, update_id=self.update_id, attempt=fields.get("attempt"))
            self._last_phase = phase
        self.event.emit({"phase": phase, **fields})

    def run(self):
        try:
            update_log(self.root, "start", update_id=self.update_id, operation=self.operation)
            if self.operation == "online":
                try:
                    cached = load_prepared(self.root, self.current_version)
                    if cached:
                        self.emit_state("ready", message="更新已准备好", result=cached)
                except (ValueError, KeyError, OSError, backend.UpdaterError) as exc:
                    update_log(self.root, "cache_invalid", error=str(exc))
                self.result = prepare_online(self.root, self.current_version, self.install_enabled, self.cancel, self.emit_state, update_id=self.update_id)
            elif self.operation == "local":
                path = self.payload
                version = backend.local_update_package_version(path)
                if backend.compare_versions(version, self.current_version) < 0:
                    raise backend.UpdaterError("安装包版本低于当前版本，不能降级安装。")
                self.emit_state("validating", message="正在验证本地安装包…", percent=-1)
                staged = backend.extract_update_zip(path, target_dir=self.root)
                backend.check_cancel(self.cancel)
                self.result = {"update_source": "local", "update_available": True, "install_enabled": True,
                               "release": {"tag_name": version, "body": ""}, "zip_path": path,
                               "staged_app_dir": staged, "updates_dir": self.root}
            elif self.operation == "plan":
                self.emit_state("planning", message="正在准备安装…", percent=-1)
                self.result = copy.deepcopy(self.payload)
                backend.check_cancel(self.cancel)
                plan = backend.build_update_plan(self.install_dir, self.result["staged_app_dir"])
                backend.check_cancel(self.cancel)
                self.result.update(change_plan=plan, change_summary=plan["summary"], change_plan_path=backend.write_update_plan(plan, self.root))
            self.result["update_id"] = self.update_id
            update_log(self.root, "prepared", update_id=self.update_id, operation=self.operation)
        except Exception as exc:
            self.error = exc
            update_log(self.root, "cancelled" if isinstance(exc, backend.UpdateCancelled) else "error", update_id=self.update_id, operation=self.operation, error=str(exc))


class UpdateCoordinator(QObject):
    changed = Signal(object)
    installation_ready = Signal(object)
    installation_cancel_requested = Signal()

    def __init__(self, install_dir, install_enabled, current_version=APP_VERSION, root=None, parent=None):
        super().__init__(parent)
        self.install_dir, self.install_enabled = install_dir, install_enabled
        self.current_version = current_version
        self.root = root or backend.updates_dir()
        self.worker = None
        self.prepared = None
        self.pending = None
        self.install_requested = False
        self.automatic_started = False
        self.state = {"phase": "idle", "message": "检查是否有可用更新", "percent": -1, "result": {}, "error": ""}

    def snapshot(self):
        return copy.deepcopy({**self.state, "busy": self.worker is not None, "install_requested": self.install_requested})

    def publish(self, **fields):
        self.state.update(fields)
        self.changed.emit(self.snapshot())

    def start_automatic(self):
        if self.automatic_started:
            return
        self.automatic_started = True
        if self.enterprise_download_url():
            self.publish(message="企业客户端请从企业平台下载更新，再选择本地安装包。")
            return
        if self.worker is None:
            self.check()

    def check(self):
        self.automatic_started = True
        enterprise_url = self.enterprise_download_url()
        if getattr(self, "enterprise_connection_error", ""):
            self.publish(phase="error", message=self.enterprise_connection_error, error=self.enterprise_connection_error)
            return
        if enterprise_url:
            self.publish(message="请在企业平台下载更新，再选择本地安装包。")
            return
        if self.worker is None:
            self._start("online")

    def enterprise_download_url(self):
        from core.enterprise_connection import read_connection
        from core.enterprise_client import EnterpriseStore
        import sqlite3
        self.enterprise_connection_error = ""
        try:
            connection = EnterpriseStore().read().get("connection") or read_connection(self.install_dir)
            return connection["platform_url"] + "/downloads" if connection else ""
        except (ValueError, OSError, sqlite3.DatabaseError, TypeError, KeyError):
            self.enterprise_connection_error = "企业连接文件或本机状态无法读取，请在企业账号中检查后再更新。"
            return ""

    def _start(self, operation, payload=None):
        self.worker = UpdateWorker(operation, self.root, self.install_dir, self.current_version, self.install_enabled, payload, self)
        self.worker.event.connect(self._event)
        self.worker.finished.connect(self._finished)
        self.publish(error="", source="local" if operation == "local" else "online" if operation == "online" else self.state.get("source", "online"))
        self.worker.start()

    def _event(self, event):
        if self.pending or (self.worker and self.worker.cancel.is_set()):
            return
        if event.get("phase") == "ready":
            self.prepared = event["result"]
        elif event.get("phase") == "available" and self.prepared:
            if self.prepared.get("asset") != event["result"].get("asset"):
                self.prepared = None
        # A valid cached package remains immediately installable while a fresh check runs.
        if self.prepared and event.get("phase") == "checking":
            return
        self.publish(**event)

    def request_install(self):
        if not self.install_enabled or self.state["phase"] == "installing" or self.install_requested:
            return
        self.install_requested = True
        if self.prepared:
            if self.worker:
                self.pending = ("plan", self.prepared)
                self.worker.cancel.set()
            else:
                self._start("plan", self.prepared)
        elif self.worker is None:
            self.check()
        self.publish()

    def select_local(self, path):
        if not self.install_enabled or self.state["phase"] == "installing":
            return
        self.automatic_started = True
        self.install_requested = False
        self.prepared = None
        self.publish(result={}, error="")
        if self.worker:
            self.pending = ("local", os.path.abspath(path))
            self.worker.cancel.set()
            self.publish(message="正在结束下载，随后验证本地安装包…")
        else:
            self._start("local", os.path.abspath(path))

    def cancel(self):
        if self.state.get("phase") == "installing":
            if self.state.get("can_cancel_install"):
                self.installation_cancel_requested.emit()
            return
        self.install_requested = False
        self.pending = None
        if self.worker:
            self.worker.cancel.set()
        else:
            self.publish(phase="ready" if self.prepared else "idle", message="更新已准备好" if self.prepared else "已取消本次更新")

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self.pending:
            operation, payload = self.pending
            self.pending = None
            self._start(operation, payload)
            return
        if worker.cancel.is_set():
            worker.error = backend.UpdateCancelled("已取消本次更新。")
        if worker.error:
            if isinstance(worker.error, backend.UpdateCancelled):
                self.publish(phase="ready" if self.prepared else "idle", message="更新已准备好" if self.prepared else "已取消本次更新", percent=-1)
                return
            self.install_requested = False
            message = "暂时无法下载更新，可重试或选择本地安装包" if isinstance(worker.error, backend.UpdateNetworkError) else str(worker.error)
            if self.prepared:
                message = "更新已准备好，可直接安装；暂时无法检查其他更新"
            self.publish(phase="ready" if self.prepared else "error", message=message, error=str(worker.error), percent=-1)
            return
        result = worker.result
        if result.get("staged_app_dir"):
            self.prepared = result
            self.publish(phase="ready", message="更新已准备好", result=result, percent=100)
            if worker.operation == "plan":
                self.install_requested = False
                self.publish(phase="installing", message="正在准备关闭应用…")
                self.installation_ready.emit(copy.deepcopy(result))
            elif self.install_requested:
                self._start("plan", result)
        else:
            self.prepared = None
            self.install_requested = False
            self.publish(phase="available" if result["update_available"] else "current", message="发现新版本" if result["update_available"] else "当前已是最新版本", result=result, percent=-1)

    def install_aborted(self, message="更新已准备好，尚未安装"):
        self.install_requested = False
        self.publish(phase="ready", message=message, percent=-1, can_cancel_install=False)
