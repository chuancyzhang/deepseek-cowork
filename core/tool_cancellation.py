"""Run-scoped cancellation shared by tool implementations."""
import logging
import subprocess
import weakref

from core.process_utils import terminate_process_tree

logger = logging.getLogger(__name__)


def abort_requested(context=None):
    context = context or {}
    check = context.get("abort_check")
    if callable(check) and check():
        return True
    authorization = context.get("execution_authorization")
    event = getattr(authorization, "_cancelled", None)
    return bool(event is not None and event.is_set())


class _AbortState(dict):
    def __init__(self, context):
        super().__init__(aborted=False, bridge=None)
        self.context = context or {}

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return bool(value or abort_requested(self.context)) if key == "aborted" else value


def init_abort_state(context):
    state = _AbortState(context)
    # Workers supply a persistent predicate: it also covers stop-before-connect.
    if callable(state.context.get("abort_check")):
        return state
    signal = state.context.get("abort_signal")
    if signal is not None:
        from PySide6.QtCore import QObject, Qt

        class Bridge(QObject):
            def trigger(self):
                target = reference()
                if target is not None:
                    target["aborted"] = True

        reference = weakref.ref(state)
        bridge = Bridge()
        signal.connect(bridge.trigger, Qt.DirectConnection)
        state["bridge"] = bridge
    return state


def stop_tool_process(process, context=None):
    """Stop descendants too, retain captured output, and expose cleanup failure."""
    context = context or {}
    stopped = terminate_process_tree(process)
    output, error = b"", b""
    try:
        output, error = process.communicate(timeout=2)
    except subprocess.TimeoutExpired as exc:
        output, error = exc.output or b"", exc.stderr or b""
        stopped = False
    def decode(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    logger.info("tool.cancel session=%s tool_call=%s pid=%s stopped=%s",
                context.get("session_id", ""), context.get("tool_call_id", ""),
                process.pid, stopped)
    message = ("工具执行已停止，已保留已有输出和文件。" if stopped else
               "工具终止未完成，请检查仍在运行的进程；已保留已有输出和文件。")
    return {"ok": False, "aborted": True, "status": "cancelled" if stopped else "unknown",
            "error": message, "stdout": decode(output), "stderr": decode(error),
            "exit_code": process.poll()}
