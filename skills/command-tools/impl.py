import fnmatch
import json
import locale
import os
import re
import subprocess
import tempfile

from PySide6.QtCore import QObject, Qt

from core.sandbox_runtime import get_runtime_executable, run_in_sandbox, run_skill_script_in_sandbox

DEFAULT_EXCLUDE_DIRS = {".git", ".idea", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}


def _init_abort_state(context):
    state = {"aborted": False, "bridge": None}
    if not context:
        return state
    abort_signal = context.get("abort_signal")
    if not abort_signal:
        return state

    class SignalBridge(QObject):
        def __init__(self, state_ref):
            super().__init__()
            self.state_ref = state_ref

        def trigger(self):
            self.state_ref["aborted"] = True

    bridge = SignalBridge(state)
    abort_signal.connect(bridge.trigger, Qt.DirectConnection)
    state["bridge"] = bridge
    return state


def _decode_bytes(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    candidates = ["utf-8", "utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    if os.name == "nt":
        candidates.extend(["mbcs", "cp936", "gbk", "cp950", "cp932"])
    best = None
    best_score = -1
    for enc in candidates:
        try:
            text = raw.decode(enc, errors="replace")
        except Exception:
            continue
        score = -text.count("�")
        if re.search(r"[\u4e00-\u9fff]", text):
            score += 5
        if score > best_score:
            best_score = score
            best = text
    if best is not None:
        return best
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return str(raw)


def _normalize_exclude_patterns(exclude):
    patterns = set(DEFAULT_EXCLUDE_DIRS)
    if not exclude:
        return patterns
    if isinstance(exclude, str):
        candidates = exclude.split(",")
    elif isinstance(exclude, (list, tuple, set)):
        candidates = exclude
    else:
        candidates = []
    for item in candidates:
        text = str(item or "").strip()
        if text:
            patterns.add(text)
    return patterns


def _resolve_workspace_path(workspace_dir, path="."):
    if not workspace_dir:
        return None, "Error: Workspace not selected."
    root = os.path.abspath(workspace_dir)
    rel = path or "."
    resolved = os.path.abspath(os.path.join(root, rel))
    try:
        common = os.path.commonpath([root, resolved])
    except ValueError:
        return None, "Error: Path is outside the workspace."
    if common != root:
        return None, "Error: Path is outside the workspace."
    return resolved, None


def _to_workspace_relative(file_path, workspace_dir):
    return os.path.relpath(file_path, workspace_dir)


def _path_matches(pattern, rel_path):
    normalized = rel_path.replace("\\", "/")
    basename = os.path.basename(rel_path)
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)


def bash(workspace_dir, command, _context=None):
    """
    在当前沙盒运行时中执行命令。
    """
    try:
        cwd = workspace_dir if workspace_dir else os.getcwd()
        abort_state = _init_abort_state(_context)
        process = run_in_sandbox(
            command,
            cwd=cwd,
            skill_id="command-tools",
            shell_kind="bash",
            text=False,
        )
        while True:
            if abort_state["aborted"]:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                return "Error: Command aborted by user."
            try:
                output_raw, error_raw = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        output = _decode_bytes(output_raw)
        error = _decode_bytes(error_raw)
        output = output or ""
        if error:
            if output:
                output += "\n"
            output += f"STDERR:\n{error}"
        return output if output else "(No output)"
    except Exception as e:
        return f"Error executing command: {str(e)}"


def run_node_code(workspace_dir, code, _context=None):
    """
    Execute JavaScript code with the sandbox Node.js runtime.
    """
    if not workspace_dir:
        return "Error: Workspace not selected."

    node_exe = get_runtime_executable("node")
    if not node_exe:
        return "Error: Bundled Node.js runtime is missing. This package may be corrupted. Please reinstall the application."

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(str(code or ""))
            temp_path = handle.name
    except Exception as e:
        return f"Error creating temp file: {e}"

    try:
        abort_state = _init_abort_state(_context)
        process = run_in_sandbox(
            [node_exe, temp_path],
            cwd=workspace_dir,
            skill_id="command-tools",
            shell_kind="exec",
            text=False,
        )
        while True:
            if abort_state["aborted"]:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                return "Error: Execution aborted by user."
            try:
                output_raw, error_raw = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        output = _decode_bytes(output_raw) or ""
        error = _decode_bytes(error_raw)
        if error:
            if output:
                output += "\n"
            output += f"STDERR:\n{error}"
        return output if output.strip() else "(No output)"
    except Exception as e:
        return f"Error executing Node.js code: {str(e)}"
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _glob_internal(workspace_dir, pattern="*", path=".", limit=200, _context=None):
    """
    在工作区内按路径/文件名模式搜索文件。
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
    pattern = str(pattern or "").strip() or "*"
    start_dir, error = _resolve_workspace_path(workspace_dir, path)
    if error:
        return error
    if not os.path.exists(start_dir):
        return f"Error: Path not found - {path}"
    if not os.path.isdir(start_dir):
        return f"Error: Path is not a directory - {path}"

    try:
        max_results = int(limit) if limit is not None else 200
    except Exception:
        return "Error: limit must be an integer."
    if max_results <= 0:
        return "Error: limit must be greater than 0."

    matches = []
    try:
        for root, dirs, files in os.walk(start_dir):
            dirs[:] = [d for d in dirs if d not in DEFAULT_EXCLUDE_DIRS]
            for file_name in files:
                file_path = os.path.join(root, file_name)
                rel_path = _to_workspace_relative(file_path, workspace_dir)
                if _path_matches(pattern, rel_path):
                    matches.append(rel_path)
                    if len(matches) >= max_results:
                        matches.append("... (Truncated due to result limit)")
                        return "\n".join(matches)
        if not matches:
            return "No matches found."
        return "\n".join(matches)
    except Exception as e:
        return f"Error: {str(e)}"


def _grep_internal(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
    """
    在工作区内搜索文件内容。
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
    start_dir, error = _resolve_workspace_path(workspace_dir, path)
    if error:
        return error
    if not os.path.exists(start_dir):
        return f"Error: Path not found - {path}"
    exclude_patterns = _normalize_exclude_patterns(exclude)

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern - {str(e)}"

    results = []
    match_count = 0
    max_matches = 1000

    try:
        for root, dirs, files in os.walk(start_dir):
            dirs[:] = [d for d in dirs if d not in exclude_patterns]
            for file_name in files:
                if file_name in exclude_patterns:
                    continue
                if not fnmatch.fnmatch(file_name, include):
                    continue
                file_path = os.path.join(root, file_name)
                try:
                    with open(file_path, "rb") as f:
                        is_binary = b"\0" in f.read(1024)
                    if is_binary:
                        continue
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for index, line in enumerate(f, start=1):
                            if regex.search(line):
                                rel_path = _to_workspace_relative(file_path, workspace_dir)
                                results.append(f"{rel_path}:{index}: {line.strip()}")
                                match_count += 1
                                if match_count >= max_matches:
                                    results.append("... (Truncated due to match limit)")
                                    return "\n".join(results)
                except Exception:
                    continue
            if not recursive:
                break
        if not results:
            return "No matches found."
        return "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


def glob(workspace_dir, pattern="*", path=".", limit=200, _context=None):
    return _glob_internal(workspace_dir, pattern=pattern, path=path, limit=limit, _context=_context)


def grep(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
    return _grep_internal(
        workspace_dir,
        pattern=pattern,
        path=path,
        include=include,
        exclude=exclude,
        recursive=recursive,
        _context=_context,
    )


def run_skill_script(skill_name, script_name, args=None, input_text=None, timeout_seconds=120, _context=None):
    """
    执行目标 skill 在 script_entries 中声明的脚本入口。
    """
    skill_name = (skill_name or "").strip()
    script_name = (script_name or "").strip()
    if not skill_name:
        return "Error: skill_name is required."
    if not script_name:
        return "Error: script_name is required."

    skill_manager = (_context or {}).get("skill_manager") if isinstance(_context, dict) else None
    if not skill_manager:
        return "Error: skill manager context is unavailable."

    record = skill_manager.skill_records.get(skill_name)
    if not record:
        return f"Error: Skill '{skill_name}' not found."

    dependency_status = record.get("dependency_status") or {"ok": True}
    if not dependency_status.get("ok"):
        dependency_status = skill_manager._prepare_skill_dependencies(skill_name, record["path"])
        record["dependency_status"] = dependency_status
        if not dependency_status.get("ok"):
            return f"Error: Dependencies for skill '{skill_name}' are not ready: {dependency_status.get('message')}"

    args_list = []
    if isinstance(args, str) and args.strip():
        try:
            args = json.loads(args)
        except Exception:
            args = [args]
    if isinstance(args, list):
        args_list = [str(item) for item in args if item is not None]
    elif args is not None:
        return "Error: args must be a list or JSON list string."

    entries = list((record.get("spec") or {}).get("script_entries") or [])
    target = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_name = str(entry.get("name") or "").strip()
        entry_path = os.path.normpath(str(entry.get("path") or "").strip()) if entry.get("path") else ""
        if script_name == entry_name or script_name == entry_path or script_name == os.path.basename(entry_path):
            target = entry
            break
    if not target:
        return f"Error: Script '{script_name}' not found in skill '{skill_name}'."

    script_rel_path = os.path.normpath(target.get("path") or "")
    script_abs_path = os.path.abspath(os.path.join(record["path"], script_rel_path))
    if not os.path.isfile(script_abs_path):
        return f"Error: Script path '{script_rel_path}' not found for skill '{skill_name}'."

    default_args = target.get("default_args") if isinstance(target.get("default_args"), list) else []
    runtime = str(target.get("runtime") or "bash").strip().lower() or "bash"
    try:
        result = run_skill_script_in_sandbox(
            skill_name,
            script_abs_path,
            runtime,
            args=list(default_args) + args_list,
            cwd=record["path"],
            input_text=input_text,
            timeout_seconds=int(timeout_seconds) if timeout_seconds is not None else 120,
        )
        payload = {
            "skill_name": skill_name,
            "script_name": target.get("name") or script_name,
            "script_path": script_rel_path,
            "runtime": runtime,
            "ok": bool(result.get("ok")),
            "exit_code": result.get("exit_code"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "command": result.get("command", ""),
            "cwd": result.get("cwd", record["path"]),
        }
        return json.dumps(payload, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return f"Error: Script '{script_name}' timed out after {timeout_seconds} seconds."
    except Exception as e:
        return f"Error executing skill script '{script_name}': {str(e)}"


TOOL_EXPORTS = [
    {
        "name": "bash",
        "handler": bash,
        "description": "Execute a shell command in the current sandbox workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute."},
            },
            "required": ["command"],
        },
        "allowed_modes": ["execution"],
        "should_defer": True,
        "search_hint": "shell command build test script",
    },
    {
        "name": "glob",
        "handler": glob,
        "description": "Find workspace files by path or filename pattern. For known filename fragments, prefer patterns like '*report*' instead of '**/report*' so root-level files are not missed.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Filename or relative-path glob pattern. If you know part of the filename, prefer '*name*' such as '*AI 赋能数据分析*'; do not default to '**/name*' because that can miss files in the workspace root."},
                "path": {"type": "string", "description": "Workspace-relative directory to search. Use this to narrow the search scope when the directory is known."},
                "limit": {"type": "integer", "description": "Maximum number of results."},
            },
            "required": [],
        },
        "read_only": True,
        "allowed_modes": ["clarifying", "execution"],
        "search_hint": "file glob find names",
    },
    {
        "name": "grep",
        "handler": grep,
        "description": "Search workspace file contents with a regex pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path": {"type": "string", "description": "Workspace-relative directory to search."},
                "include": {"type": "string", "description": "Filename include glob."},
                "exclude": {"type": "string", "description": "Comma-separated names or patterns to skip."},
                "recursive": {"type": "boolean", "description": "Whether to search recursively."},
            },
            "required": ["pattern"],
        },
        "read_only": True,
        "allowed_modes": ["clarifying", "execution"],
        "search_hint": "grep search code text",
    },
    {
        "name": "run_node_code",
        "handler": run_node_code,
        "description": "Execute JavaScript code with the sandbox Node.js runtime in the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript code to execute with Node.js."},
            },
            "required": ["code"],
        },
        "allowed_modes": ["execution"],
        "should_defer": True,
        "search_hint": "node javascript js execute runtime script",
    },
    {
        "name": "run_skill_script",
        "handler": run_skill_script,
        "description": "Run a script entry declared by a skill.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill that owns the script."},
                "script_name": {"type": "string", "description": "Script entry name or path."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Script arguments."},
                "input_text": {"type": "string", "description": "Optional stdin text."},
                "timeout_seconds": {"type": "integer", "description": "Execution timeout."},
            },
            "required": ["skill_name", "script_name"],
        },
        "allowed_modes": ["execution"],
        "should_defer": True,
        "search_hint": "skill script reusable workflow",
    },
]
