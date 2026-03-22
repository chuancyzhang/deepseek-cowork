import sys
import os
import shutil
import importlib
import json

def get_base_dir():
    """Get the base directory of the application."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # In dev mode, return the project root (parent of core/)
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
    if not getattr(sys, 'frozen', False):
        return sys.executable if os.path.exists(sys.executable) else ""
    candidates = []
    env_python = os.getenv("COWORK_PYTHON_EXE")
    if env_python:
        candidates.append(env_python)
    base_dir = os.path.dirname(sys.executable)
    candidates.extend([
        os.path.join(base_dir, "python_env", "python.exe"),
        os.path.join(base_dir, "python_env", "Scripts", "python.exe"),
        os.path.join(base_dir, "_internal", "python_env", "python.exe"),
        os.path.join(base_dir, "_internal", "python_env", "Scripts", "python.exe"),
        os.path.join(get_base_dir(), "python_env", "python.exe"),
        os.path.join(get_base_dir(), "python_env", "Scripts", "python.exe"),
        os.path.join(get_base_dir(), "_internal", "python_env", "python.exe"),
        os.path.join(get_base_dir(), "_internal", "python_env", "Scripts", "python.exe"),
    ])
    if hasattr(sys, "_MEIPASS"):
        candidates.extend([
            os.path.join(sys._MEIPASS, "python_env", "python.exe"),
            os.path.join(sys._MEIPASS, "python_env", "Scripts", "python.exe"),
            os.path.join(sys._MEIPASS, "_internal", "python_env", "python.exe"),
            os.path.join(sys._MEIPASS, "_internal", "python_env", "Scripts", "python.exe"),
        ])
    seen = set()
    for p in candidates:
        if not p:
            continue
        norm = os.path.normcase(os.path.abspath(p))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(p):
            return p
    return ""

def get_python_runtime_snapshot():
    python_exe = get_python_executable()
    snapshot = {
        "python_exe": python_exe,
        "resolved_from": "bundled" if getattr(sys, "frozen", False) else "system",
        "version": "",
        "available_packages": [],
        "missing_packages": [name for name, _ in _RUNTIME_IMPORT_CHECKS]
    }
    if not python_exe or not os.path.isfile(python_exe):
        return snapshot
    try:
        import subprocess
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        checks = json.dumps(_RUNTIME_IMPORT_CHECKS, ensure_ascii=False)
        code = (
            "import json,sys,importlib.util;"
            f"checks={checks};"
            "available=[];missing=[];"
            "for pkg,mod in checks:"
            " (available if importlib.util.find_spec(mod) else missing).append(pkg);"
            "print(json.dumps({'version':sys.version.split()[0],'available':available,'missing':missing},ensure_ascii=False))"
        )
        output = subprocess.check_output(
            [python_exe, "-c", code],
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo
        ).strip()
        data = json.loads(output)
        snapshot["version"] = data.get("version", "") or ""
        snapshot["available_packages"] = data.get("available", []) or []
        snapshot["missing_packages"] = data.get("missing", []) or []
    except Exception:
        pass
    return snapshot

_INSTALL_SUCCESS = set()
_INSTALL_FAILED = {}
_RUNTIME_IMPORT_CHECKS = [
    ("openpyxl", "openpyxl"),
    ("python-docx", "docx"),
    ("python-pptx", "pptx"),
    ("pypdf", "pypdf"),
    ("beautifulsoup4", "bs4"),
    ("requests", "requests"),
    ("markdown", "markdown"),
    ("qtawesome", "qtawesome"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("lark-oapi", "lark_oapi"),
]

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
        output = subprocess.check_output(
            [python_exe, "-c", "import json,site;print(json.dumps({'site': getattr(site,'getsitepackages',lambda:[])(), 'user': site.getusersitepackages()}))"],
            text=True
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

def ensure_package_installed(package_name, import_name=None):
    """
    Ensure a python package is installed using the environment's pip.
    
    Args:
        package_name (str): The name of the package to install (e.g. 'yt-dlp').
        import_name (str, optional): The module name to import (e.g. 'yt_dlp'). 
                                     Defaults to package_name.
    """
    if not import_name:
        import_name = package_name
        
    try:
        importlib.import_module(import_name)
        _INSTALL_SUCCESS.add(import_name)
    except ImportError:
        if import_name in _INSTALL_FAILED:
            raise RuntimeError(_INSTALL_FAILED[import_name])

        print(f"[System] Installing missing dependency: {package_name}...")
        
        # Determine the correct python executable
        python_exe = get_python_executable()
        if not python_exe:
            msg = f"Failed to install {package_name}: bundled Python runtime is missing. This package may be corrupted."
            _INSTALL_FAILED[import_name] = msg
            raise RuntimeError(msg)

        try:
            import subprocess
            
            # On Windows, we want to suppress the new window for the subprocess
            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
            subprocess.check_call(
                [python_exe, "-m", "pip", "install", package_name],
                startupinfo=startupinfo
            )
            print(f"[System] Successfully installed {package_name}.")
            
            # 1. Invalidate caches to let importlib see the new package
            importlib.invalidate_caches()
            
            # 2. Force site-packages reload and path update
            _refresh_sys_path()
            if python_exe and os.path.basename(python_exe).lower().startswith("python"):
                if os.path.abspath(python_exe) != os.path.abspath(sys.executable):
                    _attach_external_site_packages(python_exe)
            
            # 3. Verification & Retry
            try:
                importlib.import_module(import_name)
                print(f"[System] Verified {import_name} is importable.")
                _INSTALL_SUCCESS.add(import_name)
            except ImportError:
                # Retry once more with explicit cache clearing
                importlib.invalidate_caches()
                try:
                    importlib.import_module(import_name)
                    print(f"[System] Verified {import_name} is importable (after retry).")
                    _INSTALL_SUCCESS.add(import_name)
                except ImportError as e:
                     # If we are in Frozen Mode, this is expected if sys.path is not shared
                     if getattr(sys, 'frozen', False):
                          print(f"[System] Warning: {package_name} installed to external env. Restart required for in-process use.")
                          _INSTALL_FAILED[import_name] = f"Installed {package_name} but failed to load {import_name} in frozen mode. Restart required."
                     else:
                          # In Dev Mode, this is a real error
                          _INSTALL_FAILED[import_name] = f"Installed {package_name} but failed to load {import_name} dynamically. Error: {e}"
                          raise RuntimeError(_INSTALL_FAILED[import_name])

        except subprocess.CalledProcessError as e:
            err = getattr(e, "output", None)
            msg = f"Failed to install {package_name}: {str(e)}"
            if err:
                msg = f"{msg}\n{err}"
            _INSTALL_FAILED[import_name] = msg
            raise RuntimeError(msg)
