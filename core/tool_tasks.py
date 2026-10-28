"""Run-owned tool executions which can yield without restarting the operation.

Only the owner consumes results. Threads never append conversation messages.
The journal is written before a completion becomes observable to that owner.
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)
TOOL_WAIT_SECONDS = 120.0
INTERACTION_TOOLS = {"request_user_input", "request_user_approval"}
_active_task = contextvars.ContextVar("cowork_active_tool_task", default=None)
_active_run = contextvars.ContextVar("cowork_active_tool_run", default=None)


def current_tool_run():
    """The execution owner of nested tool work, independent of its thread."""
    return _active_run.get()


def current_tool_task():
    """Capture the task owner before handing work to another thread."""
    return _active_task.get()


WAIT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "wait_tool",
        "description": (
            "Wait up to 120 seconds for a running tool task in this run. "
            "Use the task_id returned with status running or queued. The original "
            "operation continues; never repeat it to poll. You may instead do "
            "independent work. Set terminate=true to request stopping only this "
            "task and wait up to 120 seconds for confirmation, preserving output. "
            "A pending result with cancel_requested=true is not confirmed stopped. "
            "Completed operations are not undone. Results are also delivered automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "terminate": {"type": "boolean", "description": "Request stopping this task; defaults to false."},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
}


def is_pending_tool_result(value):
    return (isinstance(value, dict) and value.get("background_tool") is True
            and value.get("status") in {"running", "queued"})


def tool_interaction_wait(function):
    """Exclude all nested human interaction, including connection approvals."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        task = _active_task.get()
        if task is None:
            return function(*args, **kwargs)
        task.pause_clock()
        try:
            return function(*args, **kwargs)
        finally:
            task.resume_clock()
    return wrapped


@dataclass
class ToolTask:
    task_id: str
    name: str
    args: dict
    call_id: str
    read_only: bool
    clock: object = time.monotonic
    state: str = "queued"
    result: object = None
    error: BaseException | None = None
    execution_finished: bool = False
    effects_applied: bool = False
    yielded: bool = False
    delivered: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    active_started: float | None = None
    active_elapsed: float = 0.0
    pause_depth: int = 0
    dependencies: tuple = ()
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.RLock)

    def elapsed(self):
        with self.lock:
            return self.active_elapsed + (
                max(0.0, self.clock() - self.active_started)
                if self.active_started is not None else 0.0)

    def pause_clock(self):
        with self.lock:
            if self.pause_depth == 0 and self.active_started is not None:
                self.active_elapsed += max(0.0, self.clock() - self.active_started)
                self.active_started = None
            self.pause_depth += 1

    def resume_clock(self):
        with self.lock:
            self.pause_depth -= 1
            if self.pause_depth == 0 and self.state == "running":
                self.active_started = self.clock()

    def waiting_for_user(self):
        with self.lock:
            paused = bool(self.pause_depth)
        return paused or any(not task.done.is_set() and task.waiting_for_user() for task in self.dependencies)


class ToolTaskManager:
    def __init__(self, session_id, run_id, *, persist=None, classify=None,
                 abort_check=None, clock=time.monotonic, wait_seconds=None):
        self.session_id = str(session_id)
        self.run_id = str(run_id)
        self.persist = persist or (lambda task_id, payload: None)
        self.classify = classify or (lambda result: "")
        self.abort_check = abort_check or (lambda: False)
        self.clock = clock
        self.wait_seconds = TOOL_WAIT_SECONDS if wait_seconds is None else wait_seconds
        self.tasks = {}
        self.closed = threading.Event()
        self.changed = threading.Event()
        self.events = queue.SimpleQueue()
        self.owner_ident = threading.get_ident()
        self.tick = lambda: None

    def _record(self, task, **payload):
        return self.persist(task.task_id, {
            "session_id": self.session_id, "run_id": self.run_id,
            "task_id": task.task_id, "tool_call_id": task.call_id,
            "name": task.name, "background_tool": True, **payload,
        })

    def _log(self, task, phase):
        logger.info("tool.task session=%s run=%s task=%s call=%s phase=%s elapsed=%.3f",
                    self.session_id, self.run_id, task.task_id, task.call_id,
                    phase, task.elapsed())

    def abort_requested(self, task_id):
        task = self.tasks.get(task_id)
        return (self.closed.is_set() or self.abort_check()
                or bool(task is not None and task.cancel_requested.is_set()))

    def submit(self, task_id, name, args, call_id, execute, *, read_only=False, predecessor=None):
        if self.closed.is_set() or self.abort_check():
            raise RuntimeError("任务已停止，不能启动工具。")
        # A model can accidentally repeat a pending invocation under a new call ID.
        for existing in self.tasks.values():
            if not existing.done.is_set() and existing.name == name and existing.args == args:
                return existing, False
        if task_id in self.tasks:
            return self.tasks[task_id], False
        task = ToolTask(task_id, name, args, call_id, read_only, clock=self.clock)
        dependencies = []
        if predecessor in self.tasks:
            dependencies.append(self.tasks[predecessor])
        if not read_only:
            dependencies.extend(t for t in self.tasks.values()
                                if not t.read_only and not t.done.is_set() and t not in dependencies)
        task.dependencies = tuple(dependencies)
        self._record(task, status="queued", read_only=read_only, args=args,
                     yielded=False, result_delivered=False)
        self.tasks[task_id] = task
        self._log(task, "queued")
        context = contextvars.copy_context()

        def run():
            token = _active_task.set(task)
            run_token = _active_run.set(self)
            try:
                for dependency in dependencies:
                    while not dependency.done.wait(.05):
                        if self.abort_requested(task_id):
                            return
                    if dependency.error:
                        raise RuntimeError("前序工具结果保存失败，后续操作未执行。") from dependency.error
                    if not dependency.read_only and self.classify(dependency.result) == "unknown":
                        task.result = {"status": "unknown", "not_executed": True,
                                       "error": "前序操作结果未知，本操作未执行；请先只读核实。"}
                        return
                    if dependency.task_id == predecessor and dependency.state == "cancelled":
                        task.result = {"status": "cancelled", "not_executed": True,
                                       "dependency_task_id": dependency.task_id,
                                       "content": "前序工具已停止，本次后续调用未执行；请根据已有结果决定下一步。"}
                        return
                with task.lock:
                    if self.abort_requested(task_id):
                        return
                    task.state = "running"
                    task.started_at = time.time()
                    task.active_started = self.clock()
                self._record(task, status="started", started_at=task.started_at)
                self._log(task, "started")
                self.events.put(("tool_task_started", (task.task_id,)))
                if self.abort_requested(task_id):
                    return
                task.result = execute()
                task.execution_finished = True
            except Exception as exc:
                task.error = exc
            finally:
                with task.lock:
                    task.active_elapsed = task.elapsed()
                    task.active_started = None
                    task.finished_at = time.time()
                    if task.result is None and self.abort_requested(task_id):
                        task.result = {"status": "unknown" if task.started_at else "cancelled", "aborted": True,
                                       "not_executed": not bool(task.started_at),
                                       "content": ("已请求停止，工具结果尚未确认。" if task.started_at else
                                                   "任务已停止，排队工具未执行。")}
                    failure = self.classify(task.result)
                    cancelled = isinstance(task.result, dict) and task.result.get("status") == "cancelled"
                    task.state = ("unknown" if task.error else "cancelled" if cancelled else failure or "succeeded")
                    try:
                        self._record(task, status=task.state, finished_at=task.finished_at,
                                     result_obj=task.result if isinstance(task.result, dict) else None,
                                     result_text=self.result_text(task.result),
                                     task_error=str(task.error or ""),
                                     late_result=self.closed.is_set())
                    except Exception as exc:
                        task.error = exc
                        task.state = "unknown"
                    task.done.set()
                self._log(task, "error" if task.error else task.state)
                self.changed.set()
                _active_task.reset(token)
                _active_run.reset(run_token)

        threading.Thread(target=lambda: context.run(run), name="tool-" + task_id[:12], daemon=True).start()
        return task, not any(dependency.yielded or not dependency.done.is_set() for dependency in dependencies)

    @staticmethod
    def result_text(result):
        return json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, dict) else str(result)

    def pending_result(self, task):
        with task.lock:
            task.yielded = True
            self._record(task, yielded=True)
            state = "queued" if task.state == "queued" else "running"
            cancelling = task.cancel_requested.is_set()
        self._log(task, "yielded")
        return {"background_tool": True, "status": state, "task_id": task.task_id,
                "tool_name": task.name, "elapsed_seconds": round(task.elapsed(), 3),
                "cancel_requested": cancelling,
                "content": ("已请求停止此工具，但尚未确认结束，不能视为已停止。已有输出将保留；"
                            "可调用 wait_tool(task_id) 等待确认或先做独立工作。不要重复原调用。"
                            if cancelling else
                            "工具尚未完成，原调用会继续执行。可调用 wait_tool(task_id) 继续等待，"
                            "调用 wait_tool(task_id, terminate=true) 请求停止此工具，"
                            "或先进行不依赖结果的工作；完成后会自动交回结果。不要重复原调用。")}

    def await_initial(self, task, can_wait=True):
        if not can_wait and not task.done.is_set():
            return self.pending_result(task)
        while not task.done.wait(.02):
            self.tick()
            if self.closed.is_set() or self.abort_check():
                return {"status": "unknown", "aborted": True,
                        "content": "已请求停止工具；尚未确认结果，已有输出将保留。"}
            if task.name not in INTERACTION_TOOLS and task.elapsed() >= self.wait_seconds:
                with task.lock:
                    if not task.done.is_set() and not task.pause_depth:
                        return self.pending_result(task)
        if task.error and not task.execution_finished:
            raise task.error
        return task.result

    def wait(self, task_id, *, terminate=False):
        task = self.tasks.get(task_id)
        if task is None:
            return {"status": "error", "error": "任务标识无效或不属于本次运行。"}
        if terminate:
            with task.lock:
                if not task.done.is_set() and not task.cancel_requested.is_set():
                    # Persist intent before signalling; completion uses the same lock.
                    self._record(task, cancel_requested=True, cancel_requested_at=time.time())
                    task.cancel_requested.set()
                    self._log(task, "cancel_requested")
                    self.changed.set()
        self._log(task, "waiting")
        previous, waited = self.clock(), 0.0
        while not task.done.wait(.02):
            self.tick()
            if self.closed.is_set() or self.abort_check():
                return {"status": "cancelled", "aborted": True, "task_id": task_id}
            now = self.clock()
            if task.cancel_requested.is_set() or not task.waiting_for_user():
                waited += max(0, now - previous)
            previous = now
            if waited >= self.wait_seconds:
                return self.pending_result(task)
        if task.error:
            raise task.error
        if task.delivered:
            return {"status": "already_delivered", "task_id": task_id,
                    "content": "该工具结果已交付，请使用已有结果。"}
        return {"status": "completed", "task_id": task_id, "tool_name": task.name,
                "background_tool": True, "result": task.result}

    def ready(self):
        if self.closed.is_set():
            return []
        return [t for t in self.tasks.values() if t.yielded and not t.delivered and t.done.is_set()]

    def outstanding(self):
        return [t for t in self.tasks.values() if t.yielded and not t.delivered]

    def mark_delivered(self, task_id, message_id):
        task = self.tasks.get(task_id)
        if task is not None and task.done.is_set():
            self._record(task, result_delivered=True, delivery_message_id=message_id)
            task.delivered = True
            self._log(task, "delivered")

    def snapshot(self):
        return [{"task_id": t.task_id, "tool_name": t.name, "status": t.state,
                 "cancel_requested": t.cancel_requested.is_set(),
                 "elapsed_seconds": round(t.elapsed(), 3), "result_saved": t.done.is_set() and not t.error}
                for t in self.outstanding()]

    def close(self):
        self.closed.set()
        for task in list(self.tasks.values()):
            with task.lock:
                if task.done.is_set():
                    continue
                queued = task.state == "queued"
                self._record(task, status="cancelled" if queued else "unknown",
                             unknown_reason="owner run stopped" if not queued else "",
                             cancel_requested=True)
                self._log(task, "cancelled" if queued else "cancel_requested")
        self.changed.set()


class ToolEventSignal:
    """A tool-facing signal proxy; only the run owner touches Qt signals."""
    def __init__(self, manager, name):
        self.manager, self.name = manager, name

    def emit(self, *args):
        if not self.manager.closed.is_set():
            self.manager.events.put((self.name, args))
