import hashlib
import json
import os
import shutil
import subprocess
import sys

from core.env_utils import get_app_data_dir, get_base_dir


SANDBOX_VERSION = "v1"
_RUNTIME_CACHE = None


def _norm(path):
    return os.path.abspath(path) if path else ""


def _dedupe_paths(paths):
    deduped = []
    seen = set()
    for path in paths:
        if not path:
            continue
        normalized = _norm(path)
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        deduped.append(normalized)
        seen.add(key)
    return deduped


def _simple_exec_env(extra_paths=None):
    env = os.environ.copy()
    if extra_paths:
        env["PATH"] = os.pathsep.join(_dedupe_paths(list(extra_paths) + [env.get("PATH", "")]))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _no_window_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _sandbox_root():
    root = os.path.join(get_app_data_dir(), "runtime_sandbox", SANDBOX_VERSION)
    os.makedirs(root, exist_ok=True)
    return root


def _skill_root(skill_id):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in (skill_id or "default"))
    root = os.path.join(_sandbox_root(), "skills", safe)
    os.makedirs(root, exist_ok=True)
    return root


def _candidate_existing_file(paths):
    seen = set()
    for path in paths:
        if not path:
            continue
        path = _norm(path)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(path):
            return path
    return ""


def _runtime_dir_layouts(base, runtime_name):
    return [
        os.path.join(base, "_internal", f"{runtime_name}_env"),
        os.path.join(base, "_internal", "runtime", runtime_name),
        os.path.join(base, f"{runtime_name}_env"),
        os.path.join(base, "runtime", runtime_name),
    ]


def _cached_runtime_dirs(runtime_name):
    root = os.path.join(_sandbox_root(), "runtimes", runtime_name)
    return [root]


def _candidate_runtime_dirs(name, env_var_names=None, dir_aliases=None):
    base = get_base_dir()
    dirs = []
    env_var_names = env_var_names or [f"COWORK_{name.upper()}_DIR"]
    runtime_names = dir_aliases or [name]
    for env_name in env_var_names:
        env_dir = os.getenv(env_name)
        if env_dir:
            dirs.append(env_dir)
    bases = [base]
    if hasattr(sys, "_MEIPASS"):
        bases.append(sys._MEIPASS)
    for runtime_name in runtime_names:
        for runtime_base in bases:
            dirs.extend(_runtime_dir_layouts(runtime_base, runtime_name))
    for runtime_name in runtime_names:
        dirs.extend(_cached_runtime_dirs(runtime_name))
    return _dedupe_paths(dirs)


def _read_key_value_file(path):
    data = {}
    if not path or not os.path.isfile(path):
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    except Exception:
        return {}
    return data


def _is_runtime_cache_dir(path):
    cache_root = os.path.join(_sandbox_root(), "runtimes")
    try:
        return os.path.commonpath([_norm(cache_root), _norm(path)]) == _norm(cache_root)
    except ValueError:
        return False


def _runtime_cache_is_valid(target, source_key=None, executable_names=None):
    marker = os.path.join(target, ".cowork_runtime_source")
    if not os.path.isdir(target) or not os.path.isfile(marker):
        return False
    if source_key is not None:
        try:
            with open(marker, "r", encoding="utf-8") as f:
                if f.read().strip() != source_key:
                    return False
        except Exception:
            return False
    return any(os.path.isfile(os.path.join(target, rel)) for rel in executable_names or [])


def _copy_runtime_dir(source, runtime_name, executable_names=None):
    if not source or not os.path.isdir(source) or not _is_frozen():
        return source
    if os.path.normcase(_norm(source)).startswith(os.path.normcase(os.path.join(_sandbox_root(), "runtimes"))):
        return source
    target = os.path.join(_sandbox_root(), "runtimes", runtime_name)
    marker = os.path.join(target, ".cowork_runtime_source")
    source_key = _norm(source)
    if os.path.normcase(source_key) == os.path.normcase(_norm(target)):
        return source
    try:
        if _runtime_cache_is_valid(target, source_key, executable_names):
            return target
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(source, target)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(source_key)
        return target
    except Exception:
        return source


def _runtime_file_candidates(runtime_name, executable_names):
    candidates = []
    for runtime_dir in _candidate_runtime_dirs(runtime_name):
        if _is_runtime_cache_dir(runtime_dir) and not _runtime_cache_is_valid(runtime_dir, executable_names=executable_names):
            continue
        for rel in executable_names:
            candidates.append(os.path.join(runtime_dir, rel))
    return candidates


def _runtime_file_candidates_from_dirs(runtime_name, runtime_dirs, executable_names):
    candidates = []
    for runtime_dir in runtime_dirs:
        if _is_runtime_cache_dir(runtime_dir) and not _runtime_cache_is_valid(runtime_dir, executable_names=executable_names):
            continue
        for rel in executable_names:
            candidates.append(os.path.join(runtime_dir, rel))
    return candidates


def _probe_executable(executable, args=None, extra_paths=None, timeout=8):
    if not executable:
        return "", ""
    try:
        out = subprocess.check_output(
            [executable] + list(args or []),
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=_simple_exec_env(extra_paths=extra_paths),
            **_no_window_kwargs(),
        )
        first_line = (out or "").strip().splitlines()[0] if out else ""
        return first_line, ""
    except Exception as exc:
        return "", str(exc)


def _resolve_python():
    env_python = os.getenv("COWORK_PYTHON_EXE")
    candidates = [env_python]
    if not _is_frozen():
        candidates.append(sys.executable if os.path.exists(sys.executable) else "")
    candidates.extend(
        _runtime_file_candidates(
            "python",
            [
                "python.exe",
                os.path.join("Scripts", "python.exe"),
                "python",
                os.path.join("bin", "python"),
                os.path.join("bin", "python3"),
            ],
        )
    )
    return _candidate_existing_file(candidates)


def _validate_python_runtime(python_exe):
    diagnostics = {
        "pyvenv_cfg": {},
        "error": "",
    }
    if not python_exe:
        return "", diagnostics
    runtime_dir = os.path.dirname(_norm(python_exe))
    pyvenv_cfg = os.path.join(runtime_dir, "pyvenv.cfg")
    diagnostics["pyvenv_cfg"] = _read_key_value_file(pyvenv_cfg)
    if diagnostics["pyvenv_cfg"]:
        _, error = _probe_executable(
            python_exe,
            ["--version"],
            extra_paths=[runtime_dir, os.path.join(runtime_dir, "Scripts")],
        )
        if error:
            diagnostics["error"] = (
                "Bundled Python runtime is not executable. "
                "The packaged python.exe appears to depend on an external base interpreter. "
                f"Probe error: {error}"
            )
            return "", diagnostics
    return python_exe, diagnostics


def _resolve_pip(python_exe):
    candidates = [os.getenv("COWORK_PIP_EXE")]
    if python_exe:
        python_dir = os.path.dirname(python_exe)
        candidates.extend(
            [
                os.path.join(python_dir, "pip.exe"),
                os.path.join(python_dir, "pip"),
                os.path.join(python_dir, "Scripts", "pip.exe"),
                os.path.join(python_dir, "bin", "pip"),
            ]
        )
    candidates.extend(_runtime_file_candidates("python", ["pip.exe", os.path.join("Scripts", "pip.exe"), "pip", os.path.join("bin", "pip")]))
    return _candidate_existing_file(candidates) or python_exe


def _resolve_node():
    candidates = [os.getenv("COWORK_NODE_EXE")]
    candidates.extend(_runtime_file_candidates("node", ["node.exe", "node", os.path.join("bin", "node")]))
    if not _is_frozen():
        candidates.append(shutil.which("node"))
    return _candidate_existing_file(candidates)


def _resolve_npm():
    candidates = [os.getenv("COWORK_NPM_EXE")]
    candidates.extend(
        _runtime_file_candidates(
            "node",
            [
                "npm.cmd",
                "npm",
                os.path.join("node_modules", "npm", "bin", "npm-cli.js"),
                os.path.join("bin", "npm"),
            ],
        )
    )
    if not _is_frozen():
        candidates.append(shutil.which("npm"))
    return _candidate_existing_file(candidates)


def _resolve_npx():
    candidates = [os.getenv("COWORK_NPX_EXE")]
    candidates.extend(
        _runtime_file_candidates(
            "node",
            [
                "npx.cmd",
                "npx",
                os.path.join("node_modules", "npm", "bin", "npx-cli.js"),
                os.path.join("bin", "npx"),
            ],
        )
    )
    if not _is_frozen():
        candidates.append(shutil.which("npx"))
    return _candidate_existing_file(candidates)


def _bash_runtime_dirs():
    return _candidate_runtime_dirs(
        "git_bash",
        env_var_names=["COWORK_GIT_BASH_DIR", "COWORK_BASH_DIR"],
        dir_aliases=["git_bash", "bash"],
    )


def _bash_candidate_paths():
    candidates = [os.getenv("COWORK_BASH_EXE")]
    candidates.extend(
        _runtime_file_candidates_from_dirs(
            "git_bash",
            _bash_runtime_dirs(),
            [
                os.path.join("bin", "bash.exe"),
                os.path.join("usr", "bin", "bash.exe"),
                "bash.exe",
                os.path.join("bin", "bash"),
                "bash",
            ],
        )
    )
    if not _is_frozen():
        candidates.append(shutil.which("bash"))
    return _dedupe_paths(candidates)


def _resolve_bash():
    candidates = _bash_candidate_paths()
    return _candidate_existing_file(candidates), {
        "searched_paths": candidates,
        "env_overrides": {
            "COWORK_BASH_EXE": _norm(os.getenv("COWORK_BASH_EXE")),
            "COWORK_GIT_BASH_DIR": _norm(os.getenv("COWORK_GIT_BASH_DIR")),
            "COWORK_BASH_DIR": _norm(os.getenv("COWORK_BASH_DIR")),
        },
    }


def _bash_runtime_root(bash_exe):
    exe_dir = os.path.dirname(_norm(bash_exe))
    parent_dir = os.path.dirname(exe_dir)
    if os.path.basename(exe_dir).lower() == "bin" and os.path.basename(parent_dir).lower() == "usr":
        return os.path.dirname(parent_dir)
    if os.path.basename(exe_dir).lower() == "bin":
        return parent_dir
    return parent_dir


def _resolve_cmd_exe():
    candidates = [
        os.getenv("ComSpec"),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"),
        "cmd.exe",
    ]
    return _candidate_existing_file(candidates)


def ensure_sandbox_runtime():
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is not None:
        return _RUNTIME_CACHE

    python_exe = _resolve_python()
    python_exe, python_diagnostics = _validate_python_runtime(python_exe)
    pip_exe = _resolve_pip(python_exe)
    node_exe = _resolve_node()
    npm_exe = _resolve_npm()
    npx_exe = _resolve_npx()
    bash_exe, bash_diagnostics = _resolve_bash()
    _RUNTIME_CACHE = {
        "root": _sandbox_root(),
        "python": python_exe,
        "python_diagnostics": python_diagnostics,
        "pip": pip_exe,
        "node": node_exe,
        "npm": npm_exe,
        "npx": npx_exe,
        "bash": bash_exe,
        "bash_diagnostics": bash_diagnostics,
    }
    return _RUNTIME_CACHE


def get_runtime_executable(name):
    runtime = ensure_sandbox_runtime()
    key = (name or "").strip().lower()
    return runtime.get(key, "") or ""


def _runtime_path_dirs(runtime):
    dirs = []
    for key in ("python", "node", "npm", "npx", "bash"):
        exe = runtime.get(key)
        if exe:
            dirs.append(os.path.dirname(exe))
    python_exe = runtime.get("python")
    if python_exe:
        dirs.append(os.path.join(os.path.dirname(python_exe), "Scripts"))
    bash_exe = runtime.get("bash")
    if bash_exe:
        bash_root = _bash_runtime_root(bash_exe)
        dirs.extend(
            [
                os.path.join(bash_root, "bin"),
                os.path.join(bash_root, "usr", "bin"),
                os.path.join(bash_root, "mingw64", "bin"),
            ]
        )
    deduped = []
    seen = set()
    for path in dirs:
        if path and os.path.isdir(path):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                deduped.append(path)
                seen.add(key)
    return deduped


def _skill_python_path(skill_id):
    return os.path.join(_skill_root(skill_id), "python", "site-packages")


def _skill_node_root(skill_id):
    return os.path.join(_skill_root(skill_id), "node")


def build_sandbox_env(workspace_dir=None, skill_id=None):
    runtime = ensure_sandbox_runtime()
    env = os.environ.copy()
    root = runtime["root"]
    temp_dir = os.path.join(root, "tmp")
    cache_dir = os.path.join(root, "cache")
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    path_dirs = _runtime_path_dirs(runtime)
    env["PATH"] = os.pathsep.join(path_dirs + [env.get("PATH", "")])
    env["COWORK_SANDBOX_ROOT"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PIP_CACHE_DIR"] = os.path.join(cache_dir, "pip")
    env["npm_config_cache"] = os.path.join(cache_dir, "npm")
    env["npm_config_prefix"] = os.path.join(root, "node-prefix")
    env["TMP"] = temp_dir
    env["TEMP"] = temp_dir

    if skill_id:
        python_path = _skill_python_path(skill_id)
        node_root = _skill_node_root(skill_id)
        node_modules = os.path.join(node_root, "node_modules")
        os.makedirs(python_path, exist_ok=True)
        os.makedirs(node_root, exist_ok=True)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([python_path, existing_pythonpath]) if existing_pythonpath else python_path
        env["NODE_PATH"] = node_modules
        env["npm_config_prefix"] = node_root

    if workspace_dir:
        env["COWORK_WORKSPACE_DIR"] = os.path.abspath(workspace_dir)
    return env


def run_in_sandbox(command, cwd=None, skill_id=None, shell_kind="bash", stdin=None, timeout=None, text=False):
    runtime = ensure_sandbox_runtime()
    cwd = cwd or os.getcwd()
    env = build_sandbox_env(cwd, skill_id=skill_id)

    if shell_kind == "python":
        python_exe = runtime.get("python")
        if not python_exe:
            raise FileNotFoundError("Sandbox Python runtime is missing.")
        args = command if isinstance(command, list) else [python_exe, "-X", "utf8", command]
    elif shell_kind == "exec":
        args = command
    else:
        bash_exe = runtime.get("bash")
        if bash_exe:
            args = [bash_exe, "-lc", command]
        elif os.name == "nt":
            cmd_exe = _resolve_cmd_exe()
            if not cmd_exe:
                raise FileNotFoundError("Sandbox Bash runtime is missing and Windows cmd fallback is unavailable.")
            args = [cmd_exe, "/d", "/s", "/c", str(command)]
        else:
            raise FileNotFoundError("Sandbox Bash runtime is missing.")

    popen_kwargs = {}
    if text:
        popen_kwargs.update({"encoding": "utf-8", "errors": "replace", "bufsize": 1})

    return subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdin=stdin if stdin is not None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        **popen_kwargs,
        **_no_window_kwargs(),
    )


def _shell_quote(value):
    text = str(value or "")
    return "'" + text.replace("'", "'\"'\"'") + "'"


def build_skill_script_command(runtime, script_path, args=None):
    runtime_key = (runtime or "").strip().lower()
    script_path = os.path.abspath(script_path)
    arg_list = [str(item) for item in (args or [])]
    ext = os.path.splitext(script_path.lower())[1]

    if runtime_key == "python":
        python_exe = get_runtime_executable("python")
        if not python_exe:
            raise FileNotFoundError("Sandbox Python runtime is missing.")
        return [python_exe, "-X", "utf8", script_path] + arg_list, "exec"

    if runtime_key == "node":
        node_exe = get_runtime_executable("node")
        if not node_exe:
            raise FileNotFoundError("Sandbox Node runtime is missing.")
        return [node_exe, script_path] + arg_list, "exec"

    if runtime_key == "bash":
        if ext == ".ps1":
            command = "powershell.exe -ExecutionPolicy Bypass -File " + " ".join([_shell_quote(script_path)] + [_shell_quote(arg) for arg in arg_list])
            return command, "bash"
        if ext in {".bat", ".cmd"}:
            quoted = subprocess.list2cmdline([script_path] + arg_list)
            command = f"cmd.exe /c {quoted}"
            return command, "bash"
        bash_exe = get_runtime_executable("bash")
        if not bash_exe:
            raise FileNotFoundError("Sandbox Bash runtime is missing.")
        return [bash_exe, script_path] + arg_list, "exec"

    raise ValueError(f"Unsupported script runtime: {runtime}")


def run_skill_script_in_sandbox(skill_id, script_path, runtime, args=None, cwd=None, input_text=None, timeout_seconds=120):
    command, shell_kind = build_skill_script_command(runtime, script_path, args=args)
    process = run_in_sandbox(
        command,
        cwd=cwd or os.path.dirname(os.path.abspath(script_path)),
        skill_id=skill_id,
        shell_kind=shell_kind,
        text=False,
    )
    input_bytes = None if input_text is None else str(input_text).encode("utf-8")
    output_raw, error_raw = process.communicate(input=input_bytes, timeout=timeout_seconds)
    stdout = output_raw.decode("utf-8", errors="replace") if isinstance(output_raw, (bytes, bytearray)) else (output_raw or "")
    stderr = error_raw.decode("utf-8", errors="replace") if isinstance(error_raw, (bytes, bytearray)) else (error_raw or "")
    return {
        "ok": process.returncode == 0,
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "runtime": (runtime or "").strip().lower(),
        "command": command if isinstance(command, str) else subprocess.list2cmdline(command),
        "cwd": cwd or os.path.dirname(os.path.abspath(script_path)),
    }


def _run_version(executable, args):
    out, _ = _probe_executable(executable, args, extra_paths=_runtime_path_dirs(ensure_sandbox_runtime()))
    return out


def _node_script_command(script_path):
    node_exe = get_runtime_executable("node")
    if script_path and script_path.lower().endswith(".js") and node_exe:
        return [node_exe, script_path]
    return [script_path]


def get_runtime_snapshot():
    runtime = ensure_sandbox_runtime()
    return {
        "root": runtime["root"],
        "mode": "sandbox",
        "python": {
            "path": runtime.get("python", ""),
            "available": bool(runtime.get("python")),
            "version": _run_version(runtime.get("python"), ["--version"]),
            "source": "sandbox" if _is_frozen() else "development",
            "diagnostics": runtime.get("python_diagnostics", {}),
        },
        "node": {
            "path": runtime.get("node", ""),
            "available": bool(runtime.get("node")),
            "version": _run_version(runtime.get("node"), ["--version"]),
            "source": "sandbox" if _is_frozen() else "development",
        },
        "bash": {
            "path": runtime.get("bash", ""),
            "available": bool(runtime.get("bash")),
            "version": _run_version(runtime.get("bash"), ["--version"]),
            "source": "sandbox" if _is_frozen() else "development",
            "diagnostics": runtime.get("bash_diagnostics", {}),
        },
    }


def _dependency_hash(python_dependencies, node_dependencies):
    payload = {
        "python": sorted(python_dependencies or []),
        "node": sorted(node_dependencies or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_dependency_status(skill_id):
    path = os.path.join(_skill_root(skill_id), "dependency_status.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_dependency_status(skill_id, status):
    path = os.path.join(_skill_root(skill_id), "dependency_status.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def install_skill_dependencies(skill_id, python_dependencies=None, node_dependencies=None, force=False):
    python_dependencies = [p for p in (python_dependencies or []) if isinstance(p, str) and p.strip()]
    node_dependencies = [p for p in (node_dependencies or []) if isinstance(p, str) and p.strip()]
    if not python_dependencies and not node_dependencies:
        return {"ok": True, "message": "No dependencies declared.", "installed": False}

    dep_hash = _dependency_hash(python_dependencies, node_dependencies)
    existing = _read_dependency_status(skill_id)
    if not force and existing.get("ok") and existing.get("hash") == dep_hash:
        return {"ok": True, "message": "Dependencies already installed.", "installed": False}

    runtime = ensure_sandbox_runtime()
    env = build_sandbox_env(skill_id=skill_id)
    logs = []
    try:
        if python_dependencies:
            python_exe = runtime.get("python")
            if not python_exe:
                raise RuntimeError("Sandbox Python runtime is missing.")
            target = _skill_python_path(skill_id)
            os.makedirs(target, exist_ok=True)
            cmd = [python_exe, "-m", "pip", "install", "--upgrade", "--target", target] + python_dependencies
            out = subprocess.check_output(cmd, env=env, text=True, encoding="utf-8", errors="replace", stderr=subprocess.STDOUT, **_no_window_kwargs())
            logs.append(out.strip())
        if node_dependencies:
            npm_exe = runtime.get("npm")
            if not npm_exe:
                raise RuntimeError("Sandbox npm runtime is missing.")
            node_root = _skill_node_root(skill_id)
            os.makedirs(node_root, exist_ok=True)
            cmd = _node_script_command(npm_exe) + ["install", "--prefix", node_root] + node_dependencies
            out = subprocess.check_output(cmd, env=env, text=True, encoding="utf-8", errors="replace", stderr=subprocess.STDOUT, **_no_window_kwargs())
            logs.append(out.strip())
        status = {"ok": True, "hash": dep_hash, "message": "\n".join([line for line in logs if line]), "installed": True}
        _write_dependency_status(skill_id, status)
        return status
    except Exception as exc:
        status = {"ok": False, "hash": dep_hash, "message": str(exc), "installed": False}
        _write_dependency_status(skill_id, status)
        return status
