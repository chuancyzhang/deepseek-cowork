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
PROTOCOL_VERSION = 1


class DesktopService:
    def __init__(self, publish):
        # Initialize the Office native extension chain before creating RPC
        # workers. On Windows/Python 3.14, first NumPy initialization from a
        # worker can block inside the DLL loader and leave file.open pending.
        import openpyxl
        from core.env_utils import get_app_data_dir
        from core.desktop_upgrade import DesktopUpgrade
        from core.desktop_config import DesktopConfigManager
        from core.daemon import DaemonState, DaemonServer, DaemonRequestHandler
        from core.desktop_store import DesktopChatStorage, DesktopStore
        self.publish = publish
        upgrade = DesktopUpgrade(get_app_data_dir())
        # The host resolves the existing production directory before startup.
        # Development directories must never import a user's history implicitly.
        self.config = DesktopConfigManager(migrate_legacy=False)
        self.state = DaemonState(self.config)
        from core.runtime_journal import RuntimeJournal
        # The Web view reconstructs the timeline from committed events, including
        # partial output and interaction receipts absent from the provider ledger.
        self.state.runtime_journal = RuntimeJournal(self.config.get_chat_history_dir(), retain_committed_events=True)
        self.state.chat_storage = DesktopChatStorage(self.state.chat_storage.db_path)
        self.storage = self.state.chat_storage
        self.store = DesktopStore(self.storage)
        upgrade.complete()
        self.lock = threading.RLock()
        from core.desktop_conversations import ConversationActions
        self.conversations = ConversationActions(self)
        self.file_sessions = {}
        self.streams = {}
        self.closing = False
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
        from core.desktop_messaging import DesktopMessaging
        self.messaging = DesktopMessaging(self)
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
        return {**record, **window, "render_messages": project_visible_messages(window["messages"], timeline_events=record["meta"].get("ui_timeline_v1", [])),
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
        return {key: run[key] for key in ("id", "conversation_id", "message_id", "status", "error") } if run else None

    def workspace(self, conversation_id):
        record = self.storage.get_conversation_record(conversation_id)
        if not record:
            raise ValueError("对话不存在。")
        path = record["meta"].get("workspace_dir") or record["meta"].get("workspace_path")
        if not path:
            # Old chat-only histories have no project. Resolve their established
            # per-conversation workspace without rewriting any historical row.
            path = Path(self.config.get_chat_workspace_root()) / conversation_id
        return Path(path).resolve()

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
            context = {**payload.get("runtime_context", {}), "selected_model_profile": profile}
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
            try:
                result = self.workbench.agent_request(identity, claim)
            except Exception as exc:
                log.exception("workbench_action_failed session=%s action=%s", identity["session_id"], claim["action"])
                result = {"ok": False, "status": getattr(exc, "code", "failed"), "error": str(exc)}
            self.request_agent({**identity, "token": claim["token"], "action": "workbench_result", "result": result})
        except Exception:
            log.exception("workbench_claim_failed session=%s", identity["session_id"])

    def dispatch(self, method, params):
        if method == "conversation.send":
            with self.lock:
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
        if method in self.workbench.methods:
            return self.workbench.dispatch(method, p)
        if method in self.conversations.methods:
            return self.conversations.dispatch(method, p)
        if method in self.management.methods:
            return self.management.dispatch(method, p)
        if method in self.settings_pages.methods:
            return self.settings_pages.dispatch(method, p)
        if method in self.messaging.methods:
            return self.messaging.dispatch(method, p)
        if method in self.updates.methods:
            return self.updates.dispatch(method, p)
        if method in self.models.methods:
            return self.models.dispatch(method, p)
        if method in self.skillhub.methods:
            return self.skillhub.dispatch(method, p)
        if method in self.connections.methods:
            return self.connections.dispatch(method, p)
        cid = str(p.get("conversation_id") or "")
        if method == "app.bootstrap":
            return {"protocol": PROTOCOL_VERSION, "settings": self.public_settings(),
                    "conversations": self.storage.list_conversation_summaries(limit=500),
                    "active_runs": [self.public_run(run) for run in self.store.active_runs()],
                    "last_conversation": self.store.get_state("last_conversation", ""),
                    "home_draft": self.store.versioned_state("draft:", {"text": "", "attachments": []})}
        if method == "conversation.create":
            cid = uuid.uuid4().hex
            root = Path(p.get("workspace_dir") or (Path(self.config.get_chat_workspace_root()) / cid)).resolve()
            root.mkdir(parents=True, exist_ok=True)
            self.storage.upsert_conversation(cid, title="新对话", meta={"workspace_dir": str(root), "workspace_path": str(root),
                "workspace_source": "project" if p.get("workspace_dir") else "chat", "desktop": True})
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid)
        if method == "conversation.list":
            return self.storage.list_conversation_summaries(limit=min(500, max(1, int(p.get("limit", 500)))), offset=max(0, int(p.get("offset", 0))))
        if method == "conversation.get":
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid, before=p.get("before"), limit=p.get("limit", 80))
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
            if cid:
                self.workspace(cid)
            return self.store.put_state(("draft:" if method == "draft.save" else "view:") + cid, p["value"], p.get("revision"))
        if method == "view.get":
            self.workspace(cid)
            return self.store.get_state("view:" + cid, {})
        if method == "conversation.receipt":
            run = self.store.get_run(str(p["request_id"]))
            if run and run["conversation_id"] != cid:
                raise ValueError("发送记录不属于当前对话。")
            rejected = self.store.get_state("submission_rejected:" + str(p["request_id"]))
            return {"accepted": bool(run), "rejected": bool(rejected and rejected["conversation_id"] == cid), "run": self.public_run(run), "draft": self.store.versioned_state("draft:" + cid, {"text": "", "attachments": []})}
        if method == "conversation.send":
            with self.lock:
                if self.closing:
                    raise ValueError("应用正在退出，输入内容已保留。")
                input_hash = hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                existing = self.store.get_run(str(p["request_id"]))
                if existing:
                    accepted = json.loads(existing["payload"])
                    if existing["conversation_id"] != cid or accepted.get("input_hash") != input_hash:
                        raise ValueError("请求标识已用于另一条消息，请重新发送。")
                    return {"accepted": True, "run": self.public_run(existing), "draft": self.store.versioned_state("draft:" + cid)}
                profile = self.config.get_model_profile(p.get("model_id"))
                if not profile or not profile.get("api_key"):
                    raise ValueError("请先在“模型与服务”中填写 API Key，或导入现有设置。输入内容已保留。")
                text = str(p.get("text") or "").strip()
                attachments = p.get("attachments") or []
                if not text and not attachments:
                    raise ValueError("请输入消息或添加附件。")
                from core.desktop_submission import user_payload, run_context
                record = self.storage.get_conversation_record(cid)
                if not record:
                    raise ValueError("对话不存在。")
                from core.desktop_conversations import PREFERENCE_KEYS
                preferences = {key: value for key, value in {**record["meta"], **(p.get("preferences") or {})}.items() if key in PREFERENCE_KEYS}
                workspace = self.workspace(cid)
                if not workspace.is_dir():
                    if record["meta"].get("workspace_source") == "chat" or not (record["meta"].get("workspace_dir") or record["meta"].get("workspace_path")):
                        workspace.mkdir(parents=True, exist_ok=True)
                    else:
                        raise ValueError("原项目目录暂时不可用，请恢复该目录后继续。输入和历史记录已保留。")
                prior_attachments = set()
                if p.get("rewrite"):
                    source = next((message for message in self.storage.get_messages(cid) if message["id"] == p["rewrite"].get("message_id")), {})
                    prior_attachments = {str(Path(path).resolve()) for path in (source.get("meta") or {}).get("user_added_files", [])}
                paths = []
                for item in attachments:
                    path = str(Path(item["path"]).resolve())
                    paths.append(path if path in prior_attachments else str(self.checked_file(cid, path)))
                message = user_payload(text, paths, workspace=self.workspace(cid), supports_vision=profile.get("supports_vision", False),
                    keep_audio_local=bool(preferences.get("keep_audio_local")),
                    audio_transcription_backend=preferences.get("audio_transcription_backend", "local"),
                    references=preferences.get("conversation_refs", []), branch_file_map=record["meta"].get("branch_file_map"))
                from core.enterprise_client import submission
                question = submission(profile, text)
                message_meta = {**(message.get("meta") or {})}
                if "conversation_agent" in preferences:
                    message_meta["conversation_agent"] = preferences["conversation_agent"]
                if question:
                    message_meta["enterprise_submission"] = question
                payload = {"conversation_id": cid, "request_id": str(p["request_id"]),
                           "message_id": str(p["message_id"]), "content": message["content"], "display_text": text,
                           "workspace_dir": str(self.workspace(cid)), "model_id": profile["id"],
                           "skills": p.get("skills", preferences.get("selected_skill_names", [])), "attachments": attachments,
                           "preferences": preferences, "input_hash": input_hash, "draft_revision": p.get("draft_revision"),
                           "content_parts": message.get("content_parts"), "message_meta": message_meta, "rewrite": p.get("rewrite")}
                if payload["rewrite"]:
                    self.conversations.idle(cid)
                payload["runtime_context"] = run_context(self, cid, payload, profile)
                run, created = self.store.accept(payload)
                log.info("message_accepted session=%s run=%s created=%s", cid, run["id"], created)
                if created:
                    self.start_stream(payload, copy.deepcopy(profile))
                return {"accepted": True, "run": self.public_run(run), "draft": self.store.versioned_state("draft:" + cid),
                        "undo": run["id"] if payload.get("rewrite") else None}
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
            run = self.store.get_run(str(p["run_id"]))
            if not run or run["conversation_id"] != cid:
                raise ValueError("任务不属于当前对话。补充内容已保留。")
            accepted = json.loads(run["payload"])
            profile = self.config.get_model_profile(accepted["model_id"])
            if not profile:
                raise ValueError("本轮模型配置已不可用，请停止后重新选择模型。补充内容已保留。")
            preferences = accepted.get("preferences") or {}
            from core.desktop_submission import user_payload
            from core.enterprise_client import submission
            text = str(message.get("content") or "")
            attachments = p.get("attachments") or []
            built = user_payload(text, [str(self.checked_file(cid, item["path"])) for item in attachments],
                workspace=self.workspace(cid), supports_vision=profile.get("supports_vision", False),
                keep_audio_local=bool(preferences.get("keep_audio_local")),
                audio_transcription_backend=preferences.get("audio_transcription_backend", "local"))
            message.update(content=built["content"], content_parts=built["content_parts"])
            message["meta"] = {**(message.get("meta") or {}), **(built.get("meta") or {}), "desktop_attachments": attachments}
            question = submission(profile, text)
            if question:
                message["meta"]["enterprise_submission"] = question
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
            api_key = str(p.get("api_key") or "").strip() or existing.get("api_key", "")
            model_id = str(p.get("model_id") or uuid.uuid4().hex)
            previous_model = next((model for model in existing.get("models", []) if model.get("id") == model_id), {})
            model = {**previous_model, "id": model_id, "model_name": p["model_name"], "display_name": p.get("display_name") or p["model_name"],
                     "api_protocol": p.get("api_protocol") or previous_model.get("api_protocol") or "chat_completions"}
            for key in ("supports_vision", "supports_tools", "supports_reasoning", "reasoning_effort", "context_length", "max_tokens", "temperature"):
                if key in p:
                    model[key] = p[key]
            models = [model if item.get("id") == model_id else item for item in existing.get("models", [])]
            if not previous_model:
                models.append(model)
            channel = {**existing, "channel_id": channel_id, "display_name": p.get("name") or existing.get("display_name") or "模型服务",
                       "provider_type": p.get("provider_type") or existing.get("provider_type") or "openai", "base_url": p["base_url"], "api_key": api_key,
                       "models": models}
            channels = [channel if item.get("channel_id") == channel_id else item for item in channels]
            if not existing:
                channels.append(channel)
            self.config.set_model_channels(channels, model_id)
            return self.public_settings()
        if method == "settings.select_model":
            self.config.set_selected_model_id(p["model_id"])
            return self.public_settings()
        if method == "settings.permission":
            self.config.set_god_mode(bool(p["enabled"]))
            return self.public_settings()
        if method == "settings.theme":
            appearance = self.management.themes.load_appearance()
            self.management.themes.save_appearance({**appearance, "mode": p["theme"]})
            self.publish({"event": "appearance_changed"})
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
        if method == "file.describe":
            from core.deliverable_editing import sha256_file
            path = self.checked_file(cid, p["path"])
            return {"path": str(path), "fingerprint": sha256_file(path)}
        if method == "file.open":
            from core.deliverable_editing import create_edit_session, load_editor_payload
            path = self.checked_file(cid, p["path"])
            from core.file_capabilities import file_capability
            capability = file_capability(str(path))
            if capability and not capability.editable:
                from core.deliverable_editing import sha256_file
                payload = {"kind": "preview", "preview_kind": capability.preview_kind, "read_only": True}
                if capability.preview_kind == "pptx":
                    from core.deliverable_preview import render_pptx_preview
                    payload["preview_html"] = render_pptx_preview(str(path))["html"]
                return {"id": uuid.uuid4().hex, "path": str(path), "name": path.name,
                        "fingerprint": sha256_file(path), "payload": payload}
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
        if method in {"file.save", "file.save_copy"}:
            from core.deliverable_editing import serialize_editor_payload, atomic_save_session
            owner, session = self.file_sessions.get(p["id"], (None, None))
            if owner != cid or session is None:
                raise ValueError("编辑会话已失效，请保留内容并重新打开文件。")
            payload = p["payload"]
            if isinstance(payload, dict) and set(payload) == {"kind", "value"}:
                kind = payload["kind"]
                expected = "docx_review" if session.metadata.get("docx_review") else session.descriptor.kind
                if kind != expected:
                    raise ValueError("编辑器类型与文件不匹配。")
                payload = base64.b64decode(payload["value"], validate=True) if kind == "docx" else json.loads(payload["value"])
            data = serialize_editor_payload(session, payload)
            if method == "file.save_copy":
                import tempfile
                destination = self.checked_file(cid, p["name"])
                if destination.suffix.lower() != Path(session.path).suffix.lower():
                    raise ValueError("副本必须保留原文件格式。")
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".cowork-copy-", delete=False) as handle:
                        temporary = handle.name
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Atomically publish the complete file, never replace an
                    # existing destination or touch the changed source.
                    os.link(temporary, destination)
                finally:
                    if temporary:
                        os.unlink(temporary)
                log.info("file_copy_saved session=%s file_session=%s", cid, p["id"])
                return self.dispatch("file.open", {"conversation_id": cid, "path": str(destination)})
            result = atomic_save_session(session, data, event_logger=lambda event, **details: log.info("file_save session=%s file_session=%s stage=%s", cid, p["id"], event))
            return {"saved": True, "fingerprint": result.fingerprint, "path": result.path}
        if method == "skills.list":
            snapshot = self.state.skill_catalog.snapshot()
            return [{key: skill.get(key) for key in ("name", "display_name", "description", "enabled")} for skill in snapshot.display_skills()]
        if method == "workbench.result":
            return self.request_agent({**p, "action": "workbench_result"})
        if method == "app.shutdown":
            with self.lock:
                self.closing = True
            for run in self.store.active_runs():
                try:
                    self.dispatch("conversation.stop", {"conversation_id": run["conversation_id"], "run_id": run["id"]})
                except Exception:
                    log.exception("shutdown_stop_failed run=%s", run["id"])
            deadline = time.monotonic() + 15
            while self.streams and time.monotonic() < deadline:
                time.sleep(.05)
            if self.streams:
                self.closing = False
                raise RuntimeError("任务仍在结束处理中，请稍后再退出。")
            self.scheduler.close()
            self.messaging.close()
            self.workbench.close()
            self.jobs.close()
            self.models.close()
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
        publish = transport.publish
    try:
        service = DesktopService(publish)
    except BaseException:
        lease.close()
        raise
    if transport:
        transport.start(service)
        service.scheduler.start()
        service.messaging.restart()
        try:
            while not service.closing or service.streams:
                time.sleep(.1)
        finally:
            service.scheduler.close()
            service.jobs.close()
            transport.close()
            service.server.shutdown()
            service.state.skill_catalog.stop_watching()
            lease.close()
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
            service.closing = True
            if service.streams:
                log.warning("host_disconnected stopping_active_runs=true")
                try:
                    service.dispatch("app.shutdown", {})
                except Exception:
                    log.exception("host_disconnected_stop_failed")
    threading.Thread(target=read_commands, name="desktop-commands", daemon=True).start()
    publish({"event": "ready", "protocol": PROTOCOL_VERSION})
    service.scheduler.start()
    while not service.closing or service.streams:
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
