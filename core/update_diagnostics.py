"""Installer diagnostics in the shared runtime-log inventory, without changing install decisions."""
import json
import os
from pathlib import Path

from core.runtime_logs import append_log
from core.task_details import redact_diagnostic


def installer_data_dir(transaction):
    transaction = Path(transaction).absolute()
    if transaction.parent.name != "transactions":
        return transaction
    root = transaction.parent.parent
    return root.parent if root.name == "updates" else root


def log_installer_event(transaction, phase, **fields):
    event = {"phase": phase, "transaction": Path(transaction).name, **fields}
    return append_log("app_update", json.dumps(redact_diagnostic(event), ensure_ascii=False),
                      installer_data_dir(transaction))


def installer_exit_details(transaction, process):
    """Read a bounded diagnostic snapshot before the caller writes its abort marker."""
    root = Path(transaction)
    details = {"pid": getattr(process, "pid", None), "markers": {}}
    for name in ("ready", "commit", "abort", "opened"):
        try:
            details["markers"][name] = (root / f"{name}.json").is_file()
        except OSError as exc:
            details.setdefault("marker_read_errors", {})[name] = f"{type(exc).__name__}: {exc}"
    try:
        details["exit_code"] = process.poll() if process is not None else None
    except OSError as exc:
        details["process_read_error"] = f"{type(exc).__name__}: {exc}"
    try:
        with (root / "result.json").open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("installer result exceeds 64 KiB")
        result = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(result, dict) or result.get("id") != root.name:
            raise ValueError("installer result transaction does not match")
        details["result"] = {key: result[key] for key in (
            "phase", "stage", "pid", "error", "error_type", "inner_error", "error_id",
            "script_line", "script_stack", "mutex_name", "mutex_created", "mutex_acquired",
        ) if key in result}
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        details["result_read_error"] = f"{type(exc).__name__}: {exc}"
    try:
        with (root / "update-launch.log").open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - 8192))
            raw = handle.read(8192)
        details["launch_log_bytes"] = size
        details["launch_log_tail_truncated"] = size > 8192
        try:
            tail = raw.decode("utf-8-sig")
            encoding = "utf-8"
        except UnicodeDecodeError:
            # Older Windows PowerShell startup/parse errors can precede our UTF-8 setup.
            encoding = "mbcs" if os.name == "nt" else "utf-8"
            tail = raw.decode(encoding, errors="replace")
        details["launch_log_encoding"] = encoding
        details["launch_log_tail"] = tail
    except FileNotFoundError:
        pass
    except OSError as exc:
        details["launch_log_read_error"] = f"{type(exc).__name__}: {exc}"
    return redact_diagnostic(details)
