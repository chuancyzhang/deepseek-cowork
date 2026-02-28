import os
import time
import subprocess
import webbrowser
import json
from core.env_utils import ensure_package_installed, get_app_data_dir, get_python_executable

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
    ensure_package_installed("playwright")
    python_exe = get_python_executable()
    try:
        subprocess.check_call([python_exe, "-m", "playwright", "install", "chromium"])
    except Exception:
        pass
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

def _ensure_playwright():
    ensure_package_installed("playwright")
    python_exe = get_python_executable()
    try:
        subprocess.check_call([python_exe, "-m", "playwright", "install", "chromium"])
    except Exception:
        pass

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
