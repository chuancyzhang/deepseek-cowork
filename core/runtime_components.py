import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
import zipfile
from urllib.parse import urlparse

import requests

from core.env_utils import get_app_data_dir
from core.process_utils import subprocess_kwargs_no_window


PYTHON_SOURCES = {
    "pypi": {"name": "PyPI", "url": "https://pypi.org/simple"},
    "tsinghua": {"name": "清华镜像", "url": "https://pypi.tuna.tsinghua.edu.cn/simple"},
    "aliyun": {"name": "阿里云镜像", "url": "https://mirrors.aliyun.com/pypi/simple/"},
}
NODE_SOURCES = {
    "nodejs": {"name": "Node.js 官方", "url": "https://nodejs.org/dist/"},
    "npmmirror": {"name": "npmmirror", "url": "https://npmmirror.com/mirrors/node/"},
}

NODE_VERSION = "v24.14.1"
NODE_ARCHIVE = "node-v24.14.1-win-x64.zip"
NODE_SHA256 = "6E50CE5498C0CEBC20FD39AB3FF5DF836ED2F8A31AA093CECAD8497CFF126D70"
TOOLKIT_MARKER_SCHEMA = 2

TOOLKITS = {
    "documents": {
        "name": "文档工具包",
        "description": "读取与生成 XLSX、DOCX、PPTX 和 PDF",
        "packages": ["openpyxl", "python-docx", "python-pptx", "Pillow", "pypdf", "reportlab"],
        "imports": ["openpyxl", "docx", "pptx", "PIL.Image", "pypdf", "reportlab"],
        "skills": ["document-reader"],
    },
    "data-analysis": {
        "name": "数据分析工具包",
        "description": "数据处理、科学计算、可视化与机器学习",
        "packages": ["numpy", "pandas", "scipy", "matplotlib", "seaborn", "scikit-learn"],
        "imports": ["numpy", "pandas", "scipy", "matplotlib", "seaborn", "sklearn"],
        "skills": [],
    },
    "finance": {
        "name": "金融分析工具包",
        "description": "金融数据查询与研究辅助",
        "packages": ["pandas", "akshare"],
        "imports": ["pandas", "akshare"],
        "skills": ["financial-data-akshare"],
    },
    "web-research": {
        "name": "网页研究工具包",
        "description": "网页搜索、解析与结构化采集",
        "packages": ["tavily-python==0.7.26"],
        "imports": ["tavily"],
        "skills": ["web-search"],
    },
}


def default_download_sources():
    return {
        "python": {"selected": "pypi", "custom": []},
        "node": {"selected": "nodejs", "custom": []},
    }


def load_saved_download_sources():
    path = os.path.join(get_app_data_dir(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return normalize_download_sources(payload.get("download_sources"))
    except (OSError, ValueError, TypeError):
        return default_download_sources()


def selected_python_index_url():
    return selected_source("python", load_saved_download_sources())["url"]


def normalize_download_sources(value):
    source = value if isinstance(value, dict) else {}
    result = default_download_sources()
    for kind, presets in (("python", PYTHON_SOURCES), ("node", NODE_SOURCES)):
        cfg = source.get(kind) if isinstance(source.get(kind), dict) else {}
        custom = []
        seen = set()
        for item in cfg.get("custom") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not name or not valid_https_source(url):
                continue
            if not item_id:
                item_id = "custom-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            if item_id in seen or item_id in presets:
                continue
            seen.add(item_id)
            custom.append({"id": item_id, "name": name, "url": url.rstrip("/") + "/"})
        result[kind]["custom"] = custom
        available = set(presets) | {item["id"] for item in custom}
        selected = str(cfg.get("selected") or result[kind]["selected"])
        result[kind]["selected"] = selected if selected in available else result[kind]["selected"]
    return result


def valid_https_source(url):
    try:
        parsed = urlparse(str(url or "").strip())
        return parsed.scheme.lower() == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except Exception:
        return False


def source_options(kind, settings):
    normalized = normalize_download_sources(settings)
    presets = PYTHON_SOURCES if kind == "python" else NODE_SOURCES
    options = [{"id": key, **value, "custom": False} for key, value in presets.items()]
    options.extend({**item, "custom": True} for item in normalized[kind]["custom"])
    return options


def selected_source(kind, settings):
    normalized = normalize_download_sources(settings)
    selected = normalized[kind]["selected"]
    for item in source_options(kind, normalized):
        if item["id"] == selected:
            return item
    raise RuntimeError(f"未找到已选择的{kind}下载源：{selected}")


def test_source(kind, source, timeout=12):
    url = str((source or {}).get("url") or "").strip()
    if not valid_https_source(url):
        raise ValueError("下载源必须是有效的 HTTPS 地址，且不能在 URL 中包含凭据。")
    target = url
    if kind == "node":
        target = f"{url.rstrip('/')}/{NODE_VERSION}/{NODE_ARCHIVE}"
    response = requests.get(target, stream=True, timeout=timeout, headers={"User-Agent": "deepseek-cowork-components"})
    try:
        response.raise_for_status()
    finally:
        response.close()
    return True


def toolkits_root():
    path = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "toolkits")
    os.makedirs(path, exist_ok=True)
    return path


def toolkit_path(toolkit_id):
    return os.path.join(toolkits_root(), toolkit_id, "site-packages")


def _toolkit_definition_hash(toolkit_id):
    spec = TOOLKITS[toolkit_id]
    payload = {
        "packages": spec["packages"],
        "imports": spec["imports"],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _read_toolkit_marker(toolkit_id):
    marker_path = os.path.join(os.path.dirname(toolkit_path(toolkit_id)), "toolkit.json")
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
        return marker if isinstance(marker, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _toolkit_marker_health(toolkit_id, marker):
    from core.sandbox_runtime import get_runtime_executable

    if not marker:
        return False, "缺少工具包健康标记。"
    if marker.get("schema") != TOOLKIT_MARKER_SCHEMA:
        return False, "工具包由旧版本安装，需要更新后重新验收。"
    if marker.get("definition_hash") != _toolkit_definition_hash(toolkit_id):
        return False, "工具包依赖定义已变化，需要更新。"
    current_python = os.path.normcase(os.path.abspath(get_runtime_executable("python") or ""))
    marker_python = os.path.normcase(os.path.abspath(marker.get("python_executable") or ""))
    if not current_python or marker_python != current_python:
        return False, "沙箱 Python 运行时已变化，需要重新安装工具包。"
    if not marker.get("verified"):
        return False, marker.get("verification_error") or "工具包尚未通过完整性验证。"
    return True, ""


def installed_toolkit_paths():
    paths = []
    for toolkit_id in TOOLKITS:
        path = toolkit_path(toolkit_id)
        marker = _read_toolkit_marker(toolkit_id)
        healthy, _error = _toolkit_marker_health(toolkit_id, marker)
        if os.path.isdir(path) and healthy:
            paths.append(path)
    return paths


def _directory_size(path):
    total = 0
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    return total


def toolkit_status(toolkit_id, include_size=False):
    spec = TOOLKITS[toolkit_id]
    root = os.path.dirname(toolkit_path(toolkit_id))
    marker = _read_toolkit_marker(toolkit_id)
    installed_packages = {
        str(package).strip().lower()
        for package in (marker.get("packages") or [])
        if str(package).strip()
    }
    required_packages = {package.lower() for package in spec["packages"]}
    missing_packages = sorted(required_packages - installed_packages)
    installed = bool(marker and os.path.isdir(toolkit_path(toolkit_id)))
    healthy, health_error = _toolkit_marker_health(toolkit_id, marker) if installed else (False, "")
    needs_update = installed and (
        marker.get("schema") != TOOLKIT_MARKER_SCHEMA
        or marker.get("definition_hash") != _toolkit_definition_hash(toolkit_id)
        or bool(missing_packages)
    )
    needs_repair = installed and not needs_update and not healthy
    return {
        "id": toolkit_id,
        **spec,
        "installed": installed,
        "healthy": healthy,
        "needs_update": needs_update,
        "needs_repair": needs_repair,
        "health_error": health_error,
        "missing_packages": missing_packages if installed else [],
        "source": marker.get("source") or "",
        "size": _directory_size(root) if include_size else 0,
    }


def _verify_toolkit_candidate(python_exe, toolkit_id, candidate_path):
    from core.sandbox_runtime import build_sandbox_env

    imports = TOOLKITS[toolkit_id]["imports"]
    code = (
        "import importlib,json,traceback\n"
        f"checks={json.dumps(imports, ensure_ascii=False)}\n"
        "result={'ok': True, 'checked': [], 'error': ''}\n"
        "try:\n"
        "    for name in checks:\n"
        "        importlib.import_module(name)\n"
        "        result['checked'].append(name)\n"
        "except Exception:\n"
        "    result['ok'] = False\n"
        "    result['error'] = traceback.format_exc()\n"
        "print(json.dumps(result, ensure_ascii=False))\n"
        "raise SystemExit(0 if result['ok'] else 1)\n"
    )
    env = build_sandbox_env()
    bootstrap_entries = [
        item
        for item in (env.get("PYTHONPATH") or "").split(os.pathsep)
        if item and os.path.basename(item) == "python_bootstrap"
    ]
    env["PYTHONPATH"] = os.pathsep.join(bootstrap_entries + [candidate_path])
    completed = subprocess.run(
        [python_exe, "-X", "utf8", "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_kwargs_no_window(),
    )
    output = (completed.stdout or "").strip()
    payload = {}
    if output:
        try:
            payload = json.loads(output.splitlines()[-1])
        except (ValueError, TypeError):
            payload = {}
    if completed.returncode != 0 or not payload.get("ok"):
        detail = payload.get("error") or completed.stderr or output or "未知导入错误"
        raise RuntimeError(f"{TOOLKITS[toolkit_id]['name']}完整性验证失败：\n{detail.strip()}")
    return payload.get("checked") or []


def _repair_python_runner_import_conflicts(python_exe, toolkit_id, candidate_path):
    from core.sandbox_runtime import build_sandbox_env

    skill_path = os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "skills",
        "python-runner",
        "python",
        "site-packages",
    )
    if not os.path.isdir(skill_path):
        return []
    base_env = build_sandbox_env(skill_id="python-runner")
    bootstrap_entries = [
        item
        for item in (base_env.get("PYTHONPATH") or "").split(os.pathsep)
        if item and os.path.basename(item) == "python_bootstrap"
    ]
    repaired = []
    for import_name in TOOLKITS[toolkit_id]["imports"]:
        env = dict(base_env)
        env["PYTHONPATH"] = os.pathsep.join(bootstrap_entries + [skill_path, candidate_path])
        probe = subprocess.run(
            [python_exe, "-X", "utf8", "-c", f"import importlib; importlib.import_module({import_name!r})"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if probe.returncode == 0:
            continue
        top_level = import_name.split(".", 1)[0]
        candidates = [
            os.path.join(skill_path, top_level),
            os.path.join(skill_path, top_level + ".py"),
        ]
        conflict_paths = [path for path in candidates if os.path.exists(path)]
        if not conflict_paths:
            continue
        for path in conflict_paths:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        retry = subprocess.run(
            [python_exe, "-X", "utf8", "-c", f"import importlib; importlib.import_module({import_name!r})"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if retry.returncode != 0:
            raise RuntimeError(
                f"清理 python-runner 中冲突的 {top_level} 后仍无法导入 {import_name}：\n"
                f"{(retry.stderr or retry.stdout or '未知导入错误').strip()}"
            )
        repaired.append(top_level)
    return sorted(set(repaired))


def _replace_toolkit_root(staged_root, target_root):
    backup_root = target_root + ".previous"
    if os.path.isdir(backup_root):
        shutil.rmtree(backup_root)
    had_existing = os.path.isdir(target_root)
    if had_existing:
        os.replace(target_root, backup_root)
    try:
        os.replace(staged_root, target_root)
    except Exception:
        if had_existing and os.path.isdir(backup_root) and not os.path.exists(target_root):
            os.replace(backup_root, target_root)
        raise
    if os.path.isdir(backup_root):
        shutil.rmtree(backup_root)


def install_toolkit(toolkit_id, python_source, progress_callback=None, force=False):
    if toolkit_id not in TOOLKITS:
        raise KeyError(f"未知工具包：{toolkit_id}")
    from core.sandbox_runtime import build_sandbox_env, get_runtime_executable

    python_exe = get_runtime_executable("python")
    if not python_exe:
        raise RuntimeError("沙箱 Python 不可用。")
    source_url = str((python_source or {}).get("url") or "")
    if not valid_https_source(source_url):
        raise ValueError("Python 下载源必须是有效的 HTTPS 地址。")
    spec = TOOLKITS[toolkit_id]
    target_root = os.path.dirname(toolkit_path(toolkit_id))
    staged_root = tempfile.mkdtemp(prefix=f".{toolkit_id}-", dir=toolkits_root())
    staged_target = os.path.join(staged_root, "site-packages")
    os.makedirs(staged_target)
    try:
        if progress_callback:
            progress_callback(f"正在从 {python_source.get('name') or source_url} 下载并安装 {spec['name']}…", 20)
        command = [python_exe, "-m", "pip", "install", "--index-url", source_url, "--upgrade", "--target", staged_target]
        if force:
            command.append("--force-reinstall")
        command.extend(spec["packages"])
        completed = subprocess.run(
            command,
            env=build_sandbox_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_kwargs_no_window(),
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "pip 安装失败").strip())
        if progress_callback:
            progress_callback(f"正在验证 {spec['name']} 的全部依赖…", 75)
        checked_imports = _verify_toolkit_candidate(python_exe, toolkit_id, staged_target)
        repaired_conflicts = _repair_python_runner_import_conflicts(
            python_exe,
            toolkit_id,
            staged_target,
        )
        marker = {
            "schema": TOOLKIT_MARKER_SCHEMA,
            "id": toolkit_id,
            "packages": spec["packages"],
            "imports": checked_imports,
            "definition_hash": _toolkit_definition_hash(toolkit_id),
            "python_executable": os.path.abspath(python_exe),
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "repaired_python_runner_conflicts": repaired_conflicts,
            "source": python_source.get("name") or source_url,
            "source_url": source_url,
        }
        with open(os.path.join(staged_root, "toolkit.json"), "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback(f"正在启用 {spec['name']}…", 92)
        _replace_toolkit_root(staged_root, target_root)
        staged_root = ""
        if progress_callback:
            progress_callback(f"{spec['name']}安装并验证完成。", 100)
    finally:
        if staged_root and os.path.isdir(staged_root):
            shutil.rmtree(staged_root, ignore_errors=True)
    return toolkit_status(toolkit_id, include_size=True)


def uninstall_toolkit(toolkit_id):
    if toolkit_id not in TOOLKITS:
        raise KeyError(f"未知工具包：{toolkit_id}")
    root = os.path.dirname(toolkit_path(toolkit_id))
    if os.path.isdir(root):
        shutil.rmtree(root)
    return toolkit_status(toolkit_id, include_size=True)


def node_runtime_status():
    from core.sandbox_runtime import get_runtime_executable
    path = get_runtime_executable("node")
    return {"installed": bool(path), "path": path, "version": NODE_VERSION if path else ""}


def _safe_extract(archive, target):
    target_abs = os.path.abspath(target)
    for member in archive.infolist():
        candidate = os.path.abspath(os.path.join(target_abs, member.filename))
        if os.path.commonpath([target_abs, candidate]) != target_abs:
            raise RuntimeError("Node.js 压缩包包含不安全路径。")
    archive.extractall(target)


def install_node_runtime(node_source, progress_callback=None):
    from core.sandbox_runtime import reset_runtime_cache

    source_url = str((node_source or {}).get("url") or "")
    if not valid_https_source(source_url):
        raise ValueError("Node.js 下载源必须是有效的 HTTPS 地址。")
    url = f"{source_url.rstrip('/')}/{NODE_VERSION}/{NODE_ARCHIVE}"
    runtime_root = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "runtimes")
    target = os.path.join(runtime_root, "node")
    os.makedirs(runtime_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-node-", dir=runtime_root) as temp_dir:
        archive_path = os.path.join(temp_dir, NODE_ARCHIVE)
        digest = hashlib.sha256()
        if progress_callback:
            progress_callback(f"正在从 {node_source.get('name') or source_url} 下载 Node.js…", 1)
        with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "deepseek-cowork-components"}) as response:
            response.raise_for_status()
            expected = int(response.headers.get("content-length") or 0)
            downloaded = 0
            with open(archive_path, "wb") as handle:
                for chunk in response.iter_content(512 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback and expected:
                        progress_callback("正在下载 Node.js…", min(85, int(downloaded * 85 / expected)))
        actual = digest.hexdigest().upper()
        if actual != NODE_SHA256:
            raise RuntimeError(f"Node.js SHA-256 校验失败：期望 {NODE_SHA256}，实际 {actual}")
        extract_dir = os.path.join(temp_dir, "extract")
        os.makedirs(extract_dir)
        with zipfile.ZipFile(archive_path, "r") as archive:
            _safe_extract(archive, extract_dir)
        dirs = [item.path for item in os.scandir(extract_dir) if item.is_dir()]
        if len(dirs) != 1 or not os.path.isfile(os.path.join(dirs[0], "node.exe")):
            raise RuntimeError("Node.js 压缩包结构无效。")
        staged = os.path.join(runtime_root, "node.next")
        if os.path.isdir(staged):
            shutil.rmtree(staged)
        shutil.move(dirs[0], staged)
        with open(os.path.join(staged, ".cowork_runtime_source"), "w", encoding="utf-8") as handle:
            handle.write(f"{NODE_VERSION}|{source_url}")
        if os.path.isdir(target):
            shutil.rmtree(target)
        os.replace(staged, target)
    reset_runtime_cache()
    if progress_callback:
        progress_callback("Node.js 安装完成。", 100)
    return node_runtime_status()


def uninstall_node_runtime():
    from core.sandbox_runtime import reset_runtime_cache
    target = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "runtimes", "node")
    if os.path.isdir(target):
        shutil.rmtree(target)
    reset_runtime_cache()
    return node_runtime_status()
