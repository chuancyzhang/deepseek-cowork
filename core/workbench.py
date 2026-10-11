"""Runtime-only, conversation-owned browser tabs and cancellable desktop RPC.

The registry is not conversation history. Only the trusted execution context can
select a conversation; tool arguments cannot select another task or native host.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class WorkbenchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


@dataclass
class WorkbenchTab:
    id: str
    session_id: str
    kind: str
    title: str = "新标签页"
    path: str = ""
    url: str = ""
    cwd: str = ""
    shell: str = ""
    owner: str = "available"
    control_revision: int = 0
    page_revision: int = 0
    status: str = "ready"
    view_state: dict = field(default_factory=dict)

    def snapshot(self):
        return {key: getattr(self, key) for key in (
            "id", "kind", "title", "path", "url", "cwd", "shell", "owner",
            "control_revision", "page_revision", "status",
        )}


class WorkbenchStore:
    def __init__(self):
        self.tabs: dict[str, WorkbenchTab] = {}
        self.order: dict[str, list[str]] = {}
        self.active: dict[str, str] = {}
        self.lock = threading.RLock()

    def list(self, session_id):
        with self.lock:
            return [self.tabs[key] for key in self.order.get(session_id, [])]

    def get(self, session_id, tab_id):
        with self.lock:
            tab = self.tabs.get(str(tab_id))
            if tab is None or tab.session_id != session_id:
                raise WorkbenchError("tab_unavailable", "标签已关闭或不属于当前聊天，请重新列出标签。")
            return tab

    def open(self, session_id, kind, *, activate=False, **values):
        if not session_id or kind not in {"new", "file", "web", "terminal"}:
            raise WorkbenchError("invalid_tab", "无效的聊天或标签类型。")
        with self.lock:
            if kind == "file":
                path = os.path.abspath(values.get("path") or "")
                for tab in self.list(session_id):
                    if tab.kind == "file" and path_key(tab.path) == path_key(path):
                        if activate:
                            self.active[session_id] = tab.id
                        return tab
                values["path"] = path
                values.setdefault("title", os.path.basename(path))
            tab = WorkbenchTab(uuid.uuid4().hex, session_id, kind, **values)
            self.tabs[tab.id] = tab
            self.order.setdefault(session_id, []).append(tab.id)
            if activate or not self.active.get(session_id):
                self.active[session_id] = tab.id
            return tab

    def close(self, session_id, tab_id):
        with self.lock:
            tab = self.get(session_id, tab_id)
            tab.control_revision += 1
            order = self.order[session_id]
            index = order.index(tab_id)
            order.remove(tab_id)
            del self.tabs[tab_id]
            if self.active.get(session_id) == tab_id:
                self.active[session_id] = order[min(index, len(order) - 1)] if order else ""
            return self.active.get(session_id, "")

    def set_owner(self, session_id, tab_id, owner):
        if owner not in {"available", "human", "agent"}:
            raise ValueError(owner)
        with self.lock:
            tab = self.get(session_id, tab_id)
            if tab.owner != owner:
                if "human" in {tab.owner, owner}:
                    tab.page_revision += 1
                tab.owner = owner
                tab.control_revision += 1
            return tab.control_revision

    def check_control(self, session_id, tab_id, revision=None):
        with self.lock:
            tab = self.get(session_id, tab_id)
            if tab.owner == "human":
                raise WorkbenchError("human_control", "用户已接管。等待用户交还 Agent 后重新观察，不能自动重放操作。")
            if revision is not None and tab.control_revision != revision:
                raise WorkbenchError("control_changed", "控制权已变化，本次操作未执行，请重新观察。")
            return tab


@dataclass
class WorkbenchRequest:
    session_id: str
    run_id: str
    action: str
    arguments: dict
    context: dict
    deadline: float
    revision: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    cancelled: bool = False
    started: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def check(self):
        from .tool_cancellation import abort_requested
        if self.cancelled or abort_requested(self.context):
            raise WorkbenchError("cancelled", "操作已取消；已完成的网页请求或终端命令不会被自动撤销。")
        if time.monotonic() > self.deadline:
            raise WorkbenchError("timeout", "浏览器操作等待超时，请检查页面或终端状态后继续。")
        auth = self.context.get("execution_authorization")
        if auth is not None:
            auth.check()

    def finish(self, result):
        with self._lock:
            if not self.event.is_set():
                self.result = result
                self.event.set()

    def fail(self, exc):
        self.finish({"ok": False, "status": getattr(exc, "code", "failed"),
                     "error": str(exc), "executed": self.started})


class WorkbenchBridge(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.store = WorkbenchStore()
        self.available = False
        self.pending: dict[str, WorkbenchRequest] = {}
        self.remote_routes = {}
        self._lock = threading.RLock()

    def call(self, action, arguments, context, timeout=30):
        from .execution_authorization import current_authorization, ExecutionAuthorization
        context = dict(context or {})
        authorization = current_authorization(context)
        if type(authorization) is not ExecutionAuthorization:
            return {"ok": False, "status": "unavailable", "error": "缺少可信的运行上下文。", "executed": False}
        try:
            authorization.check()
            with self._lock:
                remote = self.remote_routes.get((authorization.session_id, authorization.run_id))
            if not self.available and remote:
                return remote(action, arguments, context, timeout)
            if not self.available:
                raise WorkbenchError("unavailable", "当前运行环境没有已连接的桌面浏览器。")
            context["execution_authorization"] = authorization
            request = WorkbenchRequest(authorization.session_id, authorization.run_id, action,
                                       dict(arguments or {}), context, time.monotonic() + timeout)
            tab_id = request.arguments.get("tab_id")
            if tab_id:
                request.revision = self.store.get(request.session_id, tab_id).control_revision
            with self._lock:
                self.pending[request.id] = request
            log.info("workbench.request session=%s run=%s request=%s action=%s tab=%s",
                     request.session_id, request.run_id, request.id, action, tab_id or "")
            self.requested.emit(request)
            try:
                while not request.event.wait(0.1):
                    request.check()
                return request.result
            except Exception as exc:
                request.cancelled = True
                request.fail(exc)
                return request.result
            finally:
                with self._lock:
                    self.pending.pop(request.id, None)
                log.info("workbench.finish session=%s request=%s status=%s", request.session_id,
                         request.id, (request.result or {}).get("status", "completed"))
        except Exception as exc:
            return {"ok": False, "status": getattr(exc, "code", "failed"), "error": str(exc), "executed": False}

    def cancel_tab(self, session_id, tab_id, *, exclude_request=None):
        with self._lock:
            for request in self.pending.values():
                if request is not exclude_request and request.session_id == session_id and request.arguments.get("tab_id") == tab_id:
                    request.cancelled = True
                    request.fail(WorkbenchError("human_control", "用户已接管或关闭标签；后续操作已停止，请勿重放。"))

    def shutdown(self):
        self.available = False
        with self._lock:
            for request in self.pending.values():
                request.cancelled = True
                request.fail(WorkbenchError("unavailable", "桌面浏览器已关闭。"))


_bridge = None
_bridge_lock = threading.Lock()


def workbench_bridge():
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = WorkbenchBridge()
        return _bridge
