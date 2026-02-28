import os
import json
import subprocess
import difflib
import time
import sys
from core.env_utils import get_app_data_dir

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
