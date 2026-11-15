"""Optional Node runtime preparation for JavaScript tools (no settings UI)."""
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from urllib.parse import urlparse

import requests

from core.env_utils import get_app_data_dir
from core.process_utils import subprocess_kwargs_no_window


NODE_VERSION = "v24.14.1"
NODE_ARCHIVE = "node-v24.14.1-win-x64.zip"
NODE_SHA256 = "6E50CE5498C0CEBC20FD39AB3FF5DF836ED2F8A31AA093CECAD8497CFF126D70"


def node_release():
    from .platform_capabilities import desktop_target
    target = desktop_target()
    if target == "windows-x64":
        return NODE_ARCHIVE, NODE_SHA256, "node.exe"
    from .runtime_manifest import native_runtime_release
    release = native_runtime_release("node", target)
    return release.filename, release.sha256.upper(), release.executable

def valid_https_source(url):
    try:
        parsed = urlparse(str(url or "").strip())
        return parsed.scheme.lower() == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except Exception:
        return False


def node_runtime_status():
    from core.sandbox_runtime import get_runtime_executable, _node_process_env
    path = get_runtime_executable("node")
    version = ""
    if path:
        try:
            completed = subprocess.run(
                [path, "--version"],
                env=_node_process_env(path, os.environ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                **subprocess_kwargs_no_window(),
            )
            if completed.returncode == 0:
                version = str(completed.stdout or completed.stderr or "").strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    return {"installed": bool(path and version), "path": path, "version": version}


def _safe_extract(archive, target):
    target_abs = os.path.abspath(target)
    for member in archive.infolist():
        candidate = os.path.abspath(os.path.join(target_abs, member.filename))
        if os.path.commonpath([target_abs, candidate]) != target_abs:
            raise RuntimeError("Node.js 压缩包包含不安全路径。")
    archive.extractall(target)


def safe_extract_runtime_tar(archive_path, target):
    """Preserve executable bits and internal symlinks, reject archive escapes."""
    with tarfile.open(archive_path, "r:*") as archive:
        if not hasattr(tarfile, "data_filter"):
            raise RuntimeError("解压运行时需要带 tarfile.data_filter 的 Python，请升级构建 Python。")
        archive.extractall(target, filter="data")


def install_node_runtime(node_source=None, progress_callback=None):
    from core.sandbox_runtime import reset_runtime_cache

    node_source = node_source or {"name": "Node.js 官方", "url": "https://nodejs.org/dist/"}
    source_url = str(node_source.get("url") or "")
    archive_name, expected_hash, executable_name = node_release()
    package_path = str(node_source.get("package_path") or "")
    if not package_path and not valid_https_source(source_url):
        raise ValueError("Node.js 下载源必须是有效的 HTTPS 地址。")
    url = f"{source_url.rstrip('/')}/{NODE_VERSION}/{archive_name}"
    runtime_root = os.path.join(get_app_data_dir(), "runtime_sandbox", "v1", "runtimes")
    target = os.path.join(runtime_root, "node")
    os.makedirs(runtime_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cowork-node-", dir=runtime_root) as temp_dir:
        archive_path = os.path.join(temp_dir, archive_name)
        digest = hashlib.sha256()
        if progress_callback:
            progress_callback(f"正在从 {node_source.get('name') or source_url} 下载 Node.js…", 1)
        if package_path:
            shutil.copyfile(package_path, archive_path)
            with open(archive_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(512 * 1024), b""):
                    digest.update(chunk)
        else:
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
        if actual != expected_hash:
            raise RuntimeError(f"Node.js SHA-256 校验失败：期望 {expected_hash}，实际 {actual}")
        extract_dir = os.path.join(temp_dir, "extract")
        os.makedirs(extract_dir)
        if archive_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as archive:
                _safe_extract(archive, extract_dir)
        else:
            safe_extract_runtime_tar(archive_path, extract_dir)
        dirs = [item.path for item in os.scandir(extract_dir) if item.is_dir()]
        if len(dirs) != 1 or not os.path.isfile(os.path.join(dirs[0], executable_name)):
            raise RuntimeError("Node.js 压缩包结构无效。")
        staged = dirs[0]
        with open(os.path.join(staged, ".cowork_runtime_source"), "w", encoding="utf-8") as handle:
            handle.write(f"{NODE_VERSION}|{source_url}")
        from .file_write_locks import file_write_lock
        with file_write_lock(target):
            previous = os.path.join(temp_dir, "previous")
            if os.path.isdir(target):
                os.replace(target, previous)
            try:
                os.replace(staged, target)
            except BaseException:
                if os.path.isdir(previous):
                    os.replace(previous, target)
                raise
    reset_runtime_cache()
    if progress_callback:
        progress_callback("Node.js 安装完成。", 100)
    return node_runtime_status()
