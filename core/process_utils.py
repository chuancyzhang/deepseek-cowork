import hashlib
import os
import subprocess

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None


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
