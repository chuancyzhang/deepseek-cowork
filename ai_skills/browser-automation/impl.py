import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

from core.env_utils import ensure_package_installed, get_app_data_dir
from core.process_utils import subprocess_kwargs_no_window
from PySide6.QtCore import QObject, Qt


_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_MODES = {"persistent", "isolated", "existing"}


def _json(payload):
    return json.dumps(payload, ensure_ascii=False)


def _now_ms():
    return int(time.time() * 1000)


def _playwright_sync():
    ensure_package_installed("playwright", skill_id="browser-automation")
    from playwright.sync_api import sync_playwright

    return sync_playwright


def _uiautomation():
    ensure_package_installed("uiautomation", skill_id="browser-automation")
    import uiautomation as auto

    return auto


def _safe_session_id(value):
    value = str(value or "default").strip()
    if not _SESSION_ID_RE.fullmatch(value):
        raise ValueError("session_id must contain only letters, digits, dot, underscore, or hyphen")
    return value


def _workspace_root(workspace_dir, context):
    if workspace_dir:
        return os.path.abspath(workspace_dir)
    if isinstance(context, dict):
        config = context.get("config_manager")
        if config:
            try:
                value = config.get("default_workspace", "")
                if value:
                    return os.path.abspath(value)
            except Exception:
                pass
    return os.getcwd()


def _is_within(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except Exception:
        return False


def _validate_url(url, workspace_root):
    value = str(url or "").strip()
    if not value:
        raise ValueError("url is required")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https", "about"}:
        return value
    if parsed.scheme == "file":
        local_path = urllib.parse.unquote(parsed.path)
        if os.name == "nt" and local_path.startswith("/"):
            local_path = local_path[1:]
        if _is_within(local_path, workspace_root):
            return value
        raise ValueError("file URL must stay inside the workspace")
    raise ValueError("only http, https, about, and workspace file URLs are allowed")


def _safe_output_path(value, workspace_root, prefix="browser"):
    app_root = os.path.abspath(os.path.join(get_app_data_dir(), "browser-automation"))
    if value:
        path = os.path.abspath(str(value))
        if not (_is_within(path, workspace_root) or _is_within(path, app_root)):
            raise ValueError("output path must stay inside the workspace or app data directory")
    else:
        path = os.path.join(workspace_root, "images", f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _find_chrome_executable(playwright=None):
    candidates = [shutil.which("chrome"), shutil.which("chrome.exe"), shutil.which("msedge.exe")]
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), local]
    suffixes = [
        os.path.join("Google", "Chrome", "Application", "chrome.exe"),
        os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for root in program_files:
        for suffix in suffixes:
            if root:
                candidates.append(os.path.join(root, suffix))
    if playwright is not None:
        try:
            candidates.append(playwright.chromium.executable_path)
        except Exception:
            pass
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _chrome_profile_candidates(channel="stable"):
    local = os.environ.get("LOCALAPPDATA", "")
    suffix = {
        "stable": os.path.join("Google", "Chrome", "User Data"),
        "beta": os.path.join("Google", "Chrome Beta", "User Data"),
        "dev": os.path.join("Google", "Chrome Dev", "User Data"),
        "canary": os.path.join("Google", "Chrome SxS", "User Data"),
    }.get(str(channel or "stable").lower())
    return [os.path.join(local, suffix)] if local and suffix else []


def _read_devtools_endpoint(profile_dir, timeout_seconds=10):
    port_file = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = time.time() + max(float(timeout_seconds), 0.5)
    last_error = "DevToolsActivePort was not found"
    while time.time() < deadline:
        try:
            lines = [line.strip() for line in Path(port_file).read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                return f"http://127.0.0.1:{int(lines[0])}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(last_error)


def _get_session(session_id):
    with _SESSIONS_LOCK:
        state = _SESSIONS.get(session_id)
        if state is None:
            state = {"lock": threading.Lock(), "process": None, "profile_dir": "", "mode": "", "endpoint": ""}
            _SESSIONS[session_id] = state
        return state


def _ensure_endpoint(state, mode, playwright, channel="stable", timeout_seconds=15):
    if state.get("endpoint") and state.get("mode") == mode:
        return state["endpoint"]
    if state.get("mode") and state.get("mode") != mode:
        _close_session_state(state)
    if mode == "existing":
        for profile in _chrome_profile_candidates(channel):
            try:
                endpoint = _read_devtools_endpoint(profile, timeout_seconds=1)
                state.update({"mode": mode, "profile_dir": profile, "endpoint": endpoint, "process": None})
                return endpoint
            except Exception:
                continue
        raise RuntimeError(
            "Chrome 144+ connection unavailable. Start Chrome, enable chrome://inspect/#remote-debugging, and approve the connection request."
        )

    app_root = os.path.join(get_app_data_dir(), "browser-automation")
    if mode == "isolated":
        temp_root = os.path.join(app_root, "tmp")
        os.makedirs(temp_root, exist_ok=True)
        profile_dir = tempfile.mkdtemp(prefix="profile-", dir=temp_root)
    else:
        profile_dir = os.path.join(app_root, "profiles", "default")
        os.makedirs(profile_dir, exist_ok=True)
    executable = _find_chrome_executable(playwright)
    if not executable:
        raise RuntimeError("Chrome or Playwright Chromium executable was not found")
    args = [
        executable,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **subprocess_kwargs_no_window(),
    )
    try:
        endpoint = _read_devtools_endpoint(profile_dir, timeout_seconds=timeout_seconds)
    except Exception:
        process.terminate()
        raise
    state.update({"mode": mode, "profile_dir": profile_dir, "endpoint": endpoint, "process": process})
    return endpoint


def _close_session_state(state):
    process = state.get("process")
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    if state.get("mode") == "isolated" and state.get("profile_dir"):
        shutil.rmtree(state["profile_dir"], ignore_errors=True)
    state.update({"process": None, "profile_dir": "", "mode": "", "endpoint": ""})


def _init_abort_state(context):
    state = {"aborted": False, "bridge": None}
    if not isinstance(context, dict) or not context.get("abort_signal"):
        return state

    class SignalBridge(QObject):
        def trigger(self):
            state["aborted"] = True

    bridge = SignalBridge()
    context["abort_signal"].connect(bridge.trigger, Qt.DirectConnection)
    state["bridge"] = bridge
    return state


def _page_for_browser(browser):
    contexts = browser.contexts
    context = contexts[0] if contexts else None
    if context is None:
        raise RuntimeError("connected browser has no context")
    pages = context.pages
    return pages[-1] if pages else context.new_page()


def _observe(page, limit=80):
    limit = max(1, min(int(limit or 80), 200))
    elements = page.evaluate(
        """limit => {
          const selector = 'a,button,input,textarea,select,[role],[contenteditable="true"]';
          let index = 0;
          return Array.from(document.querySelectorAll(selector)).filter(el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          }).slice(0, limit).map(el => {
            let ref = el.getAttribute('data-cowork-ref');
            if (!ref) { ref = 'e' + (++index); el.setAttribute('data-cowork-ref', ref); }
            return {
              ref,
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || '',
              name: (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || '').trim().slice(0, 160)
            };
          });
        }""",
        limit,
    )
    text = page.locator("body").inner_text(timeout=5000)
    return {"url": page.url, "title": page.title(), "text": text[:12000], "elements": elements}


def _locator(page, step):
    ref = str(step.get("ref") or "").strip()
    if ref:
        return page.locator(f'[data-cowork-ref="{ref}"]')
    selector = str(step.get("selector") or "").strip()
    if selector:
        return page.locator(selector)
    role = str(step.get("role") or "").strip()
    name = step.get("name")
    if role:
        return page.get_by_role(role, name=name)
    label = step.get("label")
    if label:
        return page.get_by_label(str(label))
    text = step.get("text")
    if text is not None:
        return page.get_by_text(str(text), exact=bool(step.get("exact", False)))
    raise ValueError("action requires ref, selector, role, label, or text target")


def _run_step(page, step, workspace_root, timeout_ms):
    action = str(step.get("action") or "").strip().lower()
    if not action:
        raise ValueError("action is required")
    if action == "goto":
        url = _validate_url(step.get("url"), workspace_root)
        page.goto(url, wait_until=str(step.get("wait_until") or "domcontentloaded"), timeout=timeout_ms)
        return {"url": page.url, "title": page.title()}
    if action == "observe":
        return _observe(page, step.get("limit", 80))
    if action in {"click", "fill", "type", "press"}:
        target = _locator(page, step)
        if action == "click":
            target.click(timeout=timeout_ms)
        elif action == "fill":
            target.fill(str(step.get("value", step.get("input", ""))), timeout=timeout_ms)
        elif action == "type":
            target.press_sequentially(str(step.get("value", step.get("input", ""))), delay=int(step.get("delay_ms", 30)))
        else:
            target.press(str(step.get("key") or "Enter"), timeout=timeout_ms)
        return {"url": page.url}
    if action == "wait":
        if any(step.get(key) is not None for key in ("ref", "selector", "role", "label", "text")):
            _locator(page, step).wait_for(state=str(step.get("state") or "visible"), timeout=timeout_ms)
        else:
            page.wait_for_timeout(max(0, int(step.get("ms", 500))))
        return {"waited": True}
    if action == "scroll":
        x = int(step.get("x", 0) or 0)
        y = int(step.get("y", 0) or 0)
        if not y:
            y = max(1, int(step.get("amount", 1) or 1)) * 800
            if str(step.get("direction") or "down").lower() == "up":
                y = -y
        page.evaluate("([x,y]) => window.scrollBy(x,y)", [x, y])
        return {"x": x, "y": y}
    if action == "screenshot":
        path = _safe_output_path(step.get("path") or step.get("output_path"), workspace_root)
        page.screenshot(path=path, full_page=bool(step.get("full_page", True)))
        return {"artifact": {"type": "screenshot", "path": path}}
    if action == "close":
        return {"close": True}
    raise ValueError(f"unsupported action: {action}")


def browser_automate(
    steps,
    session_id="default",
    session_mode="persistent",
    timeout_ms=15000,
    continue_on_error=False,
    channel="stable",
    workspace_dir=None,
    _context=None,
):
    started = _now_ms()
    results = []
    artifacts = []
    status = "completed"
    error = None
    playwright = None
    browser = None
    try:
        session_id = _safe_session_id(session_id)
        mode = str(session_mode or "persistent").strip().lower()
        if mode not in _ALLOWED_MODES:
            raise ValueError("session_mode must be persistent, isolated, or existing")
        if isinstance(steps, str):
            steps = json.loads(steps)
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty array")
        timeout_ms = max(250, min(int(timeout_ms or 15000), 120000))
        workspace_root = _workspace_root(workspace_dir, _context)
        abort_state = _init_abort_state(_context)
        state = _get_session(session_id)
        with state["lock"]:
            sync_playwright = _playwright_sync()
            playwright = sync_playwright().start()
            endpoint = _ensure_endpoint(state, mode, playwright, channel=channel, timeout_seconds=timeout_ms / 1000)
            try:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
            except Exception:
                if mode in {"persistent", "isolated"}:
                    _close_session_state(state)
                else:
                    state["endpoint"] = ""
                endpoint = _ensure_endpoint(state, mode, playwright, channel=channel, timeout_seconds=timeout_ms / 1000)
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
            page = _page_for_browser(browser)
            should_close = False
            for index, step in enumerate(steps):
                if abort_state["aborted"]:
                    status, error = "incomplete", {"code": "aborted", "message": "Command aborted by user"}
                    break
                step_started = _now_ms()
                try:
                    if not isinstance(step, dict):
                        raise ValueError("step must be an object")
                    output = _run_step(page, step, workspace_root, timeout_ms)
                    artifact = output.get("artifact") if isinstance(output, dict) else None
                    if artifact:
                        artifacts.append(artifact)
                    if output.get("close"):
                        should_close = True
                    results.append({"index": index, "action": step.get("action"), "ok": True, "output": output, "elapsed_ms": _now_ms() - step_started})
                except Exception as exc:
                    item_error = {"code": "step_failed", "message": str(exc)}
                    results.append({"index": index, "action": step.get("action") if isinstance(step, dict) else "", "ok": False, "error": item_error, "elapsed_ms": _now_ms() - step_started})
                    status, error = "incomplete", item_error
                    if not continue_on_error:
                        break
            if should_close:
                _close_session_state(state)
    except Exception as exc:
        status = "incomplete"
        error = {"code": "browser_automation_failed", "message": str(exc)}
    finally:
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
    page_info = {}
    if results:
        for item in reversed(results):
            output = item.get("output") or {}
            if output.get("url") or output.get("title"):
                page_info = {key: output[key] for key in ("url", "title") if key in output}
                break
    return _json({
        "status": status,
        "session_id": session_id if "session_id" in locals() else str(session_id or "default"),
        "session_mode": mode if "mode" in locals() else str(session_mode or "persistent"),
        "page": page_info,
        "results": results,
        "artifacts": artifacts,
        "error": error,
        "timings": {"start_ms": started, "end_ms": _now_ms(), "elapsed_ms": _now_ms() - started},
    })


def get_active_tab_info():
    if os.name != "nt":
        return _json({"ok": False, "error": "active tab detection is supported on Windows only"})
    try:
        auto = _uiautomation()
        window = auto.GetForegroundControl().GetTopLevelControl()
        if not window:
            raise RuntimeError("no active window found")
        class_name = window.ClassName or ""
        if "Chrome_WidgetWin_1" in class_name:
            app = "Chrome/Edge"
            control = window.EditControl(Name="Address and search bar")
        elif "MozillaWindowClass" in class_name:
            app = "Firefox"
            control = window.EditControl(searchDepth=3)
        else:
            return _json({"ok": False, "error": "active window is not a supported browser", "title": window.Name, "class_name": class_name})
        url = ""
        if control and control.Exists():
            try:
                url = control.GetValuePattern().Value
            except Exception:
                url = control.Name or ""
        return _json({"ok": True, "app": app, "title": window.Name or "", "url": url})
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


def visit_and_screenshot(url, workspace_dir=None, _context=None):
    return browser_automate(
        [{"action": "goto", "url": url}, {"action": "screenshot"}],
        session_id="visit-and-screenshot",
        session_mode="isolated",
        workspace_dir=workspace_dir,
        _context=_context,
    )


_TARGET_PROPERTIES = {
    "ref": {"type": "string", "description": "Element reference returned by observe."},
    "selector": {"type": "string", "description": "CSS or Playwright selector."},
    "role": {"type": "string", "description": "Accessible role."},
    "name": {"type": "string", "description": "Accessible name used with role."},
    "label": {"type": "string", "description": "Associated form label."},
    "text": {"type": "string", "description": "Visible text target."},
}


TOOL_EXPORTS = [
    {
        "name": "browser_automate",
        "handler": browser_automate,
        "description": "Run a serial browser workflow in a persistent, isolated, or explicitly authorized existing Chrome session.",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered browser actions: goto, observe, click, fill, type, press, wait, scroll, screenshot, or close.",
                    "items": {"type": "object", "properties": {"action": {"type": "string"}, **_TARGET_PROPERTIES}},
                },
                "session_id": {"type": "string", "description": "Reusable logical session name. Defaults to default."},
                "session_mode": {"type": "string", "enum": ["persistent", "isolated", "existing"], "description": "Browser connection mode."},
                "timeout_ms": {"type": "integer", "description": "Per-action timeout from 250 to 120000 ms."},
                "continue_on_error": {"type": "boolean", "description": "Continue remaining steps after an action fails."},
                "channel": {"type": "string", "enum": ["stable", "beta", "dev", "canary"], "description": "Chrome channel for existing-session discovery."},
            },
            "required": ["steps"],
        },
        "destructive": True,
        "search_hint": "browser automate playwright chrome navigate click fill screenshot session",
        "result_format": "json",
    },
    {
        "name": "get_active_tab_info",
        "handler": get_active_tab_info,
        "description": "Read the foreground Chrome, Edge, or Firefox tab title and address on Windows.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "read_only": True,
        "search_hint": "browser active tab url title",
        "result_format": "json",
    },
    {
        "name": "visit_and_screenshot",
        "handler": visit_and_screenshot,
        "description": "Compatibility helper that visits a URL in an isolated browser and saves a screenshot.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        "destructive": True,
        "search_hint": "browser screenshot web page",
        "result_format": "json",
    },
]
