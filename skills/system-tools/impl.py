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
import webbrowser
from core.env_utils import ensure_package_installed, get_app_data_dir, get_python_executable
from PySide6.QtCore import QObject, Qt

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
        
        # Use shell=True to allow shell syntax (pipes, redirects, etc.)
        # On Windows, this uses cmd.exe or powershell depending on the environment/comspec
        abort_state = _init_abort_state(_context)
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False
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

def grep(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
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
        result = subprocess.run(
            [exe_path, "-n", str(limit), query],
            capture_output=True
        )
        if result.returncode != 0:
            err = _decode_bytes(result.stderr).strip() or _decode_bytes(result.stdout).strip()
            return None, err or "Everything CLI failed."
        lines = [line.strip() for line in _decode_bytes(result.stdout).splitlines() if line.strip()]
        return lines, None
    except Exception as e:
        return None, str(e)

def search_files(workspace_dir, query, limit=200, fallback_path=".", use_grep_fallback=True, _context=None):
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
    fallback = grep(
        workspace_dir,
        pattern=query,
        path=fallback_path,
        include="*",
        exclude=None,
        recursive=True,
        _context=_context
    )
    return f"Everything unavailable, fallback to grep in workspace.\n{fallback}"

def build_app_index(refresh=False, limit=2000, refresh_min_interval=60):
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

def find_app(query, limit=10, refresh=False, refresh_min_interval=60):
    query = (query or "").strip()
    if not query:
        return "Error: query is required."
    if refresh:
        build_app_index(refresh=True, refresh_min_interval=refresh_min_interval)
    items = _read_index()
    if not items:
        items = json.loads(build_app_index(refresh=True))
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

def launch_app(name, args=None):
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

def open_with(file, app):
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

def open_url(url):
    url = (url or "").strip()
    if not url:
        return "Error: url is required."
    ok = webbrowser.open(url)
    return "OK" if ok else "Error: failed to open url."

def screenshot_url(url, output_path=None):
    url = (url or "").strip()
    if not url:
        return "Error: url is required."
    _ensure_playwright()
    if not output_path:
        out_dir = os.path.join(get_app_data_dir(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"screenshot_{ts}.png")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    return f"OK: {output_path}"

def run_browser_steps(url=None, steps=None, output_path=None):
    if steps is None:
        return "Error: steps is required."
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            return "Error: steps must be a JSON array."
    if not isinstance(steps, list):
        return "Error: steps must be a list."
    _ensure_playwright()
    if not output_path:
        out_dir = os.path.join(get_app_data_dir(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"automation_{ts}.png")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        if url:
            page.goto(url, wait_until="networkidle")
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = step.get("action")
            if action == "goto":
                page.goto(step.get("url") or url, wait_until=step.get("wait_until") or "networkidle")
            elif action == "click":
                selector = step.get("selector")
                if selector:
                    page.click(selector)
            elif action == "fill":
                selector = step.get("selector")
                text = step.get("text") or ""
                if selector:
                    page.fill(selector, text)
            elif action == "type":
                selector = step.get("selector")
                text = step.get("text") or ""
                if selector:
                    page.type(selector, text)
            elif action == "scroll":
                x = int(step.get("x") or 0)
                y = int(step.get("y") or 0)
                if y == 0:
                    direction = (step.get("direction") or "down").lower()
                    amount = int(step.get("amount") or 1)
                    y = 800 * amount
                    if direction == "up":
                        y = -y
                page.mouse.wheel(x, y)
            elif action == "wait":
                ms = int(step.get("ms") or 500)
                page.wait_for_timeout(ms)
            elif action == "screenshot":
                path = step.get("path") or output_path
                page.screenshot(path=path, full_page=bool(step.get("full_page", True)))
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    return f"OK: {output_path}"

def ui_focus_window(window_title, backend="uia"):
    window_title = (window_title or "").strip()
    if not window_title:
        return "Error: window_title is required."
    wnd = _connect_window(window_title, backend)
    wnd.set_focus()
    return "OK"

def ui_click(window_title, control_title=None, control_type=None, backend="uia"):
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

def ui_type(window_title, text, control_title=None, control_type=None, backend="uia"):
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

def ui_scroll(window_title, direction="down", amount=1, backend="uia"):
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
        out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace").strip()
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
        result = subprocess.run(["where", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
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

def _ensure_playwright():
    ensure_package_installed("playwright")
    python_exe = get_python_executable()
    try:
        subprocess.check_call([python_exe, "-m", "playwright", "install", "chromium"])
    except Exception:
        pass

def _ensure_pywinauto():
    ensure_package_installed("pywinauto")
    from pywinauto import Application
    from pywinauto import mouse
    return Application, mouse

def _connect_window(window_title, backend):
    Application, _ = _ensure_pywinauto()
    app = Application(backend=backend)
    app.connect(title_re=window_title)
    return app.window(title_re=window_title)
