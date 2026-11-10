"""Conversation-owned native workspaces. Renderer reconnect never restarts shells."""
import logging
import re
import threading
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
