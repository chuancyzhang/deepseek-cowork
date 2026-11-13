"""Conversation-owned native workspaces. Renderer reconnect never restarts shells."""
import logging
import re
import threading
import base64
import json
import os
import shutil
import uuid
from pathlib import Path

from core.workbench import WorkbenchError, WorkbenchStore
from core.workbench_terminal import TerminalSession, terminal_profiles

log = logging.getLogger("desktop.workbench")


class DesktopWorkbench:
    methods = {"workbench.list", "workbench.profiles", "workbench.open", "workbench.close",
               "workbench.control", "workbench.read", "workbench.input", "workbench.reply", "workbench.resize", "workbench.activate"}

    def __init__(self, service):
        self.s = service
        self.store = WorkbenchStore()
        self.terminals = {}
        self.lock = threading.RLock()
        from core.desktop_browser import DesktopBrowser
        self.browser = DesktopBrowser(self)
        self.methods = self.methods | self.browser.methods

    def changed(self, cid):
        self.s.publish({"event": "workbench_changed", "conversation_id": cid})

    def snapshot(self, cid):
        with self.lock:
            tabs = []
            for tab in self.store.list(cid):
                if tab.id in self.terminals:
                    tab.status = self.terminals[tab.id].status
                tabs.append(tab.snapshot())
            return {"ok": True, "tabs": tabs, "active_tab_id": self.store.active.get(cid, "")}

    def open_terminal(self, cid, args, human=False):
        if self.s.closing:
            raise ValueError("后台正在退出，终端尚未启动。")
        profiles = terminal_profiles()
        profile = next((item for item in profiles if item["id"] == args.get("shell")), None)
        if not profile and args.get("shell"):
            raise WorkbenchError("shell_unavailable", "所选终端不可用，请选择已安装的 Shell。")
        profile = profile or (profiles[0] if profiles else None)
        if not profile:
            raise WorkbenchError("shell_unavailable", "没有可用的 Shell，请先安装 PowerShell 或 Bash。")
        cwd = Path(args.get("cwd") or self.s.workspace(cid))
        if not cwd.is_absolute():
            cwd = self.s.workspace(cid) / cwd
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise WorkbenchError("cwd_missing", "终端启动目录不存在。")
        tab = self.store.open(cid, "terminal", activate=human, title=profile["name"], cwd=str(cwd), shell=profile["id"], status="starting")
        terminal = TerminalSession(cid, tab.id, profile, str(cwd))
        self.terminals[tab.id] = terminal
        self.store.set_owner(cid, tab.id, "human" if human else "agent")
        terminal.start()
        self.changed(cid)
        return {"ok": True, "tab": tab.snapshot()}

    def close_tab(self, cid, tab_id):
        tab = self.store.get(cid, tab_id)
        terminal = self.terminals.pop(tab.id, None)
        if terminal:
            terminal.close()
        self.store.close(cid, tab.id)
        self.changed(cid)
        log.info("tab_closed session=%s tab=%s kind=%s", cid, tab_id, tab.kind)
        return {"ok": True}

    def dispatch(self, method, p):
        if method in self.browser.methods:
            return self.browser.dispatch(method, p)
        cid = str(p.get("conversation_id") or "")
        if not self.s.storage.get_conversation_record(cid):
            raise ValueError("对话不存在。")
        with self.lock:
            if method == "workbench.profiles":
                return [{"id": item["id"], "name": item["name"]} for item in terminal_profiles()]
            if method == "workbench.list":
                return self.snapshot(cid)
            if method == "workbench.open":
                if p.get("kind", "terminal") != "terminal":
                    raise ValueError("请使用对应的网页或文件入口。")
                return self.open_terminal(cid, p, human=True)
            tab = self.store.get(cid, p.get("tab_id"))
            if method == "workbench.control":
                owner = p.get("owner")
                if owner not in {"human", "available"}:
                    raise ValueError("无效的控制权。")
                self.store.set_owner(cid, tab.id, owner)
                self.changed(cid)
                return tab.snapshot()
            if method == "workbench.activate":
                self.store.active[cid] = tab.id
                return {"ok": True}
            if method == "workbench.close":
                return self.close_tab(cid, tab.id)
            terminal = self.terminals.get(tab.id)
            if not terminal:
                raise ValueError("目标不是终端。")
            if method == "workbench.read":
                return terminal.read(p.get("cursor", 0), p.get("limit", 32768))
            if method == "workbench.resize":
                terminal.resize(p.get("rows", 24), p.get("cols", 80))
            elif method == "workbench.reply":
                text = str(p.get('text') or '')
                if not re.fullmatch(r'\x1b\[(?:\??[0-9;]*c|[0-9;]*[Rn])', text):
                    raise ValueError('无效的终端协议应答。')
                if terminal.status == 'running':
                    terminal.write(text)
            elif method == "workbench.input":
                if tab.owner != "human" or p.get("control_revision") != tab.control_revision:
                    raise WorkbenchError("control_changed", "请先接管终端，再输入命令。")
                terminal.write(str(p.get("text") or ""))
            else:
                raise ValueError("不支持的工作区操作。")
            return {"ok": True}

    def agent_request(self, identity, claim):
        """Claims are trusted daemon snapshots; never accept them from renderer RPC."""
        cid, action, args = identity["session_id"], claim["action"], claim["arguments"]
        internal = (claim.get('policy', {}).get('browser_backend') or {}).get('backend') == 'internal'
        if action in {'observe', 'navigate', 'browser_action'} or (action == 'open' and args.get('kind') == 'web') or (action == 'close' and self.store.get(cid, args.get('tab_id')).kind == 'web'):
            if not internal:
                raise WorkbenchError('browser_backend_changed', '本轮使用外部浏览器，不能自动切换为内置网页。')
            result = self.browser.execute(cid, action, args, {**identity, 'token': claim['token']})
            if not claim.get('model_supports_vision'):
                result.pop('screenshot', None)
            return result
        with self.lock:
            active = self.s.request_agent({**identity, "token": claim["token"], "action": "workbench_active"})
            if active.get("status") != "ok":
                return {"ok": False, "status": "cancelled", "executed": False, "error": "请求已取消，本次操作未执行。"}
            if action == "list":
                snapshot = self.snapshot(cid)
                if not internal:
                    snapshot['tabs'] = [tab for tab in snapshot['tabs'] if tab['kind'] != 'web']
                return snapshot
            if action == "open" and args.get("kind") == "terminal":
                return self.open_terminal(cid, args)
            tab = self.store.get(cid, args.get("tab_id"))
            terminal = self.terminals.get(tab.id)
            if not terminal:
                raise WorkbenchError("invalid_kind", "目标不是终端。")
            if action == "terminal_read":
                return {"ok": True, **terminal.read(args.get("cursor", 0), args.get("limit", 32768))}
            self.store.check_control(cid, tab.id)
            self.store.set_owner(cid, tab.id, "agent")
            if action == "close":
                return self.close_tab(cid, tab.id)
            if action == "terminal_input":
                terminal.write(str(args.get("text") or ""))
            elif action == "terminal_interrupt":
                terminal.write("\x03")
            elif action == "terminal_resize":
                terminal.resize(args.get("rows", 24), args.get("cols", 80))
            else:
                raise WorkbenchError("invalid_action", "操作与标签类型不匹配。")
            self.changed(cid)
            return {"ok": True}

    def close(self):
        with self.lock:
            for terminal in self.terminals.values():
                terminal.close()
            self.terminals.clear()


class DesktopFiles:
    methods = frozenset(['workspace.list', 'workspace.attach', 'workspace.create', 'file.describe', 'file.open', 'file.save', 'file.save_copy',
                         'file.render', 'file.render_result', 'file.install_renderer'])

    def __init__(self, storage, config, jobs=None):
        self.storage, self.config = storage, config
        self.file_sessions = {}
        self.jobs = jobs

    def dispatch(self, method, p):
        cid = str(p.get("conversation_id") or "")
        if method == "file.install_renderer":
            self.workspace(cid)
            from core.libreoffice_component import install
            return self.jobs.submit("安装 LibreOffice", install)
        if method == "file.render":
            from core.document_preview import render_office_pdf
            from core.deliverable_editing import sha256_file
            path = self.checked_file(cid, p["path"])
            expected = str(p.get("fingerprint") or "")
            if path.suffix.lower() not in {".doc", ".docx", ".ppt", ".pptx"}:
                raise ValueError("此文件不使用文档版式预览。")
            def render(cancelled, progress):
                if expected and sha256_file(path) != expected:
                    raise ValueError("文件已变化，请刷新后再预览。")
                progress("正在生成页面预览…")
                try:
                    preview, fingerprint = render_office_pdf(path, cancel=cancelled, renderer="libreoffice")
                except WorkbenchError as exc:
                    if cancelled.is_set():
                        from core.desktop_jobs import JobCancelled
                        raise JobCancelled(str(exc)) from exc
                    raise
                log.info("file_preview_completed session=%s version=%s", cid, fingerprint[:12])
                return {"kind": "office_preview", "conversation_id": cid, "path": str(path),
                        "preview_path": preview, "fingerprint": fingerprint}
            return self.jobs.submit("生成文档预览", render)
        if method == "file.render_result":
            job = self.jobs.get(str(p["job_id"]))
            result = job.get("result") or {}
            if job["status"] != "completed" or result.get("kind") != "office_preview" or result.get("conversation_id") != cid:
                raise ValueError("文档预览尚未完成或不属于当前对话。")
            path = self.checked_file(cid, result["path"])
            return {"id": job["id"], "path": str(path), "name": path.name, "fingerprint": result["fingerprint"],
                    "payload": {"kind": "preview", "preview_kind": "pdf", "read_only": True, "preview_path": result["preview_path"]}}
        if method == "workspace.list":
            root = self.workspace(cid)
            folder = self.checked_file(cid, p.get("path") or ".")
            if not folder.is_dir():
                log.warning('workspace_unavailable session=%s', cid)
                raise ValueError("工作目录暂时不可用：" + str(folder) + "。请恢复目录，或重新选择工作目录；已打开的内容仍保留。")
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
            self.storage.update_conversation_meta(cid, {"has_file_changes": True})
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
            if capability and capability.office_family in {"word", "powerpoint"}:
                from core.libreoffice_component import status
                from core.deliverable_editing import sha256_file
                session_id = uuid.uuid4().hex
                recovery = bool(p.get("recover_draft") and capability.editable)
                if recovery:
                    # Old unsaved Word drafts remain exportable after inline editing retires.
                    session, report = create_edit_session(str(path))
                    if not report.allowed:
                        raise ValueError(report.message)
                    load_editor_payload(session)
                    session.metadata["recovery_only"] = True
                    self.file_sessions[session_id] = (cid, session)
                return {"id": session_id, "path": str(path), "name": path.name, "fingerprint": sha256_file(path),
                        "payload": {"kind": "preview", "preview_kind": "office", "office_family": capability.office_family,
                                    "read_only": True, "recovery_available": recovery, "renderer": status()}}
            if capability and not capability.editable:
                from core.deliverable_editing import sha256_file
                payload = {"kind": "preview", "preview_kind": capability.preview_kind, "read_only": True}
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
            if session.metadata.get("recovery_only") and method != "file.save_copy":
                raise ValueError("Word 编辑已改为本机应用，请先将恢复草稿另存副本。")
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
        raise ValueError("不支持的操作：" + method)

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
