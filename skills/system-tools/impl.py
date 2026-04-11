import os
import subprocess
import re
import fnmatch
import shutil
import locale
import json
import time
import sys
import difflib
import shlex
import base64
import webbrowser
import threading
from collections import deque
import urllib.request
import urllib.parse
from core.env_utils import ensure_package_installed, get_app_data_dir
from core.sandbox_runtime import run_in_sandbox, run_skill_script_in_sandbox
from PySide6.QtCore import QObject, Qt

_ACTION_WINDOW_STATE = {}
_ACTION_WINDOW_LOCK = threading.Lock()

def _is_god_mode(context):
    if context and 'config_manager' in context:
        return context['config_manager'].get_god_mode()
    return False

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

def _cfg_value(context, key, default=None):
    if not context:
        return default
    cfg = context.get("config_manager") if isinstance(context, dict) else None
    if not cfg:
        return default
    try:
        value = cfg.get(key, default)
        return value if value is not None else default
    except Exception:
        return default

def _cfg_bool(context, key, default=False):
    value = _cfg_value(context, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "y", "on"):
            return True
        if lowered in ("0", "false", "no", "n", "off"):
            return False
    return default

def _normalize_action_key(value):
    text = (value or "").strip().lower()
    if len(text) > 160:
        text = text[:160]
    return text

def _hit_action_limit(action, key, context):
    window_seconds = int(_cfg_value(context, "system_automate_action_window_seconds", 120) or 120)
    max_hits = int(_cfg_value(context, "system_automate_action_max_hits", 5) or 5)
    now = time.time()
    bucket_key = f"{action}:{_normalize_action_key(key)}"
    with _ACTION_WINDOW_LOCK:
        q = _ACTION_WINDOW_STATE.get(bucket_key)
        if q is None:
            q = deque()
            _ACTION_WINDOW_STATE[bucket_key] = q
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= max_hits:
            return True, max(0, int(window_seconds - (now - q[0])))
        q.append(now)
        if len(_ACTION_WINDOW_STATE) > 1000:
            stale = [k for k, dq in _ACTION_WINDOW_STATE.items() if (not dq) or (now - dq[-1] > window_seconds * 2)]
            for k in stale[:300]:
                _ACTION_WINDOW_STATE.pop(k, None)
    return False, 0

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

def _no_window_kwargs():
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {"creationflags": creationflags, "startupinfo": startupinfo}

def bash(workspace_dir, command, _context=None):
    """
    Execute a shell command.
    
    Args:
        workspace_dir (str): The current workspace directory.
        command (str): The command to execute.
    """
    try:
        # Check God Mode if strict security is needed, but user requested these as built-in skills.
        # We will assume they are allowed but should be used responsibly.
        
        cwd = workspace_dir if workspace_dir else os.getcwd()
        
        abort_state = _init_abort_state(_context)
        process = run_in_sandbox(
            command,
            cwd=cwd,
            skill_id="system-tools",
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


def run_skill_script(skill_name, script_name, args=None, input_text=None, timeout_seconds=120, _context=None):
    """
    Execute a script declared by an imported skill using the sandbox runtime.
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
            parsed = json.loads(args)
            args = parsed
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

def _standard_step_result(action, result, ok=True, chosen_strategy="", fallback_used=False, capability_notes="", artifacts=None, timings=None):
    return {
        "action": action,
        "ok": bool(ok),
        "result": result,
        "chosen_strategy": chosen_strategy,
        "fallback_used": bool(fallback_used),
        "capability_notes": capability_notes or "",
        "artifacts": artifacts or [],
        "timings": timings or {}
    }

def _grep_impl(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
    """
    Search for a text pattern in files using regex.
    
    Args:
        workspace_dir (str): Root workspace.
        pattern (str): Regex pattern to search.
        path (str): Relative path to start search (default: ".").
        include (str): Glob pattern for files to include (default: "*").
        exclude (str): Glob pattern for files to exclude.
        recursive (bool): Whether to search recursively (default: True).
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
        
    start_dir = os.path.abspath(os.path.join(workspace_dir, path))
    results = []
    
    # Common ignore patterns
    default_excludes = {'.git', '.idea', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build'}
    if exclude:
        exclude_patterns = set(exclude.split(',')) | default_excludes
    else:
        exclude_patterns = default_excludes

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern - {str(e)}"

    match_count = 0
    max_matches = 1000

    try:
        for root, dirs, files in os.walk(start_dir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_patterns]
            
            for file in files:
                if file in exclude_patterns:
                    continue
                
                # Check include pattern
                if not fnmatch.fnmatch(file, include):
                    continue
                    
                file_path = os.path.join(root, file)
                
                # Check binary
                try:
                    # Quick check for binary
                    with open(file_path, 'rb') as f:
                        is_binary = b'\0' in f.read(1024)
                    if is_binary:
                        continue
                        
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if regex.search(line):
                                rel_path = os.path.relpath(file_path, workspace_dir)
                                results.append(f"{rel_path}:{i+1}: {line.strip()}")
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

def _run_everything_search(query, limit=200):
    if os.name != "nt":
        return None, "Everything is only supported on Windows."
    exe_path = shutil.which("es.exe")
    if not exe_path:
        return None, "Everything CLI (es.exe) not found in PATH."
    try:
        result = subprocess.run([exe_path, "-n", str(limit), query], capture_output=True, **_no_window_kwargs())
        if result.returncode != 0:
            err = _decode_bytes(result.stderr).strip() or _decode_bytes(result.stdout).strip()
            return None, err or "Everything CLI failed."
        lines = [line.strip() for line in _decode_bytes(result.stdout).splitlines() if line.strip()]
        return lines, None
    except Exception as e:
        return None, str(e)

def _search_files_impl(workspace_dir, query, limit=200, fallback_path=".", use_grep_fallback=True, _context=None):
    """
    Search for files and folders using Everything CLI when available.
    Falls back to grep in the workspace when Everything is unavailable.
    
    Args:
        workspace_dir (str): Root workspace (used for fallback only).
        query (str): Search query (Everything syntax supported).
        limit (int): Maximum results to return (default 200).
        fallback_path (str): Workspace-relative path for fallback grep.
        use_grep_fallback (bool): Whether to fall back to grep (default True).
    """
    if not query or not str(query).strip():
        return "Error: Query cannot be empty."
    results, error = _run_everything_search(str(query), limit=limit)
    if results is not None:
        if not results:
            return "No matches found."
        return "\n".join(results)
    if not use_grep_fallback:
        return f"Everything unavailable: {error}"
    fallback = _grep_impl(
        workspace_dir,
        pattern=query,
        path=fallback_path,
        include="*",
        exclude=None,
        recursive=True,
        _context=_context
    )
    return f"Everything unavailable, fallback to grep in workspace.\n{fallback}"

def _build_app_index_impl(refresh=False, limit=2000, refresh_min_interval=60):
    existing = _read_index()
    if not refresh and existing:
        return json.dumps(existing, ensure_ascii=False, indent=2)
    if refresh:
        meta = _read_meta()
        last_ts = int(meta.get("last_refresh_ts") or 0)
        if last_ts and (time.time() - last_ts) < int(refresh_min_interval):
            if existing:
                return json.dumps(existing, ensure_ascii=False, indent=2)
    limit = int(limit) if limit else 2000
    items = []
    items.extend(_scan_start_menu(limit))
    remaining = max(0, limit - len(items))
    if remaining:
        items.extend(_scan_registry(remaining))
    remaining = max(0, limit - len(items))
    if remaining:
        items.extend(_scan_path(remaining))
    dedup = {}
    for item in items:
        path = item.get("path")
        name = item.get("name")
        if not path or not name:
            continue
        dedup[path] = item
    result = list(dedup.values())
    _write_index(result)
    _write_meta({"last_refresh_ts": int(time.time())})
    return json.dumps(result, ensure_ascii=False, indent=2)

def _find_app_impl(query, limit=10, refresh=False, refresh_min_interval=60):
    query = (query or "").strip()
    if not query:
        return "Error: query is required."
    if refresh:
        _build_app_index_impl(refresh=True, refresh_min_interval=refresh_min_interval)
    items = _read_index()
    if not items:
        items = json.loads(_build_app_index_impl(refresh=True))
    q = query.lower()
    scored = []
    for item in items:
        name = (item.get("name") or "").lower()
        if not name:
            continue
        score = 0
        if name == q:
            score = 3
        elif q in name:
            score = 2
        else:
            ratio = difflib.SequenceMatcher(None, q, name).ratio()
            if ratio >= 0.6:
                score = 1
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    result = [s[1] for s in scored][: int(limit)]
    return json.dumps(result, ensure_ascii=False, indent=2)

def _launch_app_impl(name, args=None):
    name = (name or "").strip()
    if not name:
        return "Error: name is required."
    if os.path.exists(name):
        _launch(name, args)
        return f"OK: launched {name}"
    items = _read_index()
    target = _find_best(name, items) if items else None
    if target and os.path.exists(target.get("path", "")):
        _launch(target["path"], args)
        return f"OK: launched {target['name']}"
    resolved = _resolve_by_where(name if name.lower().endswith(".exe") else f"{name}.exe")
    if resolved and os.path.exists(resolved):
        _launch(resolved, args)
        return f"OK: launched {resolved}"
    return f"Error: app not found for '{name}'. Consider building app index first."

def _open_with_impl(file, app):
    file = (file or "").strip()
    app = (app or "").strip()
    if not file or not app:
        return "Error: file and app are required."
    if not os.path.exists(file):
        return f"Error: file not found: {file}"
    app_path = app if os.path.exists(app) else None
    if not app_path:
        items = _read_index()
        target = _find_best(app, items) if items else None
        if target and os.path.exists(target.get("path", "")):
            app_path = target["path"]
    if not app_path:
        resolved = _resolve_by_where(app if app.lower().endswith(".exe") else f"{app}.exe")
        if resolved and os.path.exists(resolved):
            app_path = resolved
    if not app_path:
        return f"Error: app not found for '{app}'. Consider building app index first."
    subprocess.Popen([app_path, file])
    return f"OK: opened {file} with {app_path}"

def _open_url_impl(url):
    url = (url or "").strip()
    if not url:
        return "Error: url is required."
    ok = webbrowser.open(url)
    return "OK" if ok else "Error: failed to open url."

def _default_screenshot_path(prefix):
    out_dir = os.path.join(get_app_data_dir(), "screenshots")
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"{prefix}_{ts}.png")

def _find_browser_executable():
    candidates = [
        _resolve_by_where("msedge.exe"),
        _resolve_by_where("chrome.exe"),
    ]
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")]
    suffixes = [
        os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join("Google", "Chrome", "Application", "chrome.exe"),
    ]
    for base in program_files:
        if not base:
            continue
        for suffix in suffixes:
            candidates.append(os.path.join(base, suffix))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None

def _cdp_http_json(url, timeout=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)

def _cdp_request_new_page(port, url):
    encoded = urllib.parse.quote(url or "about:blank", safe=":/?=&%#")
    endpoint = f"http://127.0.0.1:{int(port)}/json/new?{encoded}"
    methods = ["PUT", "GET"]
    last_error = ""
    for method in methods:
        try:
            req = urllib.request.Request(endpoint, method=method, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw)
            ws_url = body.get("webSocketDebuggerUrl") if isinstance(body, dict) else None
            if ws_url:
                return ws_url, ""
        except Exception as e:
            last_error = str(e)
            continue
    try:
        tabs = _cdp_http_json(f"http://127.0.0.1:{int(port)}/json/list", timeout=3)
        if isinstance(tabs, list):
            for tab in tabs:
                if isinstance(tab, dict) and tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                    return tab.get("webSocketDebuggerUrl"), ""
    except Exception as e:
        last_error = str(e)
    return "", last_error or "cdp_new_page_failed"

def _ensure_cdp_ready(start_url, _context=None):
    port = int(_cfg_value(_context, "browser_cdp_port", 9222) or 9222)
    browser_exe = _find_browser_executable()
    if not browser_exe:
        return "", "browser_executable_not_found"
    launched = False
    try:
        version = _cdp_http_json(f"http://127.0.0.1:{port}/json/version", timeout=1.2)
        if not isinstance(version, dict):
            raise RuntimeError("invalid_cdp_version")
    except Exception:
        profile_dir = os.path.join(get_app_data_dir(), "cdp_profile")
        os.makedirs(profile_dir, exist_ok=True)
        args = [
            browser_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            start_url or "about:blank"
        ]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
        except Exception as e:
            return "", f"cdp_launch_failed:{e}"
    deadline = time.time() + (8.0 if launched else 3.0)
    last_error = ""
    while time.time() < deadline:
        try:
            version = _cdp_http_json(f"http://127.0.0.1:{port}/json/version", timeout=1.2)
            if isinstance(version, dict):
                ws_url, ws_err = _cdp_request_new_page(port, start_url or "about:blank")
                if ws_url:
                    return ws_url, ""
                last_error = ws_err
        except Exception as e:
            last_error = str(e)
        time.sleep(0.3)
    return "", last_error or "cdp_endpoint_unavailable"

class _CDPSession:
    def __init__(self, ws_url):
        ensure_package_installed("websocket-client", "websocket", skill_id="system-tools")
        from websocket import create_connection
        self._ws = create_connection(ws_url, timeout=10)
        self._next_id = 1
        self._ws.settimeout(10)

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass

    def call(self, method, params=None):
        call_id = self._next_id
        self._next_id += 1
        payload = {"id": call_id, "method": method, "params": params or {}}
        self._ws.send(json.dumps(payload, ensure_ascii=False))
        while True:
            message = self._ws.recv()
            if not message:
                continue
            data = json.loads(message)
            if data.get("id") != call_id:
                continue
            if data.get("error"):
                raise RuntimeError(str(data.get("error")))
            return data.get("result") or {}

def _cdp_eval(session, expression):
    result = session.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    value_obj = (result.get("result") or {})
    return value_obj.get("value")

def _cdp_wait_document_ready(session, abort_state, timeout_seconds=15):
    deadline = time.time() + max(float(timeout_seconds), 1.0)
    while time.time() < deadline:
        if abort_state["aborted"]:
            return False
        try:
            ready = _cdp_eval(session, "document.readyState")
            if ready in ("interactive", "complete"):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

def _cdp_capture_png(session, output_path, full_page=True):
    if full_page:
        try:
            session.call("Page.getLayoutMetrics")
        except Exception:
            pass
    result = session.call("Page.captureScreenshot", {"format": "png", "fromSurface": True, "captureBeyondViewport": bool(full_page)})
    raw = result.get("data")
    if not raw:
        raise RuntimeError("empty_screenshot_data")
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(raw))

def _screenshot_url_impl(url, output_path=None, _context=None):
    url = (url or "").strip()
    if not url:
        return "Error: url is required."
    start_ts = time.time()
    if not output_path:
        output_path = _default_screenshot_path("screenshot")
    ws_url, err = _ensure_cdp_ready(url, _context=_context)
    if not ws_url:
        _open_url_impl(url)
        time.sleep(1.2)
        fallback_path, fallback_err = _capture_browser_window(output_path=output_path, title_hint=_extract_host_hint(url))
        if fallback_path:
            end_ts = time.time()
            return _standard_step_result("screenshot_url", "OK", ok=True, chosen_strategy="default_window", fallback_used=True, capability_notes="browser_window_capture", artifacts=[{"type": "screenshot", "path": fallback_path}], timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
        end_ts = time.time()
        return _standard_step_result("screenshot_url", f"Error: cdp_unavailable:{err};fallback_capture_failed:{fallback_err}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    session = None
    try:
        session = _CDPSession(ws_url)
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": url})
        _cdp_wait_document_ready(session, {"aborted": False}, timeout_seconds=20)
        _cdp_capture_png(session, output_path, full_page=True)
        end_ts = time.time()
        return _standard_step_result("screenshot_url", "OK", chosen_strategy="cdp_native", artifacts=[{"type": "screenshot", "path": output_path}], timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    except Exception as e:
        _open_url_impl(url)
        time.sleep(1.2)
        fallback_path, fallback_err = _capture_browser_window(output_path=output_path, title_hint=_extract_host_hint(url))
        if fallback_path:
            end_ts = time.time()
            return _standard_step_result("screenshot_url", "OK", ok=True, chosen_strategy="default_window", fallback_used=True, capability_notes="browser_window_capture_after_cdp_failure", artifacts=[{"type": "screenshot", "path": fallback_path}], timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
        end_ts = time.time()
        return _standard_step_result("screenshot_url", f"Error: cdp_failed:{str(e)};fallback_capture_failed:{fallback_err}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    finally:
        if session:
            session.close()

def _run_browser_steps_impl(url=None, steps=None, output_path=None, _context=None):
    if steps is None:
        return _standard_step_result("run_browser_steps", "Error: steps is required.", ok=False)
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            return _standard_step_result("run_browser_steps", "Error: steps must be a JSON array.", ok=False)
    if not isinstance(steps, list):
        return _standard_step_result("run_browser_steps", "Error: steps must be a list.", ok=False)
    start_ts = time.time()
    abort_state = _init_abort_state(_context)
    goto_url = (url or "").strip()
    screenshot_needed = False
    if not output_path:
        output_path = _default_screenshot_path("automation")
    for s in steps:
        if isinstance(s, dict) and (s.get("action") or "").strip() == "goto":
            maybe_url = (s.get("url") or "").strip()
            if maybe_url:
                goto_url = maybe_url
        if isinstance(s, dict) and (s.get("action") or "").strip() == "screenshot":
            screenshot_needed = True
            maybe_path = s.get("path") or s.get("output_path")
            if maybe_path:
                output_path = maybe_path
    ws_url, cdp_err = _ensure_cdp_ready(goto_url or "about:blank", _context=_context)
    if not ws_url:
        if goto_url:
            _open_url_impl(goto_url)
        if screenshot_needed and goto_url:
            time.sleep(1.2)
            fallback_path, fallback_err = _capture_browser_window(output_path=output_path, title_hint=_extract_host_hint(goto_url))
            if fallback_path:
                end_ts = time.time()
                return _standard_step_result("run_browser_steps", "OK", ok=True, chosen_strategy="default_window", fallback_used=True, capability_notes="browser_window_capture", artifacts=[{"type": "screenshot", "path": fallback_path}], timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
            end_ts = time.time()
            return _standard_step_result("run_browser_steps", f"Error: cdp_unavailable:{cdp_err};fallback_capture_failed:{fallback_err}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
        end_ts = time.time()
        return _standard_step_result("run_browser_steps", f"Error: cdp_unavailable:{cdp_err}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    session = None
    artifacts = []
    try:
        session = _CDPSession(ws_url)
        session.call("Page.enable")
        session.call("Runtime.enable")
        for step in steps:
            if abort_state["aborted"]:
                raise RuntimeError("Command aborted by user.")
            if not isinstance(step, dict):
                continue
            action = (step.get("action") or "").strip()
            if action == "goto":
                target_url = (step.get("url") or goto_url or "").strip()
                if not target_url:
                    continue
                session.call("Page.navigate", {"url": target_url})
                _cdp_wait_document_ready(session, abort_state, timeout_seconds=20)
            elif action == "click":
                selector = step.get("selector")
                if selector:
                    js = f"""(function(){{var el=document.querySelector({json.dumps(selector)});if(!el)return false;el.click();return true;}})()"""
                    _cdp_eval(session, js)
            elif action == "fill":
                selector = step.get("selector")
                text_value = step.get("text") or ""
                if selector:
                    js = f"""(function(){{var el=document.querySelector({json.dumps(selector)});if(!el)return false;el.focus();el.value={json.dumps(text_value)};el.dispatchEvent(new Event('input',{{bubbles:true}}));el.dispatchEvent(new Event('change',{{bubbles:true}}));return true;}})()"""
                    _cdp_eval(session, js)
            elif action == "type":
                selector = step.get("selector")
                text_value = step.get("text") or ""
                if selector:
                    js = f"""(function(){{var el=document.querySelector({json.dumps(selector)});if(!el)return false;el.focus();el.value=(el.value||"")+{json.dumps(text_value)};el.dispatchEvent(new Event('input',{{bubbles:true}}));return true;}})()"""
                    _cdp_eval(session, js)
            elif action == "scroll":
                x = int(step.get("x") or 0)
                y = int(step.get("y") or 0)
                if y == 0:
                    direction = (step.get("direction") or "down").lower()
                    amount = int(step.get("amount") or 1)
                    y = 800 * amount
                    if direction == "up":
                        y = -y
                _cdp_eval(session, f"window.scrollBy({int(x)}, {int(y)}); true;")
            elif action == "wait":
                ms = int(step.get("ms") or 500)
                wait_until = time.time() + max(ms, 0) / 1000.0
                while time.time() < wait_until:
                    if abort_state["aborted"]:
                        raise RuntimeError("Command aborted by user.")
                    time.sleep(0.1)
            elif action == "screenshot":
                path = step.get("path") or step.get("output_path") or output_path
                full_page = bool(step.get("full_page", True))
                _cdp_capture_png(session, path, full_page=full_page)
                artifacts.append({"type": "screenshot", "path": path})
        if not artifacts:
            _cdp_capture_png(session, output_path, full_page=True)
            artifacts.append({"type": "screenshot", "path": output_path})
        end_ts = time.time()
        return _standard_step_result("run_browser_steps", "OK", chosen_strategy="cdp_native", artifacts=artifacts, timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    except Exception as e:
        if goto_url:
            _open_url_impl(goto_url)
        if screenshot_needed and goto_url:
            time.sleep(1.2)
            fallback_path, fallback_err = _capture_browser_window(output_path=output_path, title_hint=_extract_host_hint(goto_url))
            if fallback_path:
                end_ts = time.time()
                return _standard_step_result("run_browser_steps", "OK", ok=True, chosen_strategy="default_window", fallback_used=True, capability_notes="browser_window_capture_after_cdp_failure", artifacts=[{"type": "screenshot", "path": fallback_path}], timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
            end_ts = time.time()
            return _standard_step_result("run_browser_steps", f"Error: cdp_failed:{str(e)};fallback_capture_failed:{fallback_err}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
        end_ts = time.time()
        return _standard_step_result("run_browser_steps", f"Error: cdp_failed:{str(e)}", ok=False, chosen_strategy="default_window", fallback_used=True, capability_notes="opened_url_only", timings={"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)})
    finally:
        if session:
            session.close()

def _ui_focus_window_impl(window_title, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    wnd = _connect_window(window_title, backend)
    wnd.set_focus()
    return "OK"

def _ui_click_impl(window_title, control_title=None, control_type=None, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    wnd = _connect_window(window_title, backend)
    wnd.set_focus()
    if control_title or control_type:
        ctrl = wnd.child_window(title=control_title, control_type=control_type)
        ctrl.wait("visible", timeout=5)
        ctrl.click_input()
    else:
        wnd.click_input()
    return "OK"

def _ui_type_impl(window_title, text, control_title=None, control_type=None, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    wnd = _connect_window(window_title, backend)
    wnd.set_focus()
    if control_title or control_type:
        ctrl = wnd.child_window(title=control_title, control_type=control_type)
        ctrl.wait("visible", timeout=5)
        try:
            ctrl.type_keys(text, with_spaces=True, set_foreground=True)
        except Exception:
            ctrl.set_edit_text(text)
    else:
        wnd.type_keys(text, with_spaces=True, set_foreground=True)
    return "OK"

def _ui_scroll_impl(window_title, direction="down", amount=1, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    wnd = _connect_window(window_title, backend)
    wnd.set_focus()
    _, mouse = _ensure_pywinauto()
    direction = (direction or "down").lower()
    dist = int(amount) * 120
    if direction == "up":
        dist = -dist
    mouse.scroll(wheel_dist=dist)
    return "OK"

def _screenshot_window_impl(window_title, output_path=None, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    if not output_path:
        out_dir = os.path.join(get_app_data_dir(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"window_{ts}.png")
    wnd = _connect_window(window_title, backend)
    try:
        image = wnd.capture_as_image()
        image.save(output_path)
    except Exception as e:
        return f"Error: screenshot_window_failed:{str(e)}"
    return f"OK: {output_path}"

def _extract_host_hint(url):
    text = (url or "").strip()
    m = re.match(r"^https?://([^/]+)", text, re.IGNORECASE)
    if not m:
        return ""
    host = m.group(1).split(":", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host

def _capture_browser_window(output_path=None, title_hint="", timeout_seconds=8):
    if not output_path:
        out_dir = os.path.join(get_app_data_dir(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"browser_{ts}.png")
    ensure_package_installed("pywinauto", skill_id="system-tools")
    from pywinauto import Desktop
    hint = (title_hint or "").strip().lower()
    hint_parts = [p for p in re.split(r"[\.\-_]+", hint) if p]
    include = ["edge", "chrome", "firefox", "browser", "浏览器", "xiaohongshu", "小红书"] + hint_parts
    exclude = ["powershell", "cmd", "terminal", "命令提示符", "trae", "code"]
    deadline = time.time() + max(int(timeout_seconds), 1)
    while time.time() < deadline:
        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            windows = []
        candidates = []
        for w in windows:
            try:
                title = (w.window_text() or "").strip()
            except Exception:
                continue
            if not title:
                continue
            low = title.lower()
            if any(token in low for token in exclude):
                continue
            score = 0
            if any(token in low for token in include):
                score += 3
            try:
                if w.is_visible():
                    score += 1
            except Exception:
                pass
            if score > 0:
                candidates.append((score, w))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            target = candidates[0][1]
            try:
                target.set_focus()
            except Exception:
                pass
            try:
                image = target.capture_as_image()
                image.save(output_path)
                return output_path, ""
            except Exception as e:
                return "", str(e)
        time.sleep(0.5)
    return "", "browser_window_not_found"

def _index_path():
    return os.path.join(get_app_data_dir(), "app_index.json")

def _meta_path():
    return os.path.join(get_app_data_dir(), "app_index_meta.json")

def _read_index():
    path = _index_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _read_meta():
    path = _meta_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _write_index(items):
    path = _index_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _write_meta(meta):
    path = _meta_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _normalize_path(p):
    if not p:
        return None
    p = p.strip().strip('"')
    if "," in p:
        cand = p.split(",", 1)[0].strip().strip('"')
        if os.path.exists(cand):
            p = cand
    if os.path.exists(p):
        return p
    return None

def _resolve_lnk(path):
    if not os.path.exists(path):
        return None
    ps_path = path.replace("'", "''")
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"$sh=New-Object -ComObject WScript.Shell;$lnk=$sh.CreateShortcut('{ps_path}');$lnk.TargetPath"
    ]
    try:
        out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace", **_no_window_kwargs()).strip()
        return _normalize_path(out)
    except Exception:
        return None

def _scan_start_menu(limit):
    results = []
    dirs = []
    if os.environ.get("PROGRAMDATA"):
        dirs.append(os.path.join(os.environ["PROGRAMDATA"], "Microsoft", "Windows", "Start Menu", "Programs"))
    if os.environ.get("APPDATA"):
        dirs.append(os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs"))
    count = 0
    for base in dirs:
        for root, _, files in os.walk(base):
            for name in files:
                if not name.lower().endswith(".lnk"):
                    continue
                lnk_path = os.path.join(root, name)
                target = _resolve_lnk(lnk_path)
                if target:
                    results.append({"name": os.path.splitext(name)[0], "path": target, "source": "start_menu"})
                    count += 1
                    if count >= limit:
                        return results
    return results

def _scan_registry(limit):
    results = []
    if sys.platform != "win32":
        return results
    try:
        import winreg
    except Exception:
        return results
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    count = 0
    for root, key_path in roots:
        try:
            with winreg.OpenKey(root, key_path) as key:
                sub_count, _, _ = winreg.QueryInfoKey(key)
                for i in range(sub_count):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as sub_key:
                            try:
                                display = winreg.QueryValueEx(sub_key, "DisplayName")[0]
                            except Exception:
                                display = None
                            try:
                                icon = winreg.QueryValueEx(sub_key, "DisplayIcon")[0]
                            except Exception:
                                icon = None
                            try:
                                install = winreg.QueryValueEx(sub_key, "InstallLocation")[0]
                            except Exception:
                                install = None
                            path = _normalize_path(icon) or _normalize_path(install)
                            if display and path:
                                results.append({"name": display, "path": path, "source": "registry"})
                                count += 1
                                if count >= limit:
                                    return results
                    except Exception:
                        continue
        except Exception:
            continue
    return results

def _scan_path(limit):
    results = []
    seen = set()
    count = 0
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if not p or not os.path.isdir(p):
            continue
        try:
            for name in os.listdir(p):
                if not name.lower().endswith((".exe", ".bat", ".cmd")):
                    continue
                full = os.path.join(p, name)
                if full in seen:
                    continue
                seen.add(full)
                results.append({"name": os.path.splitext(name)[0], "path": full, "source": "path"})
                count += 1
                if count >= limit:
                    return results
        except Exception:
            continue
    return results

def _find_best(query, items):
    q = query.lower()
    best = None
    best_score = 0.0
    for item in items:
        name = (item.get("name") or "").lower()
        if not name:
            continue
        if name == q:
            return item
        if q in name:
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, q, name).ratio()
        if score > best_score:
            best_score = score
            best = item
    return best

def _resolve_by_where(name):
    try:
        result = subprocess.run(["where", name], capture_output=True, text=True, encoding="utf-8", errors="replace", **_no_window_kwargs())
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if lines:
                return lines[0]
    except Exception:
        pass
    return None

def _launch(path, args):
    if args:
        if isinstance(args, str):
            args_list = shlex.split(args)
        elif isinstance(args, list):
            args_list = args
        else:
            args_list = [str(args)]
        subprocess.Popen([path] + args_list)
    else:
        subprocess.Popen([path])

def system_automate(steps, workspace_dir=None, _context=None):
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            return "Error: steps must be a list or valid JSON list."
    if not isinstance(steps, list) or not steps:
        return "Error: steps must be a non-empty list."
    if not workspace_dir:
        workspace_dir = _cfg_value(_context, "default_workspace", "") or ""
    start_ts = time.time()
    abort_state = _init_abort_state(_context)
    results = []
    ok = True
    web_actions = {"goto", "click", "fill", "type", "scroll", "wait", "screenshot"}
    web_buffer = []
    web_url = None
    web_output = None
    web_opened = False
    def flush_web():
        nonlocal web_buffer, web_url, web_output, ok, web_opened
        if not web_buffer:
            return
        if abort_state["aborted"]:
            step_result = _standard_step_result("web_steps", "Error: Command aborted by user.", ok=False, chosen_strategy="default_window")
            results.append(step_result)
            ok = False
            web_buffer = []
            web_url = None
            web_output = None
            return
        if all((step.get("action") or "").strip() == "goto" for step in web_buffer):
            res = _open_url_impl(web_url or (web_buffer[-1].get("url") if web_buffer else ""))
            step_ok = not (isinstance(res, str) and res.startswith("Error:"))
            step_result = _standard_step_result("goto", res, ok=step_ok, chosen_strategy="default_window")
        else:
            res = _run_browser_steps_impl(web_url, web_buffer, web_output, _context=_context)
            if isinstance(res, dict) and "ok" in res and "result" in res:
                step_result = res
            else:
                step_ok = not (isinstance(res, str) and res.startswith("Error:"))
                step_result = _standard_step_result("web_steps", res, ok=step_ok, chosen_strategy="cdp_native")
        results.append(step_result)
        if not step_result.get("ok", False):
            ok = False
        web_buffer = []
        web_url = None
        web_output = None
    for idx, step in enumerate(steps):
        if abort_state["aborted"]:
            step_result = _standard_step_result("system_automate", "Error: Command aborted by user.", ok=False)
            results.append(step_result)
            ok = False
            break
        if not isinstance(step, dict):
            return f"Error: steps[{idx}] must be an object."
        action = (step.get("action") or "").strip()
        if not action:
            return f"Error: steps[{idx}] action is required."
        if action in web_actions:
            if action == "goto":
                url_value = (step.get("url") or "").strip()
                if url_value:
                    web_url = url_value
                    if not web_opened:
                        _open_url_impl(web_url)
                        web_opened = True
            if action == "screenshot":
                web_output = step.get("output_path") or step.get("path") or web_output
            web_buffer.append(step)
            continue
        flush_web()
        if action in ("find", "find_app", "search", "index", "build_app_index"):
            limit_key = step.get("query") or step.get("pattern") or step.get("name") or action
            hit, wait_seconds = _hit_action_limit(action, str(limit_key), _context)
            if hit:
                step_result = _standard_step_result(
                    action,
                    f"Error: action_rate_limited:{action}; retry_after={wait_seconds}s",
                    ok=False,
                    fallback_used=True,
                    capability_notes="rate_limit_protection"
                )
                results.append(step_result)
                ok = False
                continue
        if action == "run_browser_steps":
            step_result = _run_browser_steps_impl(step.get("url"), step.get("steps"), step.get("output_path"), _context=_context)
        elif action in ("ui_focus_window", "focus_window"):
            res = _ui_focus_window_impl(step.get("window_title"), step.get("backend", "uia"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")), chosen_strategy="desktop_window")
        elif action in ("ui_click", "click_window"):
            res = _ui_click_impl(step.get("window_title"), step.get("control_title"), step.get("control_type"), step.get("backend", "uia"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")), chosen_strategy="desktop_window")
        elif action in ("ui_type", "type"):
            res = _ui_type_impl(step.get("window_title"), step.get("text"), step.get("control_title"), step.get("control_type"), step.get("backend", "uia"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")), chosen_strategy="desktop_window")
        elif action in ("ui_scroll", "scroll_window"):
            res = _ui_scroll_impl(step.get("window_title"), step.get("direction", "down"), step.get("amount", 1), step.get("backend", "uia"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")), chosen_strategy="desktop_window")
        elif action == "screenshot_window":
            res = _screenshot_window_impl(step.get("window_title"), step.get("output_path"), step.get("backend", "uia"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")), chosen_strategy="desktop_window")
        elif action in ("launch", "launch_app"):
            res = _launch_app_impl(step.get("name"), step.get("args"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        elif action == "open_with":
            res = _open_with_impl(step.get("file"), step.get("app"))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        elif action in ("find", "find_app"):
            res = _find_app_impl(step.get("query"), step.get("limit", 10), step.get("refresh", False), step.get("refresh_min_interval", 60))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        elif action in ("index", "build_app_index"):
            res = _build_app_index_impl(step.get("refresh", False), step.get("limit", 2000), step.get("refresh_min_interval", 60))
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        elif action == "search":
            kind = (step.get("kind") or "everything").strip().lower()
            if kind == "grep":
                res = _grep_impl(workspace_dir, step.get("pattern") or step.get("query"), step.get("path", "."), step.get("include", "*"), step.get("exclude"), step.get("recursive", True), _context=_context)
            else:
                res = _search_files_impl(workspace_dir, step.get("query"), step.get("limit", 200), step.get("fallback_path", "."), step.get("use_grep_fallback", True), _context=_context)
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        elif action == "bash":
            res = bash(workspace_dir, step.get("command"), _context=_context)
            step_result = _standard_step_result(action, res, ok=not (isinstance(res, str) and res.startswith("Error:")))
        else:
            step_result = _standard_step_result(action, f"Error: unsupported action {action}", ok=False)
        results.append(step_result)
        if not step_result.get("ok", False):
            ok = False
    flush_web()
    end_ts = time.time()
    return json.dumps({
        "ok": ok,
        "results": results,
        "timings": {"start_ms": int(start_ts * 1000), "end_ms": int(end_ts * 1000), "elapsed_ms": int((end_ts - start_ts) * 1000)}
    }, ensure_ascii=False)

def _unwrap_single_result(payload):
    if not isinstance(payload, (str, dict)):
        return payload
    data = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except Exception:
            return payload
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        return payload
    item = results[0]
    result = item.get("result")
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return result

def build_app_index(refresh=False, limit=2000, refresh_min_interval=60, _context=None):
    payload = system_automate([{"action": "index", "refresh": refresh, "limit": limit, "refresh_min_interval": refresh_min_interval}], _context=_context)
    return _unwrap_single_result(payload)

def find_app(query, limit=10, refresh=False, refresh_min_interval=60, _context=None):
    payload = system_automate([{"action": "find", "query": query, "limit": limit, "refresh": refresh, "refresh_min_interval": refresh_min_interval}], _context=_context)
    return _unwrap_single_result(payload)

def launch_app(name, args=None, _context=None):
    payload = system_automate([{"action": "launch", "name": name, "args": args}], _context=_context)
    return _unwrap_single_result(payload)

def open_with(file, app, _context=None):
    payload = system_automate([{"action": "open_with", "file": file, "app": app}], _context=_context)
    return _unwrap_single_result(payload)

def search_files(workspace_dir, query, limit=200, fallback_path=".", use_grep_fallback=True, _context=None):
    payload = system_automate([{"action": "search", "kind": "everything", "query": query, "limit": limit, "fallback_path": fallback_path, "use_grep_fallback": use_grep_fallback}], workspace_dir=workspace_dir, _context=_context)
    return _unwrap_single_result(payload)

def grep(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
    payload = system_automate([{"action": "search", "kind": "grep", "pattern": pattern, "path": path, "include": include, "exclude": exclude, "recursive": recursive}], workspace_dir=workspace_dir, _context=_context)
    return _unwrap_single_result(payload)

def _ensure_pywinauto():
    ensure_package_installed("pywinauto", skill_id="system-tools")
    from pywinauto import Application
    from pywinauto import mouse
    return Application, mouse

def _connect_window(window_title, backend):
    Application, _ = _ensure_pywinauto()
    app = Application(backend=backend)
    app.connect(title_re=window_title)
    return app.window(title_re=window_title)
