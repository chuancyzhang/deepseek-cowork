import hashlib
import os
import platform
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None


_EXTERNAL_PROCESS_LAUNCH_LOCK = threading.RLock()


@dataclass(frozen=True)
class FileManagerOpenResult:
    ok: bool
    path: str
    action: str
    error: str = ""


def reveal_path_in_file_manager(path, system_name=None, shell_execute=None, popen=None):
    """Open a directory or reveal a file without hiding the file-manager window."""
    raw_path = str(path or "").strip()
    if not raw_path:
        return FileManagerOpenResult(False, "", "reveal", "路径为空。")
    normalized = os.path.abspath(os.path.expanduser(raw_path))
    if not os.path.exists(normalized):
        return FileManagerOpenResult(False, normalized, "reveal", f"路径不存在：{normalized}")

    system = str(system_name or platform.system())
    action = "open_directory" if os.path.isdir(normalized) else "select_file"
    try:
        if system == "Windows":
            if shell_execute is None:
                import ctypes
                shell_execute = ctypes.windll.shell32.ShellExecuteW
            if os.path.isdir(normalized):
                result = shell_execute(None, "open", normalized, None, None, 1)
            else:
                parameters = f'/select,"{normalized}"'
                result = shell_execute(None, "open", "explorer.exe", parameters, None, 1)
            if int(result) <= 32:
                return FileManagerOpenResult(
                    False, normalized, action, f"系统文件管理器返回错误码 {int(result)}。"
                )
        else:
            target = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
            command = ["open", target] if system == "Darwin" else ["xdg-open", target]
            launcher = popen or subprocess.Popen
            launcher(command)
        return FileManagerOpenResult(True, normalized, action)
    except Exception as exc:
        return FileManagerOpenResult(False, normalized, action, str(exc))


def subprocess_kwargs_no_window(**kwargs):
    """Return subprocess kwargs that suppress console windows on Windows."""
    if os.name != "nt":
        return kwargs

    flags = int(kwargs.get("creationflags") or 0)
    kwargs["creationflags"] = flags | getattr(subprocess, "CREATE_NO_WINDOW", 0)

    if kwargs.get("startupinfo") is None:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            startupinfo.wShowWindow = 0
        except Exception:
            pass
        kwargs["startupinfo"] = startupinfo
    return kwargs


def terminate_process_tree(process, timeout=2.0):
    """Terminate a tool process together with descendants it already spawned."""

    if process is None:
        return True
    try:
        if process.poll() is not None:
            return True
    except Exception:
        return False

    wait_timeout = max(0.1, float(timeout or 0.0))
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=wait_timeout,
                **subprocess_kwargs_no_window(),
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

    try:
        process.wait(timeout=wait_timeout)
        return True
    except Exception:
        pass

    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=wait_timeout)
    except Exception:
        pass
    try:
        return process.poll() is not None
    except Exception:
        return False


def _frozen_windows_bundle_dir():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return ""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if not bundle_dir:
        raise RuntimeError("冻结应用缺少 sys._MEIPASS，不能安全启动外部程序。")
    return os.path.abspath(bundle_dir)


def _set_windows_dll_directory(path):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    setter = kernel32.SetDllDirectoryW
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_bool
    ctypes.set_last_error(0)
    if not setter(path):
        raise ctypes.WinError(ctypes.get_last_error())


def _path_is_within(path, root):
    try:
        common = os.path.commonpath(
            [os.path.abspath(path), os.path.abspath(root)]
        )
        return os.path.normcase(common) == os.path.normcase(os.path.abspath(root))
    except (OSError, TypeError, ValueError):
        return False


def _sanitized_external_environment(environment, bundle_dir):
    sanitized = dict(os.environ if environment is None else environment)
    path_key = next(
        (key for key in sanitized if str(key).upper() == "PATH"),
        None,
    )
    path_value = sanitized.get(path_key) if path_key is not None else None
    if path_value:
        sanitized[path_key] = os.pathsep.join(
            entry
            for entry in path_value.split(os.pathsep)
            if entry
            and not _path_is_within(entry.strip().strip('"'), bundle_dir)
        )
    return sanitized


def popen_external_program(args, **kwargs):
    """Start a non-bundled program without PyInstaller's DLL search override."""
    bundle_dir = _frozen_windows_bundle_dir()
    if not bundle_dir:
        return subprocess.Popen(args, **kwargs)

    launch_kwargs = dict(kwargs)
    launch_kwargs["env"] = _sanitized_external_environment(
        launch_kwargs.get("env"),
        bundle_dir,
    )
    with _EXTERNAL_PROCESS_LAUNCH_LOCK:
        _set_windows_dll_directory(None)
        try:
            process = subprocess.Popen(args, **launch_kwargs)
        except BaseException:
            _set_windows_dll_directory(bundle_dir)
            raise
        try:
            _set_windows_dll_directory(bundle_dir)
        except BaseException:
            try:
                process.terminate()
            except OSError:
                pass
            raise
    return process


def runtime_debug_logging_enabled():
    value = str(os.environ.get("COWORK_RUNTIME_DEBUG_LOG") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "debug"}


def build_process_singleton_lock_path(root_dir, name):
    safe_name = str(name or "process-singleton").strip() or "process-singleton"
    digest = hashlib.sha256(safe_name.encode("utf-8", errors="replace")).hexdigest()[:16]
    filename = f"{safe_name.replace(os.sep, '-').replace(' ', '-')}-{digest}.lock"
    return os.path.join(root_dir, filename)


class ProcessSingletonLock:
    def __init__(self, lock_path):
        self.lock_path = os.path.abspath(lock_path)
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return True
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                if msvcrt is None:
                    raise RuntimeError("msvcrt unavailable on Windows")
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                if fcntl is None:
                    raise RuntimeError("fcntl unavailable on this platform")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle = handle
            return True
        except OSError:
            handle.close()
            return False
        except Exception:
            handle.close()
            raise

    def release(self):
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


def acquire_process_singleton(lock_path):
    lock = ProcessSingletonLock(lock_path)
    if lock.acquire():
        return lock
    return None
