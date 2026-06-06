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
from core.process_utils import subprocess_kwargs_no_window
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


def expected_asset_path(asset, target_dir=None):
    if isinstance(asset, dict):
        asset_name = str(asset.get("name") or "")
    else:
        asset_name = getattr(asset, "name", "")
    target_dir = target_dir or updates_dir()
    return os.path.join(target_dir, _safe_filename(asset_name))


def is_cached_asset_valid(asset, zip_path):
    if not os.path.isfile(zip_path):
        return False
    expected_size = int((asset.get("size") if isinstance(asset, dict) else getattr(asset, "size", 0)) or 0)
    if expected_size and os.path.getsize(zip_path) != expected_size:
        return False
    return True


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
    zip_path = expected_asset_path(asset, target_dir=target_dir)
    if is_cached_asset_valid(asset, zip_path):
        _emit(progress_callback, f"已找到本地安装包，跳过下载：{zip_path}", 100)
        return zip_path
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
    _emit(progress_callback, "正在检查 GitHub Releases", 5)
    release = fetch_latest_release()
    tag_name = str(release.get("tag_name") or release.get("name") or "").strip()
    html_url = str(release.get("html_url") or GITHUB_RELEASES_URL)
    update_available = is_newer_version(tag_name, current_version)
    _emit(progress_callback, f"发现最新版本：{tag_name or '未知'}", 10)
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
    _emit(progress_callback, "正在选择 Windows 更新包", 12)
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


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _update_script_paths(target_dir, stamp):
    return (
        os.path.join(target_dir, f"apply-update-{stamp}.ps1"),
        os.path.join(target_dir, f"apply-update-{stamp}.cmd"),
    )


def _normalize_wait_pids(current_pid=None, extra_wait_pids=None):
    pids = []
    for value in [current_pid or os.getpid()] + list(extra_wait_pids or []):
        try:
            pid = int(value)
        except Exception:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _write_fallback_cmd_script(script_path, install_dir, staged_app_dir, backup_dir, log_path, wait_pids, exe_name):
    primary_pid = wait_pids[0]
    wait_pid_lines = "\n".join(
        [
            f"call :wait_pid {pid}"
            for pid in wait_pids
        ]
    )
    content = f"""@echo off
setlocal
set "PID={primary_pid}"
set "SRC={_cmd_quote(staged_app_dir)}"
set "DEST={_cmd_quote(install_dir)}"
set "BACKUP={_cmd_quote(backup_dir)}"
set "EXE={_cmd_quote(exe_name)}"
set "LOG={_cmd_quote(log_path)}"
title DeepSeek Cowork Update
echo DeepSeek Cowork update is running.
echo Log: %LOG%
echo [%date% %time%] [0%%] Starting update. > "%LOG%"
{wait_pid_lines}
goto after_wait

:wait_pid
set "WAIT_PID=%~1"
echo [10%%] Waiting for app to exit...
echo [%date% %time%] [10%%] Waiting for process %WAIT_PID% to exit. >> "%LOG%"
:wait_loop
tasklist /FI "PID eq %WAIT_PID%" | find "%WAIT_PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait_loop
)
exit /b 0

:after_wait
echo [25%%] Backing up current installation...
echo [%date% %time%] [25%%] Backing up current installation. >> "%LOG%"
robocopy "%DEST%" "%BACKUP%" /E /XD "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto fail
echo [55%%] Copying new version...
echo [%date% %time%] [55%%] Copying new version. >> "%LOG%"
robocopy "%SRC%" "%DEST%" /MIR /XD "%SRC%\\user_data" "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto fail
echo [75%%] Preserved user_data.
echo [%date% %time%] [75%%] Preserved user_data. >> "%LOG%"
echo [90%%] Restarting application...
echo [%date% %time%] [90%%] Restarting application. >> "%LOG%"
start "" "%DEST%\\%EXE%"
echo [%date% %time%] [100%%] Update completed. >> "%LOG%"
exit /b 0
:fail
echo [%date% %time%] Update failed with code %RC%. >> "%LOG%"
echo Update failed. See log: %LOG%
if exist "%BACKUP%\\%EXE%" (
  echo Attempting rollback...
  robocopy "%BACKUP%" "%DEST%" /MIR /XD "%BACKUP%\\user_data" "%DEST%\\user_data" "user_data" /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
)
start "" "%DEST%\\%EXE%"
pause
exit /b 1
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(content)


def _write_gui_ps_update_script(script_path, install_dir, staged_app_dir, backup_dir, log_path, wait_pids, exe_name):
    ps_wait_pids = "@(" + ", ".join(str(int(pid)) for pid in wait_pids) + ")"
    content = f"""Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$PidsToWait = {ps_wait_pids}
$SourceDir = {_ps_quote(staged_app_dir)}
$InstallDir = {_ps_quote(install_dir)}
$BackupDir = {_ps_quote(backup_dir)}
$ExeName = {_ps_quote(exe_name)}
$LogPath = {_ps_quote(log_path)}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'DeepSeek Cowork Update'
$form.Width = 620
$form.Height = 420
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Updating DeepSeek Cowork'
$title.AutoSize = $true
$title.Left = 18
$title.Top = 18
$title.Font = New-Object System.Drawing.Font -ArgumentList 'Microsoft YaHei UI', 12, ([System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Preparing...'
$status.Left = 18
$status.Top = 56
$status.Width = 560
$status.Height = 24
$form.Controls.Add($status)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Left = 18
$progress.Top = 86
$progress.Width = 560
$progress.Height = 22
$progress.Minimum = 0
$progress.Maximum = 100
$progress.Value = 0
$form.Controls.Add($progress)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Left = 18
$logBox.Top = 124
$logBox.Width = 560
$logBox.Height = 210
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$form.Controls.Add($logBox)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = 'Close'
$closeButton.Left = 500
$closeButton.Top = 348
$closeButton.Width = 78
$closeButton.Enabled = $false
$closeButton.Add_Click({{ $form.Close() }})
$form.Controls.Add($closeButton)

function Add-UpdateLog([string]$Message, [int]$Percent = -1) {{
    $line = "[{{0}}] {{1}}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    $logBox.AppendText($line + [Environment]::NewLine)
    $status.Text = $Message
    if ($Percent -ge 0) {{
        $progress.Value = [Math]::Max(0, [Math]::Min(100, $Percent))
    }}
    [System.Windows.Forms.Application]::DoEvents()
}}

function Invoke-Robocopy([string[]]$RoboArgs, [string]$FailureMessage) {{
    $output = & robocopy @RoboArgs 2>&1
    foreach ($line in $output) {{
        if ($line) {{ Add-Content -Path $LogPath -Value $line -Encoding UTF8 }}
    }}
    $code = $LASTEXITCODE
    if ($code -ge 8) {{
        throw "$FailureMessage (robocopy exit code $code)"
    }}
}}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({{
    $timer.Stop()
    try {{
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
        Set-Content -Path $LogPath -Value '' -Encoding UTF8

        foreach ($pidItem in $PidsToWait) {{
            Add-UpdateLog ('Waiting for process to exit: ' + $pidItem) 10
            while (Get-Process -Id $pidItem -ErrorAction SilentlyContinue) {{
                Start-Sleep -Milliseconds 500
                [System.Windows.Forms.Application]::DoEvents()
            }}
        }}

        Add-UpdateLog 'Backing up current installation...' 25
        Invoke-Robocopy -RoboArgs @($InstallDir, $BackupDir, '/E', '/XD', (Join-Path $InstallDir 'user_data'), 'user_data', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -FailureMessage 'Backup failed'

        Add-UpdateLog 'Copying new version...' 55
        Invoke-Robocopy -RoboArgs @($SourceDir, $InstallDir, '/MIR', '/XD', (Join-Path $SourceDir 'user_data'), (Join-Path $InstallDir 'user_data'), 'user_data', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -FailureMessage 'Copy failed'

        Add-UpdateLog 'Preserved user_data directory.' 75
        Add-UpdateLog 'Starting updated application...' 90
        Start-Process -FilePath (Join-Path $InstallDir $ExeName) -WorkingDirectory $InstallDir -WindowStyle Hidden
        Add-UpdateLog 'Update completed. This window will close automatically.' 100
        Start-Sleep -Seconds 2
        $form.Close()
    }} catch {{
        Add-UpdateLog ("Update failed: " + $_.Exception.Message) 100
        if (Test-Path (Join-Path $BackupDir $ExeName)) {{
            try {{
                Add-UpdateLog 'Attempting rollback...' 100
                Invoke-Robocopy -RoboArgs @($BackupDir, $InstallDir, '/MIR', '/XD', (Join-Path $BackupDir 'user_data'), (Join-Path $InstallDir 'user_data'), 'user_data', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -FailureMessage 'Rollback failed'
                Start-Process -FilePath (Join-Path $InstallDir $ExeName) -WorkingDirectory $InstallDir -WindowStyle Hidden
                Add-UpdateLog 'Rolled back and restarted the previous version.' 100
            }} catch {{
                Add-UpdateLog ("Rollback failed: " + $_.Exception.Message) 100
            }}
        }}
        $status.Text = 'Update failed. Log: ' + $LogPath
        $closeButton.Enabled = $true
        $form.TopMost = $false
    }}
}})

$form.Add_Shown({{ $timer.Start() }})
[void]$form.ShowDialog()
"""
    with open(script_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(content)


def create_windows_update_script(install_dir, staged_app_dir, current_pid=None, exe_name=APP_EXE_NAME, target_dir=None, extra_wait_pids=None):
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
    ps_script_path, cmd_script_path = _update_script_paths(target_dir, stamp)
    wait_pids = _normalize_wait_pids(current_pid, extra_wait_pids)
    _write_gui_ps_update_script(ps_script_path, install_dir, staged_app_dir, backup_dir, log_path, wait_pids, exe_name)
    _write_fallback_cmd_script(cmd_script_path, install_dir, staged_app_dir, backup_dir, log_path, wait_pids, exe_name)
    return ps_script_path


def launch_windows_update_script(script_path):
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    script_path = os.path.abspath(script_path)
    fallback_cmd = os.path.splitext(script_path)[0] + ".cmd"
    launch_log = os.path.join(os.path.dirname(script_path), "update-launch.log")
    powershell_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(powershell_path):
        powershell_path = "powershell.exe"
    log_handle = None
    try:
        log_handle = open(launch_log, "a", encoding="utf-8")
        log_handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching GUI updater: {script_path}\n")
        log_handle.flush()
        subprocess.Popen(
            [
                powershell_path,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-STA",
                "-File",
                script_path,
            ],
            close_fds=True,
            cwd=os.path.dirname(script_path),
            stdout=log_handle,
            stderr=log_handle,
            **subprocess_kwargs_no_window(),
        )
    except Exception:
        if not os.path.exists(fallback_cmd):
            raise
        try:
            with open(launch_log, "a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] GUI updater launch failed, falling back to cmd.\n")
        except Exception:
            pass
        subprocess.Popen(
            ["cmd.exe", "/c", fallback_cmd],
            close_fds=True,
            cwd=os.path.dirname(fallback_cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **subprocess_kwargs_no_window(),
        )
    finally:
        if log_handle:
            try:
                log_handle.close()
            except Exception:
                pass
