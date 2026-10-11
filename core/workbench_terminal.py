"""Interactive Windows terminals. PTYs live independently of visible widgets."""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import threading
from collections import deque

from .workbench import WorkbenchError

log = logging.getLogger(__name__)


class WindowsProcessJob:
    """An owned kernel job: closing it kills only processes assigned to this job."""
    def __init__(self, pid):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = None
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process = self.kernel.OpenProcess(0x100 | 0x1, False, int(pid))
            if not process or not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self):
        handle = getattr(self, "handle", None)
        self.handle = None
        if handle:
            self.kernel.CloseHandle(handle)


def terminal_profiles():
    from .sandbox_runtime import get_runtime_executable
    result = []
    bash = get_runtime_executable("bash")
    if bash and os.path.isfile(bash):
        result.append({"id": "bash", "name": "Git Bash", "executable": bash,
                       "args": ["--login", "-i"]})
    pwsh = shutil.which("pwsh.exe")
    if not pwsh:
        candidate = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PowerShell", "7", "pwsh.exe")
        pwsh = candidate if os.path.isfile(candidate) else ""
    if pwsh:
        result.append({"id": "pwsh", "name": "PowerShell 7", "executable": pwsh, "args": ["-NoLogo"]})
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(powershell):
        result.append({"id": "powershell", "name": "Windows PowerShell", "executable": powershell, "args": ["-NoLogo"]})
    return result


class TerminalSession:
    MAX_BUFFER = 2 * 1024 * 1024

    def __init__(self, session_id, tab_id, profile, cwd, rows=24, cols=80):
        self.session_id, self.tab_id = session_id, tab_id
        self.profile, self.cwd = profile, os.path.abspath(cwd)
        self.status = "starting"
        self.error = ""
        self.exit_code = None
        self.process = None
        self.job = None
        self._chunks = deque()
        self._start = self._end = 0
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._rows, self._cols = rows, cols
        self.thread = threading.Thread(target=self._run, name="workbench-terminal-" + tab_id[:8], daemon=True)

    def start(self):
        self.thread.start()

    def _append(self, text):
        with self._lock:
            self._chunks.append(text)
            self._end += len(text)
            while self._end - self._start > self.MAX_BUFFER and self._chunks:
                first = self._chunks.popleft()
                excess = min(len(first), self._end - self._start - self.MAX_BUFFER)
                self._start += excess
                if excess < len(first):
                    self._chunks.appendleft(first[excess:])

    def _run(self):
        log.info("terminal.start session=%s tab=%s shell=%s", self.session_id, self.tab_id, self.profile["id"])
        try:
            if os.name != "nt":
                raise WorkbenchError("unsupported", "交互终端需要 Windows ConPTY。")
            from winpty import PTY, Backend
            from .sandbox_runtime import build_sandbox_env
            if not os.path.isdir(self.cwd):
                raise WorkbenchError("cwd_missing", "终端启动目录已不存在。")
            env = build_sandbox_env()
            env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor", "CHERE_INVOKING": "1"})
            # Use the native nonblocking API. PtyProcess adds a blocking socket
            # reader whose shutdown can wait forever on an otherwise idle PTY.
            self.process = PTY(self._cols, self._rows, backend=Backend.ConPTY)
            self.process.spawn(self.profile["executable"],
                cmdline=" " + subprocess.list2cmdline(self.profile["args"]), cwd=self.cwd,
                env="\0".join(f"{key}={value}" for key, value in env.items()) + "\0")
            self.job = WindowsProcessJob(self.process.pid)
            if self._closed.is_set():
                return
            self.status = "running"
            while not self._closed.is_set():
                try:
                    data = self.process.read(blocking=False)
                except EOFError:
                    break
                if data:
                    self._append(data)
                if self.process.iseof() or not self.process.isalive():
                    break
                if not data:
                    self._closed.wait(.01)
            self.exit_code = self.process.get_exitstatus()
            self.status = "exited"
        except Exception as exc:
            if not self._closed.is_set():
                self.status, self.error = "failed", str(exc)
                log.exception("terminal.error session=%s tab=%s", self.session_id, self.tab_id)
        finally:
            if self.job:
                self.job.close()
            if self.process:
                try:
                    self.process.cancel_io()
                except Exception:
                    log.exception("terminal.cleanup_error tab=%s", self.tab_id)
            if self._closed.is_set():
                self.status = "closed"
            log.info("terminal.finish session=%s tab=%s status=%s exit=%s", self.session_id, self.tab_id, self.status, self.exit_code)

    def read(self, cursor=0, limit=32768):
        limit = max(1, min(int(limit), 65536))
        with self._lock:
            cursor = max(0, int(cursor))
            truncated = cursor < self._start
            cursor = min(max(cursor, self._start), self._end)
            text = "".join(self._chunks)[cursor - self._start: cursor - self._start + limit]
            return {"output": text, "cursor": cursor + len(text), "truncated": truncated,
                    "has_more": cursor + len(text) < self._end, "status": self.status,
                    "exit_code": self.exit_code, "error": self.error}

    def write(self, text):
        if self.status != "running" or not self.process or self._closed.is_set():
            raise WorkbenchError("terminal_not_running", "终端未运行，请检查状态或重新启动。")
        if len(text) > 65536:
            raise WorkbenchError("input_too_large", "一次终端输入不能超过 64 KiB。")
        self.process.write(text)

    def resize(self, rows, cols):
        self._rows, self._cols = max(2, min(300, int(rows))), max(2, min(500, int(cols)))
        if self.status == "running" and self.process:
            self.process.set_size(self._cols, self._rows)

    def close(self):
        self._closed.set()
        self.status = "closed"
        if self.job:
            self.job.close()
