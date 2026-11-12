"""Bundled platform operations. The runtime owns ordering, the OS owns I/O."""
import os
import sys
from pathlib import Path


class PlatformRuntime:
    name = sys.platform

    def filesystem_path(self, path):
        """Native I/O spelling only; keep ordinary paths in persisted data."""
        return Path(path)

    def sync_file(self, handle):
        handle.flush()
        os.fsync(handle.fileno())

    def sync_directory(self, path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def replace(self, source, destination):
        os.replace(source, destination)
        self.sync_directory(str(Path(destination).parent))

    def lock(self, handle):
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def process_options(self, **options):
        from core.process_utils import subprocess_kwargs_no_window
        return subprocess_kwargs_no_window(**options)

    def stop_process(self, process, timeout=2):
        from core.process_utils import terminate_process_tree
        return terminate_process_tree(process, timeout)

    def capabilities(self):
        from core.platform_capabilities import desktop_target
        return {"target": desktop_target(), "validated": self.name == "win32"}

    def require_legacy_closed(self, directory):
        # The former Qt desktop was released on Windows. Other platform
        # backends participate in the shared data-directory lease.
        return


class WindowsRuntime(PlatformRuntime):
    def filesystem_path(self, path):
        value = os.path.abspath(path)
        if value.startswith("\\\\?\\"):
            return Path(value)
        if value.startswith("\\\\"):
            return Path("\\\\?\\UNC\\" + value[2:])
        return Path("\\\\?\\" + value)

    def require_legacy_closed(self, directory):
        import ctypes
        import hashlib
        from ctypes import wintypes
        identity = os.path.normcase(os.path.abspath(directory))
        digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:16]
        probe = ctypes.WinDLL("kernel32", use_last_error=True).WaitNamedPipeW
        probe.argtypes, probe.restype = (wintypes.LPCWSTR, wintypes.DWORD), wintypes.BOOL
        connected = probe("\\\\.\\pipe\\deepseek-cowork-ui-" + digest, 1)
        error = ctypes.get_last_error() if not connected else 0
        if connected or error in (121, 231):
            raise RuntimeError("请先正常退出原版 Cowork，保存完成后再次打开新版；历史与设置将自动延续。")
        if error not in (2, 3):
            raise ctypes.WinError(error)

    def sync_directory(self, path):
        # Publication of newly created files uses MoveFileExW WRITE_THROUGH.
        pass

    def replace(self, source, destination):
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(self.filesystem_path(source)), str(self.filesystem_path(destination)), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())

    def lock(self, handle):
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


class MacRuntime(PlatformRuntime):
    def sync_file(self, handle):
        import fcntl
        handle.flush()
        fcntl.fcntl(handle.fileno(), fcntl.F_FULLFSYNC)


platform_runtime = WindowsRuntime() if os.name == "nt" else MacRuntime() if sys.platform == "darwin" else PlatformRuntime()
