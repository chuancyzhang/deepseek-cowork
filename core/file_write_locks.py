"""Ordered path locks for managed desktop/daemon writers; external writes use hashes."""
import os
import hashlib
import time
import threading
from contextlib import contextmanager
from functools import wraps

_guard = threading.RLock()
_locks = {}
_held = threading.local()


@contextmanager
def _posix_process_locks(keys):
    import fcntl
    from .env_utils import get_app_data_dir
    root = os.path.join(get_app_data_dir(), "locks")
    os.makedirs(root, mode=0o700, exist_ok=True)
    held = getattr(_held, "files", None)
    if held is None:
        held = _held.files = {}
    acquired = []
    try:
        for key in keys:
            if key in held:
                continue  # The outer context in this thread owns the flock.
            path = os.path.join(root, hashlib.sha256(key.encode()).hexdigest() + ".lock")
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            deadline = time.monotonic() + 30
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("文件正在由其他操作保存，请稍后重试。")
                        time.sleep(.05)
            except BaseException:
                os.close(fd)
                raise
            held[key] = fd
            acquired.append(key)
        yield
    finally:
        for key in reversed(acquired):
            fd = held.pop(key)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # Never unlink: waiters may already hold this inode across os.replace().


@contextmanager
def file_write_lock(*paths):
    keys = sorted({os.path.normcase(os.path.realpath(path)) for path in paths if path})
    with _guard:
        locks = []
        for key in keys:
            lock, count = _locks.get(key, (threading.RLock(), 0))
            _locks[key] = (lock, count + 1)
            locks.append((key, lock))
    try:
        for _, lock in locks:
            lock.acquire()
        with _process_locks(keys):
            yield
    finally:
        for _, lock in reversed(locks):
            lock.release()
        with _guard:
            for key, lock in locks:
                count = _locks[key][1] - 1
                if count:
                    _locks[key] = (lock, count)
                else:
                    del _locks[key]


@contextmanager
def _process_locks(keys):
    if os.name != "nt":
        with _posix_process_locks(keys):
            yield
        return
    import ctypes
    import hashlib
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        for key in keys:
            name = "Local\\CoworkFile-" + hashlib.sha256(key.encode()).hexdigest()
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            outcome = kernel.WaitForSingleObject(handle, 30000)
            if outcome not in {0, 0x80}:
                kernel.CloseHandle(handle)
                raise TimeoutError("文件正在由其他操作保存，请稍后重试。")
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)


def locked_path(argument=0, *, session=False):
    def decorate(function):
        @wraps(function)
        def locked(*args, **kwargs):
            import inspect
            values = inspect.signature(function).bind(*args, **kwargs).arguments
            value = list(values.values())[argument]
            with file_write_lock(value.path if session else value):
                return function(*args, **kwargs)
        return locked
    return decorate
