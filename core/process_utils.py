import os
import subprocess


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
