import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass

import requests

from core.app_version import APP_VERSION, is_newer_version
from core.env_utils import get_app_data_dir

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/chuancyzhang/deepseek-cowork/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/chuancyzhang/deepseek-cowork/releases"
APP_EXE_NAME = "deepseek-cowork.exe"
INTERNAL_DIR_NAME = "_internal"
UPDATE_DIR_NAME = "updates"


class UpdaterError(RuntimeError):
    pass


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0

    def as_dict(self):
        return {
            "name": self.name,
            "browser_download_url": self.download_url,
            "size": self.size,
        }


def updates_dir():
    path = os.path.join(get_app_data_dir(), UPDATE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def fetch_latest_release(timeout=12):
    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "deepseek-cowork-updater",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise UpdaterError(f"检查 GitHub Releases 失败：{exc}") from exc
    if not isinstance(data, dict):
        raise UpdaterError("GitHub Releases 返回格式无效。")
    if data.get("draft") or data.get("prerelease"):
        raise UpdaterError("最新 Release 不是正式版本。")
    return data


def select_release_asset(release):
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        assets = []
    candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        lower_name = name.lower()
        if not lower_name.endswith(".zip"):
            continue
        if not re.match(r"^deepseek-cowork.*\.zip$", lower_name):
            continue
        candidates.append(
            ReleaseAsset(
                name=name,
                download_url=url,
                size=int(asset.get("size") or 0),
            )
        )
    if not candidates:
        raise UpdaterError("最新 Release 中没有找到 deepseek-cowork 的 zip 安装包。")
    candidates.sort(key=lambda item: (0 if item.name.lower().startswith("deepseek-cowork-v") else 1, item.name))
    return candidates[0]


def _safe_filename(name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(str(name or ""))).strip(".-")
    return cleaned or "deepseek-cowork-update.zip"


def _emit(progress_callback, message, percent=None):
    if progress_callback:
        progress_callback(message, percent)


def download_asset(asset, target_dir=None, progress_callback=None, timeout=30):
    if isinstance(asset, dict):
        asset = ReleaseAsset(
            name=str(asset.get("name") or ""),
            download_url=str(asset.get("browser_download_url") or ""),
            size=int(asset.get("size") or 0),
        )
    if not asset.download_url:
        raise UpdaterError("Release 资源缺少下载地址。")
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(target_dir, _safe_filename(asset.name))
    temp_path = zip_path + ".download"
    _emit(progress_callback, f"正在下载 {asset.name}", 0)
    try:
        with requests.get(asset.download_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            expected = asset.size or int(response.headers.get("content-length") or 0)
            downloaded = 0
            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if expected:
                        percent = min(99, int(downloaded * 100 / expected))
                        _emit(progress_callback, f"正在下载 {asset.name}", percent)
        actual_size = os.path.getsize(temp_path)
        if asset.size and actual_size != asset.size:
            raise UpdaterError(f"下载大小校验失败：期望 {asset.size} 字节，实际 {actual_size} 字节。")
        os.replace(temp_path, zip_path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        if isinstance(exc, UpdaterError):
            raise
        raise UpdaterError(f"下载更新包失败：{exc}") from exc
    _emit(progress_callback, "更新包下载完成", 100)
    return zip_path


def find_staged_app_dir(root_dir):
    if _is_valid_app_dir(root_dir):
        return root_dir
    for current_root, dirs, _files in os.walk(root_dir):
        if _is_valid_app_dir(current_root):
            return current_root
        dirs[:] = [name for name in dirs if name not in {"__MACOSX", ".git"}]
    raise UpdaterError("更新包结构无效：未找到 deepseek-cowork.exe 和 _internal 目录。")


def _is_valid_app_dir(path):
    return (
        os.path.isfile(os.path.join(path, APP_EXE_NAME))
        and os.path.isdir(os.path.join(path, INTERNAL_DIR_NAME))
    )


def _safe_extractall(archive, target_dir):
    target_root = os.path.abspath(target_dir)
    for member in archive.infolist():
        member_path = os.path.abspath(os.path.join(target_root, member.filename))
        if os.path.commonpath([target_root, member_path]) != target_root:
            raise UpdaterError("更新包包含不安全的路径。")
    archive.extractall(target_root)


def extract_update_zip(zip_path, target_dir=None, progress_callback=None):
    if not os.path.isfile(zip_path):
        raise UpdaterError("更新包不存在。")
    target_dir = target_dir or updates_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    extract_dir = os.path.join(target_dir, f"staged-{stamp}")
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir, exist_ok=True)
    _emit(progress_callback, "正在解压更新包", None)
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            _safe_extractall(archive, extract_dir)
    except zipfile.BadZipFile as exc:
        raise UpdaterError("更新包不是有效的 zip 文件。") from exc
    except Exception as exc:
        raise UpdaterError(f"解压更新包失败：{exc}") from exc
    app_dir = find_staged_app_dir(extract_dir)
    _emit(progress_callback, "更新包校验完成", None)
    return app_dir


def prepare_update(current_version=APP_VERSION, download=False, progress_callback=None):
    release = fetch_latest_release()
    tag_name = str(release.get("tag_name") or release.get("name") or "").strip()
    html_url = str(release.get("html_url") or GITHUB_RELEASES_URL)
    update_available = is_newer_version(tag_name, current_version)
    result = {
        "current_version": current_version,
        "update_available": update_available,
        "release": {
            "tag_name": tag_name,
            "name": release.get("name") or tag_name,
            "body": release.get("body") or "",
            "html_url": html_url,
        },
    }
    if not update_available:
        return result
    asset = select_release_asset(release)
    result["asset"] = asset.as_dict()
    if not download:
        return result
    target_dir = updates_dir()
    zip_path = download_asset(asset, target_dir=target_dir, progress_callback=progress_callback)
    staged_app_dir = extract_update_zip(zip_path, target_dir=target_dir, progress_callback=progress_callback)
    result.update({
        "zip_path": zip_path,
        "staged_app_dir": staged_app_dir,
        "updates_dir": target_dir,
    })
    return result


def _cmd_quote(value):
    return str(value).replace('"', '""')


def create_windows_update_script(install_dir, staged_app_dir, current_pid=None, exe_name=APP_EXE_NAME, target_dir=None):
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    install_dir = os.path.abspath(install_dir)
    staged_app_dir = os.path.abspath(staged_app_dir)
    if not _is_valid_app_dir(staged_app_dir):
        raise UpdaterError("待安装目录结构无效。")
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(target_dir, f"backup-{stamp}")
    log_path = os.path.join(target_dir, "update.log")
    script_path = os.path.join(target_dir, f"apply-update-{stamp}.cmd")
    pid = int(current_pid or os.getpid())
    content = f"""@echo off
setlocal
set "PID={pid}"
set "SRC={_cmd_quote(staged_app_dir)}"
set "DEST={_cmd_quote(install_dir)}"
set "BACKUP={_cmd_quote(backup_dir)}"
set "EXE={_cmd_quote(exe_name)}"
set "LOG={_cmd_quote(log_path)}"
echo [%date% %time%] Starting update. > "%LOG%"
:wait_app
tasklist /FI "PID eq %PID%" | find "%PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait_app
)
echo [%date% %time%] Backing up current installation. >> "%LOG%"
robocopy "%DEST%" "%BACKUP%" /E /XD "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto fail
echo [%date% %time%] Copying new version. >> "%LOG%"
robocopy "%SRC%" "%DEST%" /MIR /XD "%SRC%\\user_data" "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto fail
echo [%date% %time%] Restarting application. >> "%LOG%"
start "" "%DEST%\\%EXE%"
exit /b 0
:fail
echo [%date% %time%] Update failed with code %RC%. >> "%LOG%"
if exist "%BACKUP%\\%EXE%" (
  robocopy "%BACKUP%" "%DEST%" /MIR /XD "%BACKUP%\\user_data" "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
)
start "" "%DEST%\\%EXE%"
exit /b 1
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(content)
    return script_path


def launch_windows_update_script(script_path):
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", os.path.abspath(script_path)],
        close_fds=True,
    )
