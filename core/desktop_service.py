"""Versioned desktop RPC, reusing the existing Agent/provider execution path.

stdout is exclusively JSONL. Electron owns the process, not a renderer window;
reloading a view cannot cancel an Agent. The legacy Qt worker is an adapter in
this migration, with a QCoreApplication and no GUI application or widgets.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import io
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
PROTOCOL_VERSION = 1


class DesktopService:
    def __init__(self, publish):
        from core.config_manager import ConfigManager
        from core.daemon import DaemonState, DaemonServer, DaemonRequestHandler
        from core.desktop_store import DesktopChatStorage, DesktopStore
        self.publish = publish
        # Explicit import copies only model settings. Legacy migration may carry
        # absolute history/workspace paths and must not join the two data stores.
        self.config = ConfigManager(migrate_legacy=False)
        self.state = DaemonState(self.config)
        from core.runtime_journal import RuntimeJournal
        # The Web view reconstructs the timeline from committed events, including
        # partial output and interaction receipts absent from the provider ledger.
        self.state.runtime_journal = RuntimeJournal(self.config.get_chat_history_dir(), retain_committed_events=True)
        self.state.chat_storage = DesktopChatStorage(self.state.chat_storage.db_path)
        self.storage = self.state.chat_storage
        self.store = DesktopStore(self.storage)
        self.lock = threading.RLock()
        self.file_sessions = {}
        self.streams = {}
        self.closing = False
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
        threading.Thread(target=self.server.serve_forever, name="desktop-agent-host", daemon=True).start()
        for run in self.store.active_runs():
            journal_run = self.state.runtime_journal.get_run(run["conversation_id"], run["id"]) or {}
            terminal = journal_run.get("status")
            status = terminal if terminal in {"completed", "failed", "interrupted"} else "interrupted"
            self.store.set_run(run["id"], status, journal_run.get("terminal_error") or ("应用上次退出时任务尚未结束，已保存的消息和输出仍可查看。" if status == "interrupted" else ""))
        log.info("service_ready protocol=%s", PROTOCOL_VERSION)

    def request_agent(self, payload, timeout=10):
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as connection:
            connection.sendall((json.dumps({**payload, "desktop_token": self.token}, ensure_ascii=False) + "\n").encode())
            with connection.makefile("r", encoding="utf-8") as reader:
                result = json.loads(reader.readline())
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
                "god_mode": self.config.get_god_mode(), "theme": self.store.get_state("theme", "light"),
                "data_dir": self.config.data_dir, "projects": self.config.get("projects", []),
                "context_policy": desktop_policy(self.config)}

    def conversation(self, conversation_id):
        record = self.storage.get_conversation_record(conversation_id)
        if not record:
            raise ValueError("对话不存在。")
        run = self.store.latest_run(conversation_id)
        events = []
        for historical_run in self.store.conversation_runs(conversation_id):
            if self.state.runtime_journal.get_run(conversation_id, historical_run["id"]):
                events.extend(self.state.runtime_journal.read_events(conversation_id, historical_run["id"]))
        return {**record, "messages": self.storage.get_messages(conversation_id),
                "run": self.public_run(run), "events": events,
                "draft": self.store.get_state("draft:" + conversation_id, {"text": "", "attachments": []}),
                "view": self.store.get_state("view:" + conversation_id, {})}

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
        return {key: run[key] for key in ("id", "conversation_id", "message_id", "status", "error") } if run else None

    def workspace(self, conversation_id):
        record = self.storage.get_conversation_record(conversation_id)
        if not record:
            raise ValueError("对话不存在。")
        return Path(record["meta"]["workspace_dir"]).resolve()

    def checked_file(self, conversation_id, path):
        root = self.workspace(conversation_id)
        target = Path(path)
        target = (root / target).resolve() if not target.is_absolute() else target.resolve()
        if not target.is_relative_to(root):
            raise ValueError("文件不属于当前工作目录，请先切换工作目录。")
        return target

    def start_stream(self, payload, profile):
        run_id, conversation_id = payload["request_id"], payload["conversation_id"]
        with self.lock:
            if run_id in self.streams:
                return
            thread = threading.Thread(target=self.consume_stream, args=(payload, profile), name=f"desktop-run-{run_id[:8]}", daemon=True)
            self.streams[run_id] = thread
            thread.start()

    def consume_stream(self, payload, profile):
        run_id, conversation_id = payload["request_id"], payload["conversation_id"]
        try:
            self.store.set_run(run_id, "running")
            context = {"selected_model_profile": profile, "mode": "normal",
                       "desktop_long_context": payload.get("context_policy", {})}
            if payload.get("skills"):
                context["selected_skill_names"] = payload["skills"]
            request = {"action": "send_message_stream", "desktop_token": self.token,
                       "session_id": conversation_id, "request_id": run_id, "turn_id": run_id,
                       "user_message_id": payload["message_id"], "content": payload["content"],
                       "workspace_dir": payload["workspace_dir"], "run_context": context,
                       "writer_owner": f"desktop:{os.getpid()}", "messages": self.storage.get_messages(conversation_id)}
            log.info("run_start session=%s run=%s", conversation_id, run_id)
            final_seen = False
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
                connection.settimeout(None)
                connection.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode())
                with connection.makefile("r", encoding="utf-8") as reader:
                    for line in reader:
                        event = json.loads(line)
                        if event.get("type") == "final":
                            final_seen = True
                            result = event.get("result") or {}
                            journal = self.state.runtime_journal.get_run(conversation_id, run_id) or {}
                            error = result.get("_history_commit_error") or result.get("_runtime_journal_warning") or result.get("error") or ""
                            self.store.set_run(run_id, "interrupted" if journal.get("stop_requested") else "failed" if error else "completed", error)
                        self.publish({"event": "runtime", "data": event, "conversation_id": conversation_id, "run_id": run_id})
                        if event.get("type") == "workbench_request":
                            threading.Thread(target=self.handle_workbench, args=(event,), daemon=True).start()
            if not final_seen:
                raise RuntimeError("执行连接已断开；已保存的消息和输出仍可查看，请重新打开对话核对状态。")
            terminal = self.store.get_run(run_id)
            log.info("run_complete session=%s run=%s status=%s", conversation_id, run_id, terminal["status"])
        except Exception as exc:
            log.exception("run_error session=%s run=%s", conversation_id, run_id)
            self.store.set_run(run_id, "failed", str(exc))
            self.publish({"event": "run_error", "conversation_id": conversation_id, "run_id": run_id, "error": str(exc)})
        finally:
            with self.lock:
                self.streams.pop(run_id, None)
            self.publish({"event": "conversation_changed", "conversation_id": conversation_id})

    def handle_workbench(self, event):
        identity = {"request_id": event["data"]["request_id"], "session_id": event["session_id"], "run_id": event["run_id"]}
        try:
            claim = self.request_agent({**identity, "action": "workbench_claim"})
            if claim.get("status") != "ok":
                return
            self.publish({"event": "workbench", "data": {**identity, **claim}})
        except Exception:
            log.exception("workbench_claim_failed session=%s", identity["session_id"])

    def dispatch(self, method, params):
        p = params or {}
        cid = str(p.get("conversation_id") or "")
        if method == "app.bootstrap":
            return {"protocol": PROTOCOL_VERSION, "settings": self.public_settings(),
                    "conversations": self.storage.list_conversation_summaries(limit=500),
                    "active_runs": [self.public_run(run) for run in self.store.active_runs()],
                    "last_conversation": self.store.get_state("last_conversation", "")}
        if method == "conversation.create":
            cid = uuid.uuid4().hex
            root = Path(p.get("workspace_dir") or (Path(self.config.get_chat_workspace_root()) / cid)).resolve()
            root.mkdir(parents=True, exist_ok=True)
            self.storage.upsert_conversation(cid, title="新对话", meta={"workspace_dir": str(root), "workspace_path": str(root), "desktop": True})
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid)
        if method == "conversation.list":
            return self.storage.list_conversation_summaries(limit=500)
        if method == "conversation.get":
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid)
        if method == "conversation.rename":
            if not self.storage.has_conversation(cid):
                raise ValueError("对话不存在。")
            title = str(p.get("title") or "").strip()[:200]
            if not title:
                raise ValueError("请输入对话标题。")
            self.storage.upsert_conversation(cid, title=title)
            return {"saved": True}
        if method == "conversation.archive":
            if any(r["conversation_id"] == cid for r in self.store.active_runs()):
                raise ValueError("请先停止当前任务，再归档对话。")
            self.storage.update_conversation_meta(cid, {"archived": True})
            return {"saved": True}
        if method in {"draft.save", "view.save"}:
            self.workspace(cid)
            return self.store.put_state(("draft:" if method == "draft.save" else "view:") + cid, p["value"])
        if method == "view.get":
            self.workspace(cid)
            return self.store.get_state("view:" + cid, {})
        if method == "conversation.send":
            with self.lock:
                if self.closing:
                    raise ValueError("应用正在退出，输入内容已保留。")
                profile = self.config.get_model_profile(p.get("model_id"))
                if not profile or not profile.get("api_key"):
                    raise ValueError("请先在“模型与服务”中填写 API Key，或导入现有设置。输入内容已保留。")
                text = str(p.get("text") or "").strip()
                attachments = p.get("attachments") or []
                if not text and not attachments:
                    raise ValueError("请输入消息或添加附件。")
                content = self.attachment_content(cid, text, attachments)
                payload = {"conversation_id": cid, "request_id": str(p["request_id"]),
                           "message_id": str(p["message_id"]), "content": content, "display_text": text,
                           "workspace_dir": str(self.workspace(cid)), "model_id": profile["id"],
                           "skills": p.get("skills") or [], "attachments": attachments}
                # Freeze policy at acceptance. An idempotent retry must use the
                # original policy even after settings have changed.
                existing = self.store.get_run(payload["request_id"])
                from core.long_context import desktop_policy
                payload["context_policy"] = (json.loads(existing["payload"]).get("context_policy", {})
                                             if existing else desktop_policy(self.config))
                run, created = self.store.accept(payload)
                log.info("message_accepted session=%s run=%s created=%s", cid, run["id"], created)
                if created:
                    self.start_stream(payload, copy.deepcopy(profile))
                return {"accepted": True, "run": self.public_run(run)}
        if method == "conversation.stop":
            run = self.store.get_run(str(p["run_id"]))
            if not run or run["conversation_id"] != cid:
                raise ValueError("任务不属于当前对话。")
            # Wait for the queued run to register its worker; a stop immediately
            # after the acceptance receipt must not silently miss that worker.
            deadline = time.monotonic() + 10
            while run["status"] in {"queued", "running"} and not self.state.runtime_journal.get_run(cid, run["id"]):
                if time.monotonic() >= deadline:
                    raise RuntimeError("任务仍在启动，请稍后重试停止。")
                time.sleep(.02)
                run = self.store.get_run(run["id"])
            if run["status"] not in {"queued", "running", "stopping"}:
                return {"stopped": True, "released": True}
            self.store.set_run(run["id"], "stopping")
            result = self.request_agent({"action": "stop_session", "session_id": cid, "run_id": run["id"]}, timeout=20)
            if not result.get("released"):
                raise RuntimeError("任务仍在结束处理中，已完成内容会保留，请稍后核对状态。")
            log.info("run_stop session=%s run=%s released=%s", cid, run["id"], result.get("released"))
            return result
        if method == "conversation.steer":
            message = copy.deepcopy(p["message"])
            message["content"] = self.attachment_content(cid, message.get("content"), p.get("attachments") or [])
            message.setdefault("meta", {})["desktop_attachments"] = p.get("attachments") or []
            return self.request_agent({"action": "steer_message", "session_id": cid, "expected_turn_id": p["run_id"], "message": message})
        if method == "interaction.respond":
            # The shared interaction service also checks the request identity.
            from core.interaction import interaction_service, parse_interaction_reply
            request = interaction_service.get_pending_request(cid)
            if not request or request["request_id"] != p["request_id"]:
                raise ValueError("这项操作已经结束或不属于当前对话。")
            result, valid, reason = parse_interaction_reply(request, p["value"])
            if not valid:
                raise ValueError(reason or "请完成填写。")
            return self.request_agent({"action": "respond_interaction", "request_id": p["request_id"], "result": result})
        if method == "settings.get":
            return self.public_settings()
        if method == "settings.save_model":
            channel_id = str(p.get("channel_id") or uuid.uuid4().hex)
            channels = self.config.get_model_channels()
            existing = next((c for c in channels if c.get("channel_id") == channel_id), {})
            key = str(p.get("api_key") or "").strip() or existing.get("api_key", "")
            model_id = str(p.get("model_id") or uuid.uuid4().hex)
            channel = {**existing, "channel_id": channel_id, "display_name": p.get("name") or "模型服务",
                       "provider_type": p.get("provider_type") or "openai", "base_url": p["base_url"], "api_key": key,
                       "models": [{"id": model_id, "model_name": p["model_name"], "display_name": p["model_name"],
                                   "api_protocol": p.get("api_protocol") or "chat_completions"}]}
            channels = [c for c in channels if c.get("channel_id") != channel_id] + [channel]
            self.config.set_model_channels(channels, model_id)
            return self.public_settings()
        if method == "settings.select_model":
            self.config.set_selected_model_id(p["model_id"])
            return self.public_settings()
        if method == "settings.permission":
            self.config.set_god_mode(bool(p["enabled"]))
            return self.public_settings()
        if method == "settings.theme":
            self.store.put_state("theme", p["theme"])
            return {"saved": True}
        if method == "settings.context":
            from core.long_context import TARGETS
            target = p.get("target_tokens")
            if target not in (0, *TARGETS) or type(target) is not int:
                raise ValueError("请选择未启用、300K 或 1M。")
            self.config.set_experimental_long_context({"enabled": bool(target), "target_tokens": target or TARGETS[0]})
            log.info("context_policy_saved target=%s next_run=true", target)
            return self.public_settings()
        if method == "settings.import":
            source = Path(p["path"]).resolve()
            if source.name != "config.json" or source == Path(self.config.config_path).resolve():
                raise ValueError("请选择旧版数据目录中的 config.json。")
            config = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(config.get("model_channels"), list):
                raise ValueError("文件中没有可导入的模型服务。")
            self.config.set_model_channels(config["model_channels"], config.get("selected_model_id"))
            return self.public_settings()
        if method == "workspace.list":
            root = self.workspace(cid)
            folder = self.checked_file(cid, p.get("path") or ".")
            result = []
            for entry in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if entry.name.startswith("."):
                    continue
                try:
                    resolved = entry.resolve()
                    if not resolved.is_relative_to(root):
                        continue
                    result.append({"name": entry.name, "path": str(resolved), "relative": str(resolved.relative_to(root)), "directory": entry.is_dir(), "size": entry.stat().st_size})
                except OSError:
                    continue
                if len(result) >= 500:
                    break
            return {"root": str(root), "folder": str(folder), "files": result}
        if method == "workspace.attach":
            root = self.workspace(cid) / "attachments"
            root.mkdir(exist_ok=True)
            result = []
            for source_name in p["paths"]:
                source = Path(source_name).resolve()
                if not source.is_file() or source.stat().st_size > 50 * 1024 * 1024:
                    raise ValueError("附件不存在或超过 50 MiB。")
                target = root / (uuid.uuid4().hex[:8] + "-" + source.name)
                with source.open("rb") as src, target.open("xb") as dest:
                    shutil.copyfileobj(src, dest)
                    dest.flush()
                    os.fsync(dest.fileno())
                result.append({"name": source.name, "path": str(target), "size": target.stat().st_size})
            return result
        if method == "workspace.create":
            path = self.checked_file(cid, p.get("name") or "未命名.md")
            if path.suffix.lower() not in {".md", ".txt", ".html", ".json", ".csv"}:
                raise ValueError("请选择 Markdown、文本、HTML、JSON 或 CSV 文件。")
            with path.open("x", encoding="utf-8") as handle:
                handle.write(str(p.get("text") or ""))
                handle.flush()
                os.fsync(handle.fileno())
            return self.dispatch("file.open", {"conversation_id": cid, "path": str(path)})
        if method == "file.open":
            from core.deliverable_editing import create_edit_session, load_editor_payload
            path = self.checked_file(cid, p["path"])
            session, report = create_edit_session(str(path))
            if not report.allowed:
                raise ValueError(report.message)
            payload = load_editor_payload(session)
            expected = str(p.get("expected_fingerprint") or "")
            # Preserve the original edit base across restart, so a recovered
            # draft cannot overwrite a file changed by another application.
            conflict = bool(expected and expected != session.initial_fingerprint)
            if expected:
                session.initial_fingerprint = expected
            session_id = uuid.uuid4().hex
            self.file_sessions[session_id] = (cid, session)
            if isinstance(payload.get("binary"), bytes):
                payload["binary"] = base64.b64encode(payload["binary"]).decode()
            return {"id": session_id, "path": str(path), "name": path.name, "fingerprint": session.initial_fingerprint, "payload": payload, "conflict": conflict}
        if method == "file.save":
            from core.deliverable_editing import serialize_editor_payload, atomic_save_session
            owner, session = self.file_sessions.get(p["id"], (None, None))
            if owner != cid or session is None:
                raise ValueError("编辑会话已失效，请保留内容并重新打开文件。")
            data = serialize_editor_payload(session, p["payload"])
            result = atomic_save_session(session, data, event_logger=lambda event, **details: log.info("file_save session=%s file_session=%s stage=%s", cid, p["id"], event))
            return {"saved": True, "fingerprint": result.fingerprint, "path": result.path}
        if method == "skills.list":
            snapshot = self.state.skill_catalog.snapshot()
            return [{key: skill.get(key) for key in ("name", "display_name", "description", "enabled")} for skill in snapshot.display_skills()]
        if method == "workbench.result":
            return self.request_agent({**p, "action": "workbench_result"})
        if method == "app.shutdown":
            for run in self.store.active_runs():
                try:
                    self.dispatch("conversation.stop", {"conversation_id": run["conversation_id"], "run_id": run["id"]})
                except Exception:
                    log.exception("shutdown_stop_failed run=%s", run["id"])
            deadline = time.monotonic() + 15
            while self.streams and time.monotonic() < deadline:
                time.sleep(.05)
            if self.streams:
                raise RuntimeError("任务仍在结束处理中，请稍后再退出。")
            self.closing = True
            return {"stopped": True}
        raise ValueError("不支持的操作：" + method)


def main():
    # The frozen Windows interpreter may use the system ANSI codepage even
    # when the launcher sets PYTHONUTF8. JSONL is UTF-8 in both directions.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    os.environ["COWORK_APP_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["COWORK_EXTERNAL_BACKGROUND_SERVICES"] = "0"
    wire = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    output_lock = threading.Lock()
    def publish(value):
        with output_lock:
            wire.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            wire.flush()
    from PySide6.QtCore import QCoreApplication, QTimer
    app = QCoreApplication([])
    service = DesktopService(publish)
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
            service.closing = True
            if service.streams:
                log.warning("host_disconnected stopping_active_runs=true")
                try:
                    service.dispatch("app.shutdown", {})
                except Exception:
                    log.exception("host_disconnected_stop_failed")
    threading.Thread(target=read_commands, name="desktop-commands", daemon=True).start()
    publish({"event": "ready", "protocol": PROTOCOL_VERSION})
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: app.quit() if service.closing and not service.streams else None)
    timer.start()
    result = app.exec()
    executor.shutdown(wait=False, cancel_futures=True)
    service.server.shutdown()
    service.state.skill_catalog.stop_watching()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
