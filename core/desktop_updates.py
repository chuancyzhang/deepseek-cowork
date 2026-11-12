"""Electron package updates using the shared verified transaction installer."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time

import requests

from core import updater
from core.app_version import APP_VERSION, compare_versions
from core.desktop_jobs import JobCancelled
from core.update_coordinator import atomic_json

log = logging.getLogger("desktop.updates")


def validate_release(value):
    version = value.get("version") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("app_layout") != "electron"
            or value.get("target_os") != "windows" or value.get("arch") != "x64"
            or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version)
            or value.get("filename") != f"cowork-desktop-v{version}-windows-x64.zip"
            or type(value.get("size")) is not int or value["size"] <= 0
            or not re.fullmatch(r"[0-9a-fA-F]{64}", str(value.get("sha256") or ""))):
        raise ValueError("更新清单不属于新版 Windows 工作台，尚未安装任何文件。")
    return value


class DesktopUpdates:
    methods = frozenset({"updates.prepare_install", "updates.abort_install", "updates.commit_install", "updates.acknowledge"})

    def __init__(self, service):
        self.s = service
        self.root = Path(service.config.data_dir) / "updates" / "desktop"
        self.root.mkdir(parents=True, exist_ok=True)
        self.install_dir = Path(os.environ.get("COWORK_DESKTOP_INSTALL_DIR") or Path(__file__).resolve().parents[1]).resolve()
        self.packaged = updater._is_valid_app_dir(self.install_dir, layout="electron")
        self.lock = threading.RLock()
        self.prepared = None
        self.transaction = None
        self.startup_request = {"id": os.environ.pop("COWORK_UPDATE_ID", ""), "receipt": os.environ.pop("COWORK_UPDATE_RECEIPT", ""), "install_dir": str(self.install_dir)}

    def page(self):
        from core.desktop_settings import action
        result = {"title": "更新与关于", "values": {}, "revision": "", "sections": [{"title": "DeepSeek Cowork", "status": "当前版本 " + APP_VERSION,
            "actions": [action("update.check", "检查更新"), action("update.local", "选择本地安装包", open_kind="update")]}]}
        if self.prepared:
            result["sections"].append({"title": "版本 " + self.prepared["version"], "status": "已下载并校验" + (" · 开发运行请使用完整安装包" if not self.packaged else ""),
                "description": self.prepared.get("notes", ""), "actions": [action("update.install", "重启并更新", disabled=not self.packaged)]})
        return result

    def action(self, identity, p):
        if identity == "update.install":
            if not self.prepared or not self.packaged:
                raise ValueError("请在完整安装包中准备更新。")
            self.s.publish({"event": "update_install_requested"})
            return {"requested": True}
        def prepare(cancelled, progress):
            def report(message, percent=None):
                if cancelled.is_set():
                    raise JobCancelled("已取消更新，当前版本继续可用。")
                progress(message, percent=percent)
            with self.lock:
                report("正在读取更新包…")
                notes, expected_version = "", None
                if identity == "update.check":
                    base = updater.community_source_config()["public_base_url"]
                    url = base + "/updates/desktop-windows-x64/latest.json" if base else updater.GITHUB_RELEASES_URL + "/latest/download/latest-desktop-windows-x64.json"
                    with requests.get(url, headers={"User-Agent": "cowork-desktop-updater", "Cache-Control": "no-cache"}, timeout=20) as response:
                        if response.status_code == 404:
                            return {"message": "更新源尚未发布新版工作台安装包。当前版本可以继续使用。"}
                        response.raise_for_status()
                        manifest = validate_release(response.json())
                    if compare_versions(manifest["version"], APP_VERSION) <= 0:
                        return {"message": "当前已是最新版本。"}
                    expected_version, notes = manifest["version"], manifest.get("notes", "")
                    asset = updater.ReleaseAsset(manifest["filename"], url.rsplit("/", 1)[0] + "/" + manifest["filename"], manifest["size"], manifest["version"], "sha256:" + manifest["sha256"])
                    package = updater.download_asset(asset, target_dir=str(self.root), progress_callback=report, cancel_event=cancelled)
                else:
                    package = str(p.get("path") or "")
                staged = updater.extract_update_zip(package, str(self.root), report, layout="electron")
                metadata = json.loads((Path(staged) / "desktop-release.json").read_text(encoding="utf-8"))
                version = metadata.get("version", "")
                if metadata.get("app_layout") != "electron" or metadata.get("target") != "windows-x64" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
                    raise ValueError("安装包的版本或平台无效。")
                if expected_version and expected_version != version:
                    raise ValueError("安装包版本与发布清单不一致。")
                if compare_versions(version, APP_VERSION) < 0:
                    raise ValueError("不能安装低于当前版本的更新。")
                report("安装包已校验。")
                updater.validate_online_package(staged, updater.update_connection(str(self.install_dir)))
                self.prepared = {"version": version, "notes": notes, "staged": staged, "package": package}
                self.s.publish({"event": "update_ready", "data": {"version": version}})
                return {"refresh": True, "message": "更新包已准备好。"}
        return self.s.jobs.submit("检查工作台更新", prepare)

    def dispatch(self, method, p):
        if method == "updates.acknowledge":
            from core.update_startup import acknowledge_update_request
            return {"acknowledged": acknowledge_update_request(self.startup_request, str(self.root), str(self.install_dir), APP_VERSION) if self.startup_request["id"] else False}
        with self.lock:
            if method == "updates.prepare_install":
                if not self.packaged or not self.prepared:
                    raise ValueError("没有已准备好的工作台更新。")
                if self.s.store.active_runs() or self.s.jobs.active:
                    raise ValueError("请等后台任务和操作结束后再更新；已有内容已保留。")
                plan = updater.build_update_plan(str(self.install_dir), self.prepared["staged"], layout="electron")
                plan_path = updater.write_update_plan(plan, str(self.root))
                script = updater.create_windows_update_script(str(self.install_dir), self.prepared["staged"], plan_path,
                    current_pid=int(p["host_pid"]), exe_name=updater.desktop_executable(self.prepared["staged"]), target_dir=str(self.root), extra_wait_pids=[os.getpid()],
                    version=self.prepared["version"], layout="electron")
                transaction = Path(script).parent
                process = updater.launch_windows_update_script(script)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not (transaction / "ready.json").exists():
                    if process.poll() is not None:
                        raise ValueError("安装器启动失败，请查看更新诊断日志。")
                    time.sleep(.1)
                if not (transaction / "ready.json").exists():
                    atomic_json(str(transaction / "abort.json"), {"id": transaction.name})
                    raise ValueError("安装器准备超时，当前版本保持运行。")
                self.transaction = transaction
                return {"id": transaction.name}
            if not self.transaction or p.get("id") != self.transaction.name:
                raise ValueError("更新事务不存在。")
            if method == "updates.abort_install":
                atomic_json(str(self.transaction / "abort.json"), {"id": self.transaction.name})
                return {"aborted": True}
            if method == "updates.commit_install":
                if self.s.store.active_runs():
                    raise ValueError("仍有运行中的任务，更新尚未开始。")
                self.s.dispatch("app.shutdown", {})
                atomic_json(str(self.transaction / "commit.json"), {"id": self.transaction.name})
                return {"committed": True}
        raise ValueError("不支持的更新操作。")
