"""Run-owned authorization. Tool metadata and model arguments are never grants.

This is an execution approval boundary, not an operating-system sandbox. Opaque
calls are approved as whole executions, including their child processes.
"""
from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


_current = contextvars.ContextVar("execution_authorization", default=None)
_invocation = contextvars.ContextVar("authorized_invocation", default=None)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exact, application-owned implementations only. Never infer trust from names.
_READERS = {
    "file-system": {"workspace_list_files", "text_file_read", "glob", "grep"},
    "command-tools": {"glob", "grep"},
    "history-query": {"query_history", "query_history_vector"},
    "memory-manager": {"read_memories"},
}
_CONTROL = {
    "interaction": {"request_user_input", "request_user_approval"},
    "meta-tools": {"parallel_tools"},
    "agent-manager": {"spawn_agent", "send_input", "wait_agent", "close_agent", "list_agents"},
}
_FILES = {"apply_patch", "workspace_rename_path", "workspace_delete_path"}


class AuthorizationError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ExecutionAuthorization:
    session_id: str
    run_id: str
    god_mode: bool
    artifact_root: str
    policy_version: int = 1
    _cancelled: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    @classmethod
    def start(cls, config, session_id, run_id="", workspace_dir=None):
        identity = uuid.uuid4().hex
        mode = bool(config.get_god_mode()) if config is not None else False
        root = ""
        if not mode:
            base = workspace_dir
            if not isinstance(base, (str, os.PathLike)) or not str(base).strip():
                chat_root = config.get_chat_workspace_root() if config is not None else None
                session_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or identity))
                base = os.path.join(chat_root, session_key) if isinstance(chat_root, (str, os.PathLike)) else None
            if not isinstance(base, (str, os.PathLike)) or not str(base).strip():
                raise ValueError("无法确定任务产物目录。")
            root = os.path.abspath(os.path.join(base, "artifacts", identity))
        return cls(str(session_id or ""), str(run_id or identity), mode, root)

    def cancel(self):
        self._cancelled.set()

    def runtime_snapshot(self):
        """Host IPC metadata only; never contains a consumable approval grant."""
        return {"session_id": self.session_id, "run_id": self.run_id,
                "god_mode": self.god_mode, "artifact_root": self.artifact_root,
                "policy_version": self.policy_version, "cancelled": self._cancelled.is_set()}

    def check(self):
        if self._cancelled.is_set():
            raise AuthorizationError("cancelled", "任务已停止，本次操作未执行。")


def authorization_from_host_snapshot(payload, *, session_id, run_id):
    """Accept only a matching daemon transport envelope, never model/context data.

    This transfers the run's policy to the desktop continuation. Off-mode actions
    still need fresh approvals; neither messages nor journal recovery call this.
    """
    if (not isinstance(payload, dict) or payload.get("session_id") != session_id
            or payload.get("run_id") != run_id or type(payload.get("god_mode")) is not bool
            or payload.get("policy_version") != 1 or type(payload.get("cancelled")) is not bool):
        raise ValueError("后台运行权限身份无效。")
    root = payload.get("artifact_root")
    if not isinstance(root, str) or (not payload["god_mode"] and not os.path.isabs(root)):
        raise ValueError("后台任务产物目录无效。")
    authorization = ExecutionAuthorization(session_id, run_id, payload["god_mode"], root)
    if payload["cancelled"]:
        authorization.cancel()
    return authorization


def current_authorization(context=None):
    value = (context or {}).get("execution_authorization")
    if type(value) is ExecutionAuthorization:
        return value
    return _current.get()


@contextmanager
def bind_authorization(authorization):
    token = _current.set(authorization if type(authorization) is ExecutionAuthorization else None)
    try:
        yield
    finally:
        _current.reset(token)


def _source(handler):
    try:
        from .skill_manager import _LazySkillHandler
        if type(handler) is _LazySkillHandler:
            return os.path.normcase(os.path.realpath(handler.impl_path))
        return os.path.normcase(os.path.realpath(inspect.getsourcefile(handler) or ""))
    except (TypeError, OSError):
        return ""


def trusted_kind(name, handler):
    source = _source(handler)
    expected_name = {"tool_search": "_tool_search", "lookup_app_variable": "_lookup_app_variable"}.get(name, name)
    if getattr(handler, "__name__", "") != expected_name:
        return "opaque"
    if name == "document_read" and source == os.path.normcase(os.path.realpath(os.path.join(_ROOT, "ai_skills", "document-reader", "impl.py"))):
        return "read"
    for skill, names in _READERS.items():
        if name in names and source == os.path.normcase(os.path.realpath(os.path.join(_ROOT, "skills", skill, "impl.py"))):
            return "read"
    for skill, names in _CONTROL.items():
        if name in names and source == os.path.normcase(os.path.realpath(os.path.join(_ROOT, "skills", skill, "impl.py"))):
            return "control"
    if name in {"tool_search", "lookup_app_variable"} and source == os.path.normcase(os.path.realpath(os.path.join(_ROOT, "core", "skill_manager.py"))):
        return "read"
    if name in _FILES and source == os.path.normcase(os.path.realpath(os.path.join(_ROOT, "skills", "file-system", "impl.py"))):
        return "file"
    return "opaque"


def _inside(root, path):
    try:
        return os.path.normcase(os.path.commonpath([root, path])) == os.path.normcase(root)
    except ValueError:
        return False


def _path(path, cwd):
    return os.path.abspath(os.path.join(cwd, str(path)))


def _signature(path):
    # Inspect ancestors as well: approval cannot follow a replaced junction.
    ancestors = []
    parent = path
    while True:
        if os.path.lexists(parent):
            info = os.lstat(parent)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise AuthorizationError("denied", "目标路径经过符号链接或目录联接，操作未执行。")
            ancestors.append((parent, info.st_dev, info.st_ino))
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent
    if not os.path.lexists(path):
        return ("missing", ancestors)
    info = os.stat(path)
    digest = ""
    if stat.S_ISREG(info.st_mode):
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
    elif stat.S_ISDIR(info.st_mode):
        h = hashlib.sha256()
        pending = [path]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(entry_stat.st_mode) or getattr(entry_stat, "st_file_attributes", 0) & 0x400:
                    raise AuthorizationError("denied", "目录包含符号链接或目录联接，操作未执行。")
                h.update(repr((os.path.relpath(entry.path, path), entry_stat.st_mode, entry_stat.st_size,
                               entry_stat.st_mtime_ns, entry_stat.st_ino)).encode("utf-8"))
                if stat.S_ISDIR(entry_stat.st_mode):
                    pending.append(entry.path)
                elif stat.S_ISREG(entry_stat.st_mode):
                    with open(entry.path, "rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            h.update(chunk)
        digest = h.hexdigest()
    return (info.st_mode, info.st_size, info.st_mtime_ns, digest, ancestors)


@dataclass(frozen=True)
class PreparedAction:
    name: str
    kind: str
    fingerprint: str
    details: str
    targets: tuple
    state: tuple
    free_write: bool


@dataclass(frozen=True)
class AuthorizationDecision:
    outcome: str
    reason: str = ""


@dataclass
class ApprovalGrant:
    authorization: ExecutionAuthorization
    fingerprint: str
    call_id: str
    consumed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def consume(self, authorization, action, call_id):
        with self._lock:
            authorization.check()
            if self.consumed or authorization is not self.authorization or self.fingerprint != action.fingerprint or self.call_id != call_id:
                raise AuthorizationError("denied", "本次许可已失效，请重新申请。")
            self.consumed = True


def prepare_action(name, args, handler, context, authorization, *, preparation=None):
    cwd = os.path.abspath(context.get("workspace_dir") or os.getcwd())
    kind = trusted_kind(name, handler)
    targets = []
    if kind == "file":
        if name == "apply_patch":
            from .apply_patch import parse_patch
            for operation in parse_patch(args.get("patch")):
                targets.append(_path(operation.path, cwd))
                if operation.move_path:
                    targets.append(_path(operation.move_path, cwd))
        else:
            keys = ("old_path", "new_path") if name == "workspace_rename_path" else ("path",)
            targets.extend(_path(args.get(key, ""), cwd) for key in keys)
    targets = tuple(dict.fromkeys(targets))
    state = tuple(_signature(path) for path in targets)
    preparation = preparation() if callable(preparation) else {}
    script_states = []
    for path in preparation.get("script_paths", []):
        script_states.append((path, _signature(path)))
    implementation = _source(handler)
    if kind == "opaque" and implementation and os.path.isfile(implementation):
        script_states.append((implementation, _signature(implementation)))
    runtime_kind = {"run_python_code": "python", "code_block": "python", "run_node_code": "node", "bash": "bash", "pwsh": "pwsh"}.get(name)
    runtime = None
    if runtime_kind:
        from . import sandbox_runtime
        runtime = {"kind": runtime_kind, "executable": (sandbox_runtime._RUNTIME_CACHE or {}).get(runtime_kind) or "由当前应用运行时解析"}
    payload = {"tool": name, "arguments": args, "cwd": cwd, "targets": targets,
               "runtime": runtime, "preparation": preparation, "scripts": script_states}
    fingerprint = hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True) + repr(state)).encode("utf-8")).hexdigest()
    details = json.dumps({**payload, "scripts": [path for path, _state in script_states]}, ensure_ascii=False, sort_keys=True, indent=2)
    free_write = bool(targets) and all(_inside(authorization.artifact_root, path) for path in targets)
    # Moving/deleting has existing product confirmations, retained even for artifacts.
    return PreparedAction(name, kind, fingerprint, details, targets, state, free_write)


def decide(action, *, needs_preparation=False):
    if not needs_preparation and (action.kind in {"read", "control"} or action.free_write):
        return AuthorizationDecision("allow")
    if action.kind == "file":
        names = {"apply_patch": "应用文件补丁", "workspace_rename_path": "移动或重命名", "workspace_delete_path": "删除"}
        summary = names.get(action.name, action.name) + f"，涉及 {len(action.targets)} 个位置：\n" + "\n".join(action.targets[:3])
        if len(action.targets) > 3:
            summary += "\n其余位置请查看执行详情。"
    else:
        summary = f"即将执行 {action.name}，可能改变文件或外部状态。"
    if needs_preparation:
        summary += "\n包含依赖准备或外部服务连接，请查看详情。"
    return AuthorizationDecision("ask", summary + "\n本次操作需要你的许可。")


def failure(status, message):
    return {"ok": False, "status": status, "authorization_status": status,
            "executed": False, "error": message, "content": message}


def invoke(name, args, handler, context, execute, *, preparation=None):
    authorization = current_authorization(context)
    if type(authorization) is not ExecutionAuthorization:
        return failure("unavailable", "缺少可信的运行授权，操作未执行。请从任务入口重新启动。")
    executing = False
    try:
        authorization.check()
        # Preserve the existing full-access path, without action preparation or I/O.
        if authorization.god_mode:
            with bind_authorization(authorization):
                token = _invocation.set((authorization, name, "full"))
                try:
                    executing = True
                    return execute()
                finally:
                    _invocation.reset(token)
        context = dict(context)
        context["execution_authorization"] = authorization
        action = prepare_action(name, args, handler, context, authorization, preparation=preparation)
        extra = preparation() if callable(preparation) else {}
        decision = decide(action, needs_preparation=bool(extra.get("requires_install")))
        call_id = str(context.get("tool_call_id") or uuid.uuid4().hex)
        emit = context.get("authorization_event")

        def event(status):
            if callable(emit):
                emit({"type": "authorization", "status": status, "tool": name,
                      "fingerprint": action.fingerprint, "tool_call_id": call_id,
                      "session_id": authorization.session_id, "run_id": authorization.run_id})

        granted = False
        if decision.outcome == "ask":
            from .interaction import interaction_service
            event("awaiting_approval")
            response = interaction_service.create_request(
                authorization.session_id, "approval", decision.reason,
                title="允许本次执行？", source_tool=name, timeout_seconds=120,
                metadata={"execution_permission": True, "run_id": authorization.run_id,
                          "tool_call_id": call_id, "fingerprint": action.fingerprint,
                          "details": action.details,
                          "scope": "仅授权列出的这一次文件操作。" if action.kind == "file" and not extra.get("requires_install") else "批准的是本次完整执行及其可能产生的影响；当前没有系统级隔离。"},
                abort_check=lambda: authorization._cancelled.is_set() or bool(callable(context.get("abort_check")) and context["abort_check"]()),
                require_receiver=True,
            )
            status = "approved" if response.get("approved") is True and response.get("status") == "completed" else str(response.get("status") or "unavailable")
            if status == "completed":
                status = "denied"
            event(status)
            if status != "approved":
                label = {"denied": "已拒绝", "timeout": "审批超时", "cancelled": "已取消", "unavailable": "审批不可用"}.get(status, "未获许可")
                return failure(status, "本次操作未执行（" + label + "）。已保留输入和现有成果，可重新申请。")
            refreshed = prepare_action(name, args, handler, context, authorization, preparation=preparation)
            grant = ApprovalGrant(authorization, action.fingerprint, call_id)
            try:
                grant.consume(authorization, refreshed, call_id)
            except AuthorizationError:
                event("invalidated")
                raise
            granted = True
        authorization.check()
        start = context.get("authorization_started")
        if callable(context.get("abort_check")) and context["abort_check"]():
            raise AuthorizationError("cancelled", "任务已停止，本次操作未执行。")
        if callable(start):
            start()
        with bind_authorization(authorization):
            token = _invocation.set((authorization, name, "approved" if granted else action.kind))
            try:
                executing = True
                return execute()
            finally:
                _invocation.reset(token)
    except AuthorizationError as exc:
        if executing:
            raise
        return failure(exc.status, str(exc))
    except (OSError, ValueError) as exc:
        if executing:
            raise
        return failure("unavailable", f"无法完成执行前授权检查，操作未执行：{exc}")
    except Exception as exc:
        if executing:
            raise
        return failure("unavailable", f"执行前检查或授权记录失败，操作未执行：{exc}")


def invocation_authorized(context=None):
    auth = current_authorization(context)
    frame = _invocation.get()
    return bool(type(auth) is ExecutionAuthorization and frame and frame[0] is auth)


def approved_file_confirmation(context=None):
    frame = _invocation.get()
    auth = current_authorization(context)
    return bool(frame and frame[0] is auth and not auth.god_mode and frame[1] in _FILES and frame[2] == "approved")


def require_process_authorization():
    auth = current_authorization()
    if auth is not None:
        auth.check()
        if not invocation_authorized():
            raise AuthorizationError("denied", "进程缺少本次执行许可。")
