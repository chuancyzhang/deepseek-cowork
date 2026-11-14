"""Optional, versioned LibreOffice runtime; no system installation or associations."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import time

import requests

from .env_utils import get_app_data_dir
from .process_utils import popen_external_program, subprocess_kwargs_no_window, terminate_process_tree

log = logging.getLogger(__name__)
VERSION = "26.2.7.2"
# Pinned official release, with checksum published by The Document Foundation.
FILENAME = f"LibreOffice_{VERSION}_Win_x86-64.msi"
URL = f"https://downloadarchive.documentfoundation.org/libreoffice/old/{VERSION}/win/x86_64/{FILENAME}"
SHA256 = "4dc72c6d99b2f7c28a320eb7995e5f2b400ac1468ea9ff6f52592c3ac093e647"
SIZE = 373186560
_install_lock = threading.Lock()


def component_root():
    return Path(get_app_data_dir()).resolve() / "runtime_components" / "libreoffice"


def managed_executable():
    path = component_root() / VERSION / "program" / "soffice.com"
    return str(path) if path.is_file() else None


def can_install():
    return os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64"}


def status():
    from .document_preview import _libreoffice_executable
    from .workbench import WorkbenchError
    try:
        executable = _libreoffice_executable()
        return {"available": True, "install_supported": can_install(), "managed": executable == managed_executable()}
    except WorkbenchError as exc:
        return {"available": False, "install_supported": can_install(), "message": str(exc)}


def install(cancelled, progress, *, package=None):
    """Extract a verified MSI administratively into app data, then publish atomically."""
    from .desktop_jobs import JobCancelled
    def check():
        if cancelled.is_set():
            raise JobCancelled("已取消 LibreOffice 安装，原文件和已有组件保留。")

    if not can_install():
        raise ValueError("此平台请安装 LibreOffice 后重试预览。")
    if not _install_lock.acquire(blocking=False):
        raise ValueError("LibreOffice 正在安装，可在设置的后台操作中查看进度。")
    try:
        check()
        if managed_executable():
            return {"installed": True}
        root = component_root()
        root.mkdir(parents=True, exist_ok=True)
        log.info("libreoffice_install.start version=%s", VERSION)
        with tempfile.TemporaryDirectory(prefix="install-", dir=root) as temporary:
            staging = Path(temporary).resolve()
            if not staging.is_relative_to(root):
                raise ValueError("组件临时目录无效。")
            archive = Path(package).resolve() if package else staging / FILENAME
            if not package:
                progress("正在下载 LibreOffice（约 356 MiB）…")
                received, last = 0, 0
                with requests.get(URL, stream=True, timeout=(20, 30)) as response:
                    response.raise_for_status()
                    with archive.open("xb") as output:
                        for block in response.iter_content(1024 * 1024):
                            check()
                            received += len(block)
                            if received > SIZE:
                                raise ValueError("LibreOffice 下载大小与发布记录不符。")
                            output.write(block)
                            if time.monotonic() - last >= 1:
                                progress(f"正在下载 LibreOffice · {received * 100 // SIZE}%")
                                last = time.monotonic()
            check()
            progress("正在校验 LibreOffice…")
            digest = hashlib.sha256()
            with archive.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    check()
                    digest.update(block)
            if archive.stat().st_size != SIZE or digest.hexdigest() != SHA256:
                raise ValueError("LibreOffice 安装包校验失败，请重新安装。")
            progress("正在准备 LibreOffice…")
            image = staging / "image"
            image.mkdir()
            trace = staging / "extract.log"
            executable = Path(os.environ["SystemRoot"]) / "System32" / "msiexec.exe"
            process = popen_external_program(
                [str(executable), "/a", str(archive), "/qn", f"TARGETDIR={image}", "/L*v", str(trace)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **subprocess_kwargs_no_window(),
            )
            try:
                deadline = time.monotonic() + 240
                while process.poll() is None:
                    check()
                    if time.monotonic() > deadline:
                        raise TimeoutError("LibreOffice 准备超时，请重试安装。")
                    cancelled.wait(.15)
            finally:
                if process.poll() is None:
                    terminate_process_tree(process)
            if process.returncode != 0:
                log.warning("libreoffice_install.extract_failed exit=%s", process.returncode)
                raise RuntimeError(f"LibreOffice 准备失败（安装程序代码 {process.returncode}）。已有文件保留，可重新安装。")
            candidates = list(image.rglob("program/soffice.com"))
            if len(candidates) != 1:
                raise ValueError("LibreOffice 安装包缺少唯一的程序入口。")
            extracted = candidates[0].parent.parent
            # Administrative MSI extraction does not install shared runtimes or
            # fonts. Use the copies distributed with this exact verified package.
            for library in (extracted / "System64").glob("*.dll"):
                shutil.copy2(library, extracted / "program" / library.name)
            if (extracted / "Fonts").is_dir():
                shutil.copytree(extracted / "Fonts", extracted / "share/fonts/truetype", dirs_exist_ok=True)
            check()
            probe = subprocess.run([str(candidates[0]), "--headless", "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                                   timeout=25, **subprocess_kwargs_no_window())
            if probe.returncode or b"LibreOffice" not in probe.stdout:
                raise RuntimeError("LibreOffice 启动验证失败，请重新安装。")
            check()
            target = root / VERSION
            if target.exists():
                raise RuntimeError("组件目录已存在但不可用，请在本机修复 LibreOffice 后重试。")
            os.replace(candidates[0].parent.parent, target)
        log.info("libreoffice_install.completed version=%s", VERSION)
        return {"installed": True}
    except Exception as exc:
        log.warning("libreoffice_install.failed version=%s reason=%s", VERSION, type(exc).__name__)
        raise
    finally:
        _install_lock.release()
