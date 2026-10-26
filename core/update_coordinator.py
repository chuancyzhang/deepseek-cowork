"""Application-owned update preparation. No widget owns a download or retry timer."""
import copy
import hashlib
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
    # Only package caches are channel-scoped; logs and installer transactions
    # stay at the stable application root, including after an app restart.
    if os.path.basename(os.path.dirname(root)) == "channels" and len(os.path.basename(root)) == 64:
        root = os.path.dirname(os.path.dirname(root))
    return os.path.dirname(root) if os.path.basename(root) == "updates" else root


def update_log(root, phase, **fields):
    append_log("app_update", json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "phase": phase, **fields}, ensure_ascii=False), update_log_directory(root))


def load_prepared(root, current_version, connection=None, source_config=None):
    manifest = os.path.join(root, "prepared.json")
    if not os.path.isfile(manifest):
        return None
    with open(manifest, encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise backend.UpdaterError("更新缓存清单格式无效。")
    if result.get("connection") != connection:
        return None
    if connection is None and result.get("source_config") != (source_config if source_config is not None else backend.community_source_config()):
        return None
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
    backend.validate_online_package(result["staged_app_dir"], connection)
    result["cache_reused"] = True
    return result


def prepare_online(root, current_version, install_enabled, cancel, emit, retry_delays=RETRY_DELAYS, update_id="", connection=None, source_config=None):
    """One retry budget across check and download; successful checks do not reset it."""
    result = None
    config = None if connection else (source_config if source_config is not None else backend.community_source_config())
    source = "enterprise" if connection else "public" if config["public_base_url"] else "github"
    fallback_reason = ""
    downloads = os.path.join(root, "downloads")
    for attempt in range(1, len(retry_delays) + 2):
        backend.check_cancel(cancel)
        try:
            if result is None:
                emit("checking", message="正在检查更新…", attempt=attempt, source_name=source, fallback_reason=fallback_reason)
                release = backend.fetch_enterprise_release(connection) if connection else backend.fetch_latest_release(source=source, source_config=config)
                backend.check_cancel(cancel)
                if release is None:
                    return {"release": {}, "current_version": current_version, "update_available": False,
                            "update_source": "online", "connection": connection, "install_enabled": install_enabled,
                            "updates_dir": root, "source_name": source, "message": "企业平台暂无推荐版本，请联系管理员发布并推荐客户端。"}
                version = str(release.get("tag_name") or release.get("name") or "")
                result = {"release": {"tag_name": version, "body": release.get("body") or "", "html_url": release.get("html_url") or backend.GITHUB_RELEASES_URL},
                          "current_version": current_version, "update_available": is_newer_version(version, current_version),
                          "update_source": "online", "connection": connection, "install_enabled": install_enabled, "updates_dir": root,
                          "source_config": config, "source_name": source, "fallback_reason": fallback_reason}
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
            if not isinstance(identity, dict):
                identity = {}
            matches = backend.same_release_asset(identity.get("asset") or {}, result["asset"]) and backend.is_cached_asset_valid(result["asset"], expected)
            if matches:
                matches = identity.get("sha256") == backend._hash_file(expected)
            result["cache_reused"] = matches
            if matches:
                result["source_name"] = identity.get("source_name") or source
                result["fallback_reason"] = identity.get("fallback_reason") or ""
            backend.cleanup_update_artifacts(downloads, keep_paths=[expected] if matches else [])
            progress = lambda message, percent: emit("downloading", message="正在下载更新…", percent=percent if percent is not None else -1, attempt=attempt)
            zip_path = backend.download_asset(result["asset"], target_dir=downloads, progress_callback=progress, cancel_event=cancel,
                                              **({"connection": connection} if connection else {"public_source": True} if source == "public" else {}))
            atomic_json(identity_path, {"asset": result["asset"], "sha256": backend._hash_file(zip_path),
                                        "source_name": result["source_name"], "fallback_reason": result["fallback_reason"]})
            backend.check_cancel(cancel)
            emit("validating", message="正在准备安装包…", percent=-1)
            staged = backend.extract_update_zip(zip_path, target_dir=root)
            backend.validate_online_package(staged, connection)
            backend.check_cancel(cancel)
            result.update(zip_path=zip_path, staged_app_dir=staged, sha256=backend._hash_file(zip_path))
            atomic_json(os.path.join(root, "prepared.json"), result)
            return result
        except backend.UpdateNetworkError as exc:
            update_log(root, "network_error", update_id=update_id, attempt=attempt, source=source, error=str(exc))
            if attempt > len(retry_delays):
                raise
            if source == "public":
                source = "github"
                fallback_reason = str(exc)
                if result:
                    # Keep the already selected version, size and digest. Only transport changes.
                    result["asset"]["browser_download_url"] = backend.github_asset_url(result["release"]["tag_name"], result["asset"]["name"])
                    result.update(source_name=source, fallback_reason=fallback_reason)
                update_log(root, "source_switched", update_id=update_id, source=source, reason=fallback_reason)
                emit("switching", message="公共更新源暂时不可用，正在改用 GitHub…", source_name=source,
                     fallback_reason=fallback_reason, result=result or {}, percent=-1)
                continue
            delay = max(retry_delays[attempt - 1], exc.retry_after)
            message = f"更新服务请求过于频繁，{int(delay) + 1} 秒后重试（{attempt}/5）" if exc.retry_after else f"暂时无法连接更新服务，将重试（{attempt}/5）"
            emit("retrying", message=message, attempt=attempt, percent=-1)
            if cancel.wait(delay):
                raise backend.UpdateCancelled("已取消本次更新。")
    raise AssertionError("retry budget exhausted")


class UpdateWorker(QThread):
    event = Signal(object)

    def __init__(self, operation, root, install_dir, current_version, install_enabled, payload=None, parent=None, connection=None, source_config=None):
        super().__init__(parent)
        self.operation, self.root, self.install_dir = operation, root, install_dir
        self.current_version, self.install_enabled = current_version, install_enabled
        self.payload = payload
        self.connection = copy.deepcopy(connection)
        self.source_config = copy.deepcopy(source_config)
        self.cancel = threading.Event()
        self.result = None
        self.error = None
        self.update_id = uuid.uuid4().hex
        self._last_phase = ""

    def emit_state(self, phase, **fields):
        if phase != self._last_phase:
            update_log(self.root, phase, update_id=self.update_id, attempt=fields.get("attempt"))
            self._last_phase = phase
        self.event.emit(copy.deepcopy({"phase": phase, **fields}))

    def run(self):
        try:
            update_log(self.root, "start", update_id=self.update_id, operation=self.operation,
                       channel="enterprise" if self.connection else "community",
                       platform_id=(self.connection or {}).get("platform_id"))
            if self.operation == "online":
                try:
                    # Enterprise caches are reused only after checking the current recommendation.
                    cached = None if self.connection else load_prepared(self.root, self.current_version, source_config=self.source_config)
                    if cached:
                        self.emit_state("ready", message="更新已准备好", result=cached)
                except (ValueError, KeyError, OSError, backend.UpdaterError) as exc:
                    update_log(self.root, "cache_invalid", error=str(exc))
                self.result = prepare_online(self.root, self.current_version, self.install_enabled, self.cancel, self.emit_state,
                                             update_id=self.update_id, connection=self.connection, source_config=self.source_config)
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
                if (self.result.get("update_source") == "online"
                        and self.result.get("connection") != backend.update_connection(self.install_dir)):
                    raise backend.UpdaterError("更新来源已改变，请重新检查更新。")
                if self.result.get("update_source") == "online":
                    if not self.connection and self.result.get("source_config") != backend.community_source_config():
                        raise backend.UpdaterError("更新来源已改变，请重新检查更新。")
                    backend.validate_online_package(self.result["staged_app_dir"], self.result.get("connection"))
                plan = backend.build_update_plan(self.install_dir, self.result["staged_app_dir"])
                backend.check_cancel(self.cancel)
                self.result.update(change_plan=plan, change_summary=plan["summary"], change_plan_path=backend.write_update_plan(plan, self.root))
                self.result["updates_dir"] = self.root
            self.result["update_id"] = self.update_id
            update_log(self.root, "prepared", update_id=self.update_id, operation=self.operation,
                       source=self.result.get("source_name"), version=self.result.get("release", {}).get("tag_name"))
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
        self.cache_root = self.root
        self.connection = None
        self.source_config = None
        self.worker = None
        self.prepared = None
        self.pending = None
        self.install_requested = False
        self.automatic_started = False
        self.state = {"phase": "idle", "message": "检查是否有可用更新", "percent": -1, "result": {}, "error": ""}
        try:
            self.connection = backend.update_connection(self.install_dir)
            self.source_config = None if self.connection else backend.community_source_config()
        except backend.UpdaterError as exc:
            self.state.update(phase="error", message=str(exc), error=str(exc), source="online")

    def snapshot(self):
        return copy.deepcopy({**self.state, "busy": self.worker is not None, "install_requested": self.install_requested})

    def publish(self, **fields):
        self.state.update(fields)
        self.changed.emit(self.snapshot())

    def start_automatic(self):
        if self.automatic_started:
            return
        self.automatic_started = True
        if self.worker is None:
            self.check()

    def check(self):
        self.automatic_started = True
        if self.worker is None:
            try:
                connection = backend.update_connection(self.install_dir)
                source_config = None if connection else backend.community_source_config()
            except backend.UpdaterError as exc:
                self.install_requested = False
                self.prepared = None
                update_log(self.root, "source_error", error=str(exc))
                self.publish(phase="error", message=str(exc), error=str(exc), result={}, source="online")
                return
            if connection != self.connection or source_config != self.source_config:
                self.prepared = None
                self.state["result"] = {}
            self.connection = connection
            self.source_config = source_config
            if connection or source_config["public_base_url"]:
                key = hashlib.sha256(json.dumps(connection or source_config, sort_keys=True).encode("utf-8")).hexdigest()
                self.cache_root = os.path.join(self.root, "channels", key)
            else:
                self.cache_root = self.root
            self._start("online")

    def _start(self, operation, payload=None):
        work_root = self.cache_root if operation == "online" else self.root
        self.worker = UpdateWorker(operation, work_root, self.install_dir, self.current_version, self.install_enabled, payload, self,
                                   connection=self.connection, source_config=self.source_config)
        self.worker.event.connect(self._event)
        self.worker.finished.connect(self._finished)
        fields = {"error": "", "source": "local" if operation == "local" else "online" if operation == "online" else self.state.get("source", "online")}
        if operation in {"online", "local"}:
            fields["fallback_reason"] = ""
        self.publish(**fields)
        self.worker.start()

    def _event(self, event):
        if self.pending or (self.worker and self.worker.cancel.is_set()):
            return
        if event.get("phase") == "ready":
            self.prepared = event["result"]
        elif event.get("phase") == "available" and self.prepared:
            if not backend.same_release_asset(self.prepared.get("asset") or {}, event["result"].get("asset") or {}):
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
            if getattr(worker.error, "retry_after", 0):
                message = "更新服务请求过于频繁，请稍后重试或选择本地安装包。"
            cached_available = (self.prepared and worker.operation == "online" and not self.connection
                                and isinstance(worker.error, backend.UpdateNetworkError))
            if cached_available:
                message = "更新已准备好，可直接安装；暂时无法检查其他更新"
            else:
                self.prepared = None
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
            self.publish(phase="available" if result["update_available"] else "current",
                         message=result.get("message") or ("发现新版本" if result["update_available"] else "当前已是最新版本"), result=result, percent=-1)

    def install_aborted(self, message="更新已准备好，尚未安装"):
        self.install_requested = False
        self.publish(phase="ready", message=message, percent=-1, can_cancel_install=False)
