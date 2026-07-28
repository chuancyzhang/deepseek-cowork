import json
import os
import runpy
import sys
import time
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent
PATH_SUFFIXES = {".csv", ".json", ".md", ".txt", ".xlsx", ".xls", ".yaml", ".yml"}


def emit(stage, entry, **fields):
    payload = {
        "type": "wind_aifinmarket_script",
        "stage": stage,
        "entry": entry,
        "timestamp": time.time(),
    }
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def validate_workspace_args(entry, workspace_path, arguments):
    for raw in arguments:
        value = str(raw)
        if value.startswith("-") and "=" not in value:
            continue
        candidate_text = value.split("=", 1)[1] if value.startswith("-") and "=" in value else value
        candidate = Path(candidate_text)
        looks_like_path = (
            candidate.is_absolute()
            or "/" in candidate_text
            or "\\" in candidate_text
            or candidate.suffix.lower() in PATH_SUFFIXES
        )
        if not looks_like_path:
            continue
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace_path / candidate).resolve()
        if os.path.commonpath([str(workspace_path), str(resolved)]) != str(workspace_path):
            emit("error", entry, error_type="WorkspacePathEscape")
            raise RuntimeError(f"Path argument escapes COWORK_WORKSPACE_DIR: {candidate_text}")


def run_child(entry, relative_path):
    emit("submit", entry)
    workspace = str(os.environ.get("COWORK_WORKSPACE_DIR") or "").strip()
    if not workspace:
        emit("error", entry, error_type="WorkspaceRequired")
        raise RuntimeError("COWORK_WORKSPACE_DIR is required.")
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        emit("error", entry, error_type="WorkspaceNotFound")
        raise RuntimeError(f"COWORK_WORKSPACE_DIR does not exist: {workspace_path}")
    target = (PLUGIN_ROOT / relative_path).resolve()
    if os.path.commonpath([str(PLUGIN_ROOT), str(target)]) != str(PLUGIN_ROOT):
        emit("error", entry, error_type="ChildPathEscape")
        raise RuntimeError("Child script path escapes the plugin root.")
    if not target.is_file():
        emit("error", entry, error_type="ChildScriptMissing")
        raise RuntimeError(f"Child script is missing: {relative_path}")
    started = time.time()
    emit("start", entry)
    previous_cwd = Path.cwd()
    previous_argv = sys.argv[:]
    validate_workspace_args(entry, workspace_path, previous_argv[1:])
    sys.path.insert(0, str(target.parent))
    try:
        os.chdir(workspace_path)
        sys.argv = [str(target), *previous_argv[1:]]
        emit("run", entry)
        runpy.run_path(str(target), run_name="__main__")
        emit("finish", entry, duration_seconds=round(time.time() - started, 3))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code in (None, "") else 1)
        emit(
            "finish" if code == 0 else "error",
            entry,
            exit_code=code,
            duration_seconds=round(time.time() - started, 3),
        )
        raise
    except Exception as exc:
        emit("error", entry, error_type=type(exc).__name__)
        raise
    finally:
        sys.argv = previous_argv
        os.chdir(previous_cwd)
        if sys.path and sys.path[0] == str(target.parent):
            sys.path.pop(0)
