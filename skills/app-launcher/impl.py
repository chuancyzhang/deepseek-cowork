import os
import json
import subprocess
import difflib
import shlex
from core.env_utils import get_app_data_dir

def _index_path():
    return os.path.join(get_app_data_dir(), "app_index.json")

def _read_index():
    path = _index_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

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
