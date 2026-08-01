import sys
import os
import shutil
import importlib
import json
from core.process_utils import subprocess_kwargs_no_window

def get_base_dir():
    """Get the base directory of the application."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # In dev mode, return the project root (parent of core/)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_dir():
    """Return the read-only root that contains bundled application resources."""
    if getattr(sys, "frozen", False):
        resource_dir = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if not resource_dir:
            raise RuntimeError("冻结应用缺少 PyInstaller 资源目录。")
        return os.path.abspath(resource_dir)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_app_data_dir():
    """
    Get the directory for storing user data (config, history, skills).
    Logic:
    1. Portable Mode: Check if 'user_data' folder exists next to executable/script.
    2. Installed Mode: Use standard AppData location.
    """
    base_dir = get_base_dir()
    
    # 1. Portable Mode Check
    portable_data_dir = os.path.join(base_dir, 'user_data')
    if os.path.exists(portable_data_dir):
        return portable_data_dir
        
    # 2. Standard AppData
    # e.g., C:\Users\<User>\AppData\Roaming\DeepSeekCowork
    app_name = "DeepSeekCowork"
    if sys.platform == 'win32':
        app_data = os.getenv('APPDATA')
        # If APPDATA is not set (rare), fallback to user home
        if not app_data:
            app_data = os.path.expanduser("~")
        data_dir = os.path.join(app_data, app_name)
    elif sys.platform == 'darwin':
        data_dir = os.path.expanduser(f"~/Library/Application Support/{app_name}")
    else: # Linux/Unix
        data_dir = os.path.expanduser(f"~/.local/share/{app_name}")
        
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_python_executable():
    from core.sandbox_runtime import get_runtime_executable
    return get_runtime_executable("python")

def get_runtime_snapshot():
    from core.sandbox_runtime import get_runtime_snapshot as _get_runtime_snapshot
    return _get_runtime_snapshot()

_INSTALL_SUCCESS = set()
_INSTALL_FAILED = {}


def _inject_skill_python_path(skill_id):
    from core.sandbox_runtime import build_sandbox_env
    python_path = build_sandbox_env(skill_id=skill_id).get("PYTHONPATH", "")
    for path in reversed([item for item in python_path.split(os.pathsep) if item]):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _summarize_import_probe(probe):
    if not isinstance(probe, dict):
        return ""
    parts = []
    error = (probe.get("error") or "").strip()
    stderr = (probe.get("stderr") or "").strip()
    stdout = (probe.get("stdout") or "").strip()
    if error:
        parts.append(error)
    if stderr and stderr not in parts:
        parts.append(stderr)
    if stdout and stdout not in parts:
        parts.append(stdout)
    return "\n".join([item for item in parts if item])


def _sandbox_import_probe(python_exe, import_name, skill_id=None):
    if not python_exe:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": "Sandbox Python runtime is missing."}
    try:
        import subprocess
        from core.sandbox_runtime import build_sandbox_env
        code = (
            "import importlib,json,traceback\n"
            f"name={json.dumps(import_name)}\n"
            "payload={'ok': True, 'error': ''}\n"
            "try:\n"
            "    importlib.import_module(name)\n"
            "except Exception:\n"
            "    payload['ok'] = False\n"
            "    payload['error'] = traceback.format_exc()\n"
            "print(json.dumps(payload, ensure_ascii=False))\n"
            "raise SystemExit(0 if payload['ok'] else 1)\n"
        )
        completed = subprocess.run(
            [python_exe, "-X", "utf8", "-c", code],
            env=build_sandbox_env(skill_id=skill_id),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        error = ""
        ok = completed.returncode == 0
        if stdout:
            last_line = stdout.splitlines()[-1]
            try:
                payload = json.loads(last_line)
                ok = bool(payload.get("ok"))
                error = (payload.get("error") or "").strip()
                stdout = "\n".join(stdout.splitlines()[:-1]).strip()
            except Exception:
                if not ok:
                    error = stdout
        elif not ok and stderr:
            error = stderr
        return {
            "ok": ok,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "", "error": str(exc)}


def _sandbox_can_import(python_exe, import_name, skill_id=None):
    return _sandbox_import_probe(python_exe, import_name, skill_id=skill_id).get("ok", False)

def _refresh_sys_path():
    import site
    try:
        importlib.reload(site)
    except Exception:
        pass

    if site.ENABLE_USER_SITE:
        user_site = site.getusersitepackages()
        if user_site and os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.append(user_site)
            print(f"[System] Added User Site to sys.path: {user_site}")

    if hasattr(site, 'getsitepackages'):
        try:
            global_sites = site.getsitepackages()
            for p in global_sites:
                if p and os.path.isdir(p) and p not in sys.path:
                    sys.path.append(p)
                    print(f"[System] Added Global Site to sys.path: {p}")
        except Exception:
            pass

def _get_external_site_packages(python_exe):
    try:
        import subprocess
        from core.sandbox_runtime import build_sandbox_env
        output = subprocess.check_output(
            [python_exe, "-c", "import json,site;print(json.dumps({'site': getattr(site,'getsitepackages',lambda:[])(), 'user': site.getusersitepackages()}))"],
            text=True,
            env=build_sandbox_env(),
            **subprocess_kwargs_no_window(),
        )
        data = json.loads(output.strip())
        sites = data.get("site", []) or []
        user_site = data.get("user")
        if user_site:
            sites.append(user_site)
        return [p for p in sites if isinstance(p, str)]
    except Exception:
        return []

def _attach_external_site_packages(python_exe):
    for p in _get_external_site_packages(python_exe):
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
            print(f"[System] Added External Site to sys.path: {p}")

def ensure_package_installed(package_name, import_name=None, skill_id=None):
    """
    Ensure a python package is installed using the environment's pip.
    
    Args:
        package_name (str): The name of the package to install (e.g. 'yt-dlp').
        import_name (str, optional): The module name to import (e.g. 'yt_dlp'). 
                                     Defaults to package_name.
        skill_id (str, optional): Install into a skill-scoped sandbox package path.
    """
    if not import_name:
        import_name = package_name
    cache_key = f"{skill_id or 'global'}:{import_name}"
    python_exe = get_python_executable()
    if skill_id:
        _inject_skill_python_path(skill_id)
    sandbox_ready = _sandbox_can_import(python_exe, import_name, skill_id=skill_id)
    if sandbox_ready:
        _INSTALL_SUCCESS.add(cache_key)
        _INSTALL_FAILED.pop(cache_key, None)
        return

    if cache_key in _INSTALL_FAILED:
        raise RuntimeError(_INSTALL_FAILED[cache_key])

    print(f"[System] Installing missing dependency: {package_name}...")

    if not python_exe:
        msg = f"Failed to install {package_name}: bundled Python runtime is missing. This package may be corrupted."
        _INSTALL_FAILED[cache_key] = msg
        raise RuntimeError(msg)

    try:
        import subprocess

        from core.sandbox_runtime import build_sandbox_env, install_skill_dependencies
        if skill_id:
            status = install_skill_dependencies(skill_id, python_dependencies=[package_name])
            if not status.get("ok"):
                msg = status.get("message") or f"Failed to install {package_name}."
                _INSTALL_FAILED[cache_key] = msg
                raise RuntimeError(msg)
            _inject_skill_python_path(skill_id)
            if not _sandbox_can_import(python_exe, import_name, skill_id=skill_id):
                status = install_skill_dependencies(skill_id, python_dependencies=[package_name], force=True)
                if not status.get("ok"):
                    msg = status.get("message") or f"Failed to install {package_name}."
                    _INSTALL_FAILED[cache_key] = msg
                    raise RuntimeError(msg)
                _inject_skill_python_path(skill_id)
        else:
            from core.runtime_components import selected_python_index_url
            subprocess.check_call(
                [python_exe, "-m", "pip", "install", "--index-url", selected_python_index_url(), package_name],
                env=build_sandbox_env(),
                **subprocess_kwargs_no_window(),
            )
        print(f"[System] Successfully installed {package_name}.")

        importlib.invalidate_caches()
        _refresh_sys_path()
        if python_exe and os.path.basename(python_exe).lower().startswith("python"):
            if os.path.abspath(python_exe) != os.path.abspath(sys.executable):
                _attach_external_site_packages(python_exe)

        if _sandbox_can_import(python_exe, import_name, skill_id=skill_id):
            print(f"[System] Verified {import_name} is importable in sandbox runtime.")
            _INSTALL_SUCCESS.add(cache_key)
            _INSTALL_FAILED.pop(cache_key, None)
            try:
                importlib.import_module(import_name)
            except ImportError:
                pass
            return

        probe = _sandbox_import_probe(python_exe, import_name, skill_id=skill_id)
        detail = _summarize_import_probe(probe)
        if getattr(sys, 'frozen', False) and not skill_id:
            print(f"[System] Warning: {package_name} installed to external env. Restart required for in-process use.")
            _INSTALL_FAILED[cache_key] = (
                f"Installed {package_name} but the sandbox runtime still cannot import {import_name}. Restart required."
            )
        else:
            message = f"Installed {package_name} but the sandbox runtime still cannot import {import_name}."
            if detail:
                message = f"{message}\n{detail}"
            _INSTALL_FAILED[cache_key] = message
            raise RuntimeError(message)
        if detail:
            _INSTALL_FAILED[cache_key] = f"{_INSTALL_FAILED[cache_key]}\n{detail}"

    except subprocess.CalledProcessError as e:
        err = getattr(e, "output", None)
        msg = f"Failed to install {package_name}: {str(e)}"
        if err:
            msg = f"{msg}\n{err}"
        _INSTALL_FAILED[cache_key] = msg
        raise RuntimeError(msg)
