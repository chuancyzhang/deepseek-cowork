"""Versioned desktop RPC, reusing the existing Agent/provider execution path.

The persistent service owns tasks independently of Electron. A JSONL stdio
adapter is also available for diagnostics. Workers use native Python threads and ordered event subscriptions;
no GUI framework is loaded by the service.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
import uuid

log = logging.getLogger("desktop")
PROTOCOL_VERSION = 2


class DesktopService:
    def __init__(self, publish, startup=None):
        startup = startup or (lambda _message: None)
        startup("正在准备本地服务…")
        # Initialize the Office native extension chain before creating RPC
        # workers. On Windows/Python 3.14, first NumPy initialization from a
        # worker can block inside the DLL loader and leave file.open pending.
        import openpyxl
        from core.env_utils import get_app_data_dir
        from core.desktop_upgrade import existing_database
        from core.desktop_config import DesktopConfigManager
        from core.runtime_coordinator import RuntimeCoordinator
        from core.daemon import DaemonServer, DaemonRequestHandler
        from core.desktop_store import DesktopChatStorage, DesktopStore
        self.publish = publish
        database = existing_database(get_app_data_dir())
        startup("正在恢复设置与历史…")
        # The host resolves the existing production directory before startup.
        # Development directories must never import a user's history implicitly.
        self.config = DesktopConfigManager(migrate_legacy=False)
        self.storage = DesktopChatStorage(str(database))
        self.store = DesktopStore(self.storage)
        from core.runtime_ledger import RuntimeLedger, LedgerJournal
        self.ledger = RuntimeLedger(self.storage)
        self.storage.runtime_ledger = self.ledger
        journal = LedgerJournal(self.ledger)
        startup("正在恢复运行状态…")
        self.state = RuntimeCoordinator(self.config, storage=self.storage, journal=journal)
        self.state.desktop_store = self.store
        startup("正在准备工作台…")
        self.lock = threading.RLock()
        from core.desktop_conversations import ConversationActions
        self.conversations = ConversationActions(self)
        self.streams = self.state.streams
        self.closing = False
        self.shutdown_complete = False
        self.shutdown_lock = threading.Lock()
        from core.desktop_jobs import DesktopJobs
        from core.desktop_management import Management
        from core.desktop_scheduler import DesktopScheduler
        self.jobs = DesktopJobs(self)
        self.management = Management(self)
        from core.desktop_settings import DesktopSettings
        self.settings_pages = DesktopSettings(self)
        from core.desktop_updates import DesktopUpdates
        self.updates = DesktopUpdates(self)
        from core.desktop_models import DesktopModels
        self.models = DesktopModels(self)
        from core.desktop_skillhub import DesktopSkillHub
        self.skillhub = DesktopSkillHub(self)
        self.scheduler = DesktopScheduler(self)
        from core.desktop_connections import Connections
        self.connections = Connections(self)
        from core.desktop_workbench import DesktopWorkbench
        self.workbench = DesktopWorkbench(self)
        from core.desktop_workbench import DesktopFiles
        from core.desktop_conversations import ConversationRuntime
        from core.desktop_settings import DesktopSettingsAPI
        self.files = DesktopFiles(self.storage, self.config, self.jobs)
        self.conversation_runtime = ConversationRuntime(config=self.config, storage=self.storage, store=self.store,
            state=self.state, files=self.files, actions=self.conversations, publish=self.publish,
            start=self.start_stream, view=self.conversation, request=self.request_agent)
        self.settings_api = DesktopSettingsAPI(self.config, self.management.themes, self.publish, self.public_settings, self.store)
        token = uuid.uuid4().hex + uuid.uuid4().hex
        self.token = token

        class AuthenticatedHandler(DaemonRequestHandler):
            def handle(handler):
                raw = handler.rfile.readline(16 * 1024 * 1024)
                try:
                    request = json.loads(raw)
                    import hmac
                    if not hmac.compare_digest(str(request.pop("desktop_token", "")), token):
                        raise ValueError("invalid host")
                    if request.pop("runtime_protocol", None) != PROTOCOL_VERSION:
                        raise ValueError("unsupported runtime protocol")
                except Exception:
                    handler._send({"status": "error", "error": "无效的桌面连接。"})
                    return
                original = handler.rfile
                handler.rfile = io.BytesIO(json.dumps(request).encode() + b"\n")
                try:
                    super(AuthenticatedHandler, handler).handle()
                finally:
                    handler.rfile = original

        self.server = DaemonServer(("127.0.0.1", 0), AuthenticatedHandler, self.state)
        self.port = self.server.server_address[1]
        from core.desktop_messaging import DesktopMessaging
        self.messaging = DesktopMessaging(self)
        threading.Thread(target=self.server.serve_forever, name="desktop-agent-host", daemon=True).start()
        self.state.recover_runs()
        log.info("service_ready protocol=%s", PROTOCOL_VERSION)

    def request_agent(self, payload, timeout=10):
        result = self.state.request(payload)
        if result.get("status") == "error":
            raise RuntimeError(result.get("error") or "后台操作失败。")
        return result

    def public_settings(self):
        from core.long_context import desktop_policy
        models = []
        for profile in self.config.iter_model_profiles():
            models.append({key: profile.get(key) for key in ("id", "display_name", "model_name", "channel_id", "channel_display_name", "provider_type", "base_url", "reasoning_effort", "reasoning_efforts")}
                          | {"configured": bool(profile.get("api_key")), "label": self.config.get_model_label(profile["id"])})
        return {"models": models, "selected_model_id": self.config.get_selected_model_id(),
                "god_mode": self.config.get_god_mode(), "theme": self.management.themes.load_appearance()["mode"],
                "data_dir": self.config.data_dir, "projects": self.config.get_projects(),
                "context_policy": desktop_policy(self.config)}

    def conversation(self, conversation_id, *, before=None, limit=80):
        record = self.storage.get_conversation_record(conversation_id)
        if not record:
            raise ValueError("对话不存在。")
        run = self.store.latest_run(conversation_id)
        window = self.store.message_window(conversation_id, before=before, limit=limit)
        visible_turns = {str((message.get("meta") or {}).get("turn_id") or "") for message in window["messages"]}
        events = []
        for historical_run in self.store.conversation_runs(conversation_id):
            if historical_run["id"] in visible_turns and self.state.runtime_journal.get_run(conversation_id, historical_run["id"]):
                events.extend(self.state.runtime_journal.read_events(conversation_id, historical_run["id"]))
        from core.conversation_render import project_visible_messages
        draft = self.store.versioned_state("draft:" + conversation_id, {"text": "", "attachments": []})
        view = self.store.versioned_state("view:" + conversation_id, {})
        from core.skill_manager import RETIRED_SKILLS
        record["meta"]["selected_skill_names"] = [name for name in record["meta"].get("selected_skill_names", []) if name not in RETIRED_SKILLS]
        rendered = project_visible_messages(window["messages"], timeline_events=record["meta"].get("ui_timeline_v1", []))
        return {**record, **window, "render_messages": self.files.message_files(conversation_id, rendered),
                "run": self.public_run(run), "events": events,
                "draft": draft["value"], "draft_revision": draft["revision"],
                "view": view["value"], "view_revision": view["revision"]}

    def attachment_content(self, cid, text, attachments):
        content = str(text or "").strip()
        for attachment in attachments:
            target = self.checked_file(cid, attachment["path"])
            if not target.is_file():
                raise ValueError("附件已不存在，请重新添加。")
            content += f"\n\n[附件：{target.name}]\n{target}"
        return content

    @staticmethod
    def public_run(run):
        return {key: run[key] for key in ("id", "conversation_id", "message_id", "status", "error", "created_at", "updated_at") } if run else None

    def workspace(self, conversation_id):
        return self.files.workspace(conversation_id)

    def checked_file(self, conversation_id, path):
        return self.files.checked_file(conversation_id, path)

    def start_stream(self, payload, profile):
        self.state.start_stream(payload, profile, self.publish, self.handle_workbench)

    def handle_workbench(self, event):
        identity = {"request_id": event["data"]["request_id"], "session_id": event["session_id"], "run_id": event["run_id"]}
        try:
            claim = self.request_agent({**identity, "action": "workbench_claim"})
            if claim.get("status") != "ok":
                return
            try:
                result = self.workbench.agent_request(identity, claim)
            except Exception as exc:
                log.exception("workbench_action_failed session=%s action=%s", identity["session_id"], claim["action"])
                result = {"ok": False, "status": getattr(exc, "code", "failed"), "error": str(exc)}
            self.request_agent({**identity, "token": claim["token"], "action": "workbench_result", "result": result})
        except Exception:
            log.exception("workbench_claim_failed session=%s", identity["session_id"])

    def dispatch(self, method, params):
        if method.endswith((".get", ".list", ".search", ".export", ".inspect")) or method == "app.bootstrap":
            pending = self.ledger.projected < self.ledger.durable
            if pending:
                self.publish({"event": "query_sync", "pending": True, "method": method})
            try:
                with self.storage.read_scope():
                    return self._dispatch(method, params)
            finally:
                if pending:
                    self.publish({"event": "query_sync", "pending": False, "method": method})
        if method == "conversation.send":
            identity = str((params or {}).get("request_id") or "")
            rejected = self.store.get_state("submission_rejected:" + identity)
            if rejected:
                raise ValueError(rejected["error"])
            try:
                return self._dispatch(method, params)
            except Exception as exc:
                if identity and not self.store.get_run(identity):
                    self.store.put_state("submission_rejected:" + identity, {"conversation_id": (params or {}).get("conversation_id"), "error": str(exc)})
                raise
        return self._dispatch(method, params)
    def _dispatch(self, method, params):
        p = params or {}
        if not isinstance(p, dict):
            raise ValueError("无效的操作参数。")
        for domain in (self.workbench, self.conversations, self.conversation_runtime, self.files,
                       self.management, self.settings_pages, self.settings_api, self.messaging,
                       self.updates, self.models, self.skillhub, self.connections):
            if method in domain.methods:
                return domain.dispatch(method, p)
        cid = str(p.get("conversation_id") or "")
        if method == "app.bootstrap":
            return {"protocol": PROTOCOL_VERSION, "settings": self.public_settings(),
                    "conversations": self.store.summarize_conversations(self.storage.list_conversation_summaries(limit=500)),
                    "active_runs": [self.public_run(run) for run in self.store.active_runs()],
                    "last_conversation": self.store.get_state("last_conversation", ""),
                    "home_draft": self.store.versioned_state("draft:", {"text": "", "attachments": []})}
        if method == "skills.list":
            snapshot = self.state.skill_catalog.snapshot()
            return [{key: skill.get(key) for key in ("name", "display_name", "description", "enabled", "source_kind")} for skill in snapshot.display_skills()]
        if method == "workbench.result":
            return self.request_agent({**p, "action": "workbench_result"})
        if method == "app.shutdown":
            return self.shutdown()
        raise ValueError("不支持的操作：" + method)

    def shutdown(self):
        with self.shutdown_lock:
            if self.shutdown_complete:
                return {"stopped": True}
            log.info("service_shutdown_requested active_runs=%s", len(self.store.active_runs()))
            with self.lock:
                self.closing = True
                self.state.update_preparing = True
            try:
                for run in self.store.active_runs():
                    self.dispatch("conversation.stop", {"conversation_id": run["conversation_id"], "run_id": run["id"]})
                deadline = time.monotonic() + 15
                while self.streams and time.monotonic() < deadline:
                    time.sleep(.05)
                if self.streams:
                    raise RuntimeError("任务仍在结束处理中，请稍后再退出。")
                settled = self.state.prepare_update_shutdown()
                if not settled.get("saved"):
                    raise RuntimeError(settled.get("error") or "后台任务尚未完成保存，请稍后再退出。")
                self.updates.close()
                self.scheduler.close()
                self.messaging.close()
                self.workbench.close()
                self.jobs.close()
                self.models.close()
                self.ledger.close()
                self.shutdown_complete = True
                log.info("service_shutdown_saved")
                return {"stopped": True}
            except Exception:
                self.closing = False
                self.state.update_preparing = False
                self.updates.resume_automatic()
                log.exception("service_shutdown_failed retry_available=true")
                raise


def main():
    # The frozen Windows interpreter may use the system ANSI codepage even
    # when the launcher sets PYTHONUTF8. JSONL is UTF-8 in both directions.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--serve", action="store_true", help="Keep tasks alive across desktop reconnects")
    parser.add_argument("--im-gateway", action="store_true")
    args = parser.parse_args()
    os.environ["COWORK_APP_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    if args.im_gateway:
        from core.im_gateway.runtime import run
        run()
        return 0
    wire = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    output_lock = threading.Lock()
    def publish(value):
        with output_lock:
            wire.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            wire.flush()
    from core.desktop_upgrade import DataLease
    lease = DataLease(args.data_dir)
    transport = None
    if args.serve:
        from core.desktop_transport import DesktopTransport
        transport = DesktopTransport(args.data_dir, PROTOCOL_VERSION)
        # Publish the authenticated control endpoint before initialization, so a
        # host can observe progress or stop startup without spawning a rival.
        transport.start()
        publish = transport.publish
    try:
        service = DesktopService(publish, startup=transport.progress if transport else None)
        if transport:
            transport.progress("工作台已准备就绪")
    except BaseException as error:
        if transport:
            transport.close()
        lease.close()
        from core.desktop_transport import StartupCancelled
        if isinstance(error, StartupCancelled):
            log.info("startup_cancelled original_data_retained=true")
            return 0
        raise
    if transport:
        transport.start(service)
        service.scheduler.start()
        service.messaging.restart()
        try:
            while not service.shutdown_complete:
                time.sleep(.1)
        finally:
            service.scheduler.close()
            service.jobs.close()
            transport.close()
            service.server.shutdown()
            service.state.skill_catalog.stop_watching()
            lease.close()
            log.info("service_stopped")
        return 0
    executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="desktop-rpc")
    def handle(request):
        identity = request.get("id")
        method = str(request.get("method") or "")
        try:
            result = service.dispatch(method, request.get("params"))
            publish({"id": identity, "result": result})
        except Exception as exc:
            log.exception("rpc_error method=%s request=%s", method, identity)
            publish({"id": identity, "error": {"message": str(exc), "code": type(exc).__name__}})
    def read_commands():
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    executor.submit(handle, request)
                except Exception as exc:
                    publish({"event": "protocol_error", "error": str(exc)})
        finally:
            if not service.shutdown_complete:
                log.warning("host_disconnected stopping_active_runs=true")
                try:
                    service.dispatch("app.shutdown", {})
                except Exception:
                    log.exception("host_disconnected_stop_failed")
    threading.Thread(target=read_commands, name="desktop-commands", daemon=True).start()
    publish({"event": "ready", "protocol": PROTOCOL_VERSION})
    service.scheduler.start()
    while not service.shutdown_complete:
        time.sleep(0.05)
    result = 0
    executor.shutdown(wait=False, cancel_futures=True)
    service.scheduler.close()
    service.jobs.close()
    service.server.shutdown()
    service.state.skill_catalog.stop_watching()
    lease.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
