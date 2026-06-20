import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
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

TOOLKITS = {
    "documents": {
        "name": "文档工具包",
        "description": "读取与生成 XLSX、DOCX、PPTX 和 PDF",
        "packages": ["openpyxl", "python-docx", "python-pptx", "pypdf"],
        "imports": ["openpyxl", "docx", "pptx", "pypdf"],
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
        "description": "金融数据、策略回测与绩效分析",
        "packages": ["pandas", "akshare", "yfinance", "tushare", "backtrader", "quantstats"],
        "imports": ["pandas", "akshare", "yfinance", "tushare", "backtrader", "quantstats"],
        "skills": ["financial-data-akshare", "quant-strategy-management"],
    },
    "browser-automation": {
        "name": "浏览器自动化工具包",
        "description": "连接并自动化已授权的 Chrome 会话",
        "packages": ["playwright", "uiautomation"],
        "imports": ["playwright", "uiautomation"],
        "skills": ["browser-automation"],
    },
    "web-research": {
        "name": "网页研究工具包",
        "description": "网页搜索、解析与结构化采集",
        "packages": ["beautifulsoup4", "duckduckgo-search", "scrapling"],
        "imports": ["bs4", "duckduckgo_search", "scrapling"],
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


def installed_toolkit_paths():
    paths = []
    for toolkit_id in TOOLKITS:
        path = toolkit_path(toolkit_id)
        marker = os.path.join(os.path.dirname(path), "toolkit.json")
        if os.path.isdir(path) and os.path.isfile(marker):
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


def toolkit_status(toolkit_id):
    spec = TOOLKITS[toolkit_id]
    root = os.path.dirname(toolkit_path(toolkit_id))
    marker_path = os.path.join(root, "toolkit.json")
    marker = {}
    if os.path.isfile(marker_path):
        try:
            with open(marker_path, "r", encoding="utf-8") as handle:
                marker = json.load(handle)
        except Exception:
            marker = {}
    return {
        "id": toolkit_id,
        **spec,
        "installed": bool(marker and os.path.isdir(toolkit_path(toolkit_id))),
        "source": marker.get("source") or "",
        "size": _directory_size(root),
    }


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
    target = toolkit_path(toolkit_id)
    os.makedirs(target, exist_ok=True)
    if progress_callback:
        progress_callback(f"正在从 {python_source.get('name') or source_url} 安装 {spec['name']}…", 10)
    command = [python_exe, "-m", "pip", "install", "--index-url", source_url, "--upgrade", "--target", target]
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
    marker = {
        "id": toolkit_id,
        "packages": spec["packages"],
        "source": python_source.get("name") or source_url,
        "source_url": source_url,
    }
    with open(os.path.join(os.path.dirname(target), "toolkit.json"), "w", encoding="utf-8") as handle:
        json.dump(marker, handle, ensure_ascii=False, indent=2)
    if progress_callback:
        progress_callback(f"{spec['name']}安装完成。", 100)
    return toolkit_status(toolkit_id)


def uninstall_toolkit(toolkit_id):
    if toolkit_id not in TOOLKITS:
        raise KeyError(f"未知工具包：{toolkit_id}")
    root = os.path.dirname(toolkit_path(toolkit_id))
    if os.path.isdir(root):
        shutil.rmtree(root)
    return toolkit_status(toolkit_id)


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
