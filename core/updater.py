import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass

import requests

from core.app_version import APP_VERSION, compare_versions, is_newer_version
from core.process_utils import subprocess_kwargs_no_window
from core.env_utils import get_app_data_dir

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/chuancyzhang/deepseek-cowork/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/chuancyzhang/deepseek-cowork/releases"
APP_EXE_NAME = "deepseek-cowork.exe"
INTERNAL_DIR_NAME = "_internal"
UPDATE_DIR_NAME = "updates"
UPDATE_PLAN_FORMAT = 1
UPDATE_EXCLUDED_ROOTS = {"user_data"}
LOCAL_UPDATE_PACKAGE_PATTERN = re.compile(
    r"^deepseek-cowork-v(?P<version>\d+\.\d+\.\d+)\.zip$",
    re.IGNORECASE,
)


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


def _normalized_relative_path(path):
    value = str(path or "").replace("\\", "/").strip("/")
    if not value or value == ".":
        raise UpdaterError("更新文件路径不能为空。")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise UpdaterError(f"更新文件路径不是相对路径：{path}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UpdaterError(f"更新文件路径不安全：{path}")
    return "/".join(parts)


def _is_update_excluded(relative_path):
    normalized = str(relative_path or "").replace("\\", "/").strip("/")
    return bool(normalized and normalized.split("/", 1)[0].lower() in UPDATE_EXCLUDED_ROOTS)


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_app_files(root_dir):
    root_dir = os.path.abspath(root_dir)
    files = {}
    for current_root, dirs, names in os.walk(root_dir):
        relative_root = os.path.relpath(current_root, root_dir)
        if relative_root == ".":
            dirs[:] = [name for name in dirs if not _is_update_excluded(name)]
        for name in names:
            full_path = os.path.join(current_root, name)
            relative_path = os.path.relpath(full_path, root_dir).replace("\\", "/")
            if _is_update_excluded(relative_path):
                continue
            normalized = _normalized_relative_path(relative_path)
            files[normalized] = {
                "path": normalized,
                "size": os.path.getsize(full_path),
                "full_path": full_path,
            }
    return files


def build_update_plan(install_dir, staged_app_dir, progress_callback=None):
    install_dir = os.path.abspath(install_dir)
    staged_app_dir = os.path.abspath(staged_app_dir)
    if not _is_valid_app_dir(staged_app_dir):
        raise UpdaterError("待安装目录结构无效。")
    if not os.path.isdir(install_dir):
        raise UpdaterError("当前安装目录不存在。")

    _emit(progress_callback, "正在比较当前版本与新版本文件", None)
    current_files = _collect_app_files(install_dir)
    staged_files = _collect_app_files(staged_app_dir)
    added = []
    modified = []
    unchanged = 0

    for relative_path, staged_info in sorted(staged_files.items()):
        staged_hash = _hash_file(staged_info["full_path"])
        operation = {
            "path": relative_path,
            "size": staged_info["size"],
            "sha256": staged_hash,
        }
        current_info = current_files.get(relative_path)
        if current_info is None:
            added.append(operation)
            continue
        if current_info["size"] == staged_info["size"] and _hash_file(current_info["full_path"]) == staged_hash:
            unchanged += 1
            continue
        modified.append(operation)

    deleted = [
        {"path": relative_path}
        for relative_path in sorted(set(current_files) - set(staged_files))
    ]
    write_bytes = sum(item["size"] for item in added + modified)
    plan = {
        "format_version": UPDATE_PLAN_FORMAT,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "summary": {
            "added": len(added),
            "modified": len(modified),
            "deleted": len(deleted),
            "unchanged": unchanged,
            "write_bytes": write_bytes,
        },
    }
    summary = plan["summary"]
    _emit(
        progress_callback,
        f"文件比较完成：新增 {summary['added']}，修改 {summary['modified']}，删除 {summary['deleted']}，未变化 {summary['unchanged']}",
        None,
    )
    return plan


def write_update_plan(plan, target_dir=None):
    if not isinstance(plan, dict) or plan.get("format_version") != UPDATE_PLAN_FORMAT:
        raise UpdaterError("更新操作清单格式无效。")
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    plan_path = os.path.join(target_dir, f"apply-update-{stamp}.json")
    with open(plan_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
    return plan_path


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


def cleanup_update_artifacts(target_dir=None, keep_paths=None, progress_callback=None):
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    keep = {os.path.abspath(path) for path in (keep_paths or []) if path}
    removed = 0
    patterns = (
        re.compile(r"^staged-\d{8}-\d{6}$", re.IGNORECASE),
        re.compile(r"^backup-\d{8}-\d{6}$", re.IGNORECASE),
        re.compile(r"^apply-update-\d{8}-\d{6}\.(ps1|cmd)$", re.IGNORECASE),
        re.compile(r"^apply-update-\d{8}-\d{6}\.json$", re.IGNORECASE),
        re.compile(r"^deepseek-cowork.*\.zip$", re.IGNORECASE),
        re.compile(r"^deepseek-cowork.*\.zip\.download$", re.IGNORECASE),
    )
    for name in os.listdir(target_dir):
        path = os.path.join(target_dir, name)
        abs_path = os.path.abspath(path)
        if abs_path in keep:
            continue
        if not any(pattern.match(name) for pattern in patterns):
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except FileNotFoundError:
            continue
        except Exception:
            continue
    for log_name in ("update.log", "update-launch.log"):
        log_path = os.path.join(target_dir, log_name)
        if os.path.abspath(log_path) in keep:
            continue
        try:
            if os.path.exists(log_path):
                os.remove(log_path)
                removed += 1
        except Exception:
            continue
    _emit(progress_callback, f"已清理历史更新痕迹：{removed} 项", None)
    return removed


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


def prepare_update(current_version=APP_VERSION, download=False, progress_callback=None, install_dir=None):
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
    expected_zip_path = expected_asset_path(asset, target_dir=target_dir)
    keep_paths = [expected_zip_path] if is_cached_asset_valid(asset, expected_zip_path) else []
    cleanup_update_artifacts(target_dir=target_dir, keep_paths=keep_paths, progress_callback=progress_callback)
    zip_path = download_asset(asset, target_dir=target_dir, progress_callback=progress_callback)
    staged_app_dir = extract_update_zip(zip_path, target_dir=target_dir, progress_callback=progress_callback)
    result.update({
        "zip_path": zip_path,
        "staged_app_dir": staged_app_dir,
        "updates_dir": target_dir,
    })
    if install_dir:
        change_plan = build_update_plan(install_dir, staged_app_dir, progress_callback=progress_callback)
        change_plan_path = write_update_plan(change_plan, target_dir=target_dir)
        result.update({
            "change_plan": change_plan,
            "change_plan_path": change_plan_path,
            "change_summary": change_plan["summary"],
        })
    return result


def local_update_package_version(zip_path):
    package_name = os.path.basename(os.fspath(zip_path or ""))
    match = LOCAL_UPDATE_PACKAGE_PATTERN.fullmatch(package_name)
    if not match:
        raise UpdaterError(
            "本地安装包名称无效。请选择名称为 deepseek-cowork-vX.Y.Z.zip 的官方安装包。"
        )
    return match.group("version")


def prepare_local_update(
    zip_path,
    current_version=APP_VERSION,
    install_dir=None,
    progress_callback=None,
):
    source_path = os.path.abspath(os.fspath(zip_path or ""))
    if not source_path or not os.path.isfile(source_path):
        raise UpdaterError("本地安装包不存在或已被删除。")
    if not install_dir:
        raise UpdaterError("本地更新缺少当前安装目录。")

    package_name = os.path.basename(source_path)
    package_version = local_update_package_version(source_path)
    if compare_versions(package_version, current_version) <= 0:
        raise UpdaterError(
            f"本地安装包版本 {package_version} 不高于当前版本 {current_version}，不能安装。"
        )

    package_size = os.path.getsize(source_path)
    _emit(progress_callback, f"正在验证本地安装包 {package_name}", 5)
    target_dir = updates_dir()
    cleanup_update_artifacts(
        target_dir=target_dir,
        keep_paths=[source_path],
        progress_callback=progress_callback,
    )
    staged_app_dir = extract_update_zip(
        source_path,
        target_dir=target_dir,
        progress_callback=progress_callback,
    )
    _emit(progress_callback, "正在生成本地更新差异清单", 70)
    change_plan = build_update_plan(
        os.path.abspath(os.fspath(install_dir)),
        staged_app_dir,
        progress_callback=progress_callback,
    )
    change_plan_path = write_update_plan(change_plan, target_dir=target_dir)
    _emit(progress_callback, "本地安装包已准备完成", 100)
    return {
        "current_version": current_version,
        "update_available": True,
        "update_source": "local",
        "release": {
            "tag_name": package_version,
            "name": package_version,
            "body": "",
            "html_url": "",
        },
        "asset": {
            "name": package_name,
            "browser_download_url": "",
            "size": package_size,
        },
        "zip_path": source_path,
        "staged_app_dir": staged_app_dir,
        "updates_dir": target_dir,
        "change_plan": change_plan,
        "change_plan_path": change_plan_path,
        "change_summary": change_plan["summary"],
    }


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


def _write_fallback_cmd_script(script_path, ps_script_path, log_path):
    content = f"""@echo off
setlocal
set "LOG={_cmd_quote(log_path)}"
title DeepSeek Cowork Update
echo Retrying the differential updater through cmd.exe. >> "%LOG%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "{_cmd_quote(ps_script_path)}"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo Differential updater failed with code %RC%. See log: %LOG%
  pause
)
exit /b %RC%
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(content)


def _write_gui_ps_update_script(script_path, install_dir, staged_app_dir, backup_dir, log_path, plan_path, wait_pids, exe_name, background_install=False):
    ps_wait_pids = "@(" + ", ".join(str(int(pid)) for pid in wait_pids) + ")"
    top_most = "$false" if background_install else "$false"
    window_state = "'Minimized'" if background_install else "'Normal'"
    content = f"""Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$PidsToWait = {ps_wait_pids}
$SourceDir = {_ps_quote(staged_app_dir)}
$InstallDir = {_ps_quote(install_dir)}
$BackupDir = {_ps_quote(backup_dir)}
$ExeName = {_ps_quote(exe_name)}
$LogPath = {_ps_quote(log_path)}
$PlanPath = {_ps_quote(plan_path)}
$RunInBackground = {'$true' if background_install else '$false'}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'DeepSeek Cowork Update'
$form.Width = 620
$form.Height = 420
$form.StartPosition = 'CenterScreen'
$form.TopMost = {top_most}
$form.MinimizeBox = $true
$form.ShowInTaskbar = $true
$form.WindowState = {window_state}

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

function Resolve-SafeRelativePath([string]$Root, [string]$RelativePath) {{
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [System.IO.Path]::IsPathRooted($RelativePath)) {{
        throw "Unsafe update path: $RelativePath"
    }}
    $normalized = $RelativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $normalized))
    $rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd('\\') + '\\'
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {{
        throw "Update path escapes application directory: $RelativePath"
    }}
    return $candidate
}}

function Copy-UpdateFile([string]$SourcePath, [string]$DestinationPath) {{
    $parent = Split-Path -Parent $DestinationPath
    if ($parent) {{ New-Item -ItemType Directory -Force -Path $parent | Out-Null }}
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
}}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({{
    $timer.Stop()
    try {{
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
        Set-Content -Path $LogPath -Value '' -Encoding UTF8
        if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {{
            throw "Update operation plan is missing: $PlanPath"
        }}
        $plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$plan.format_version -ne {UPDATE_PLAN_FORMAT}) {{
            throw "Unsupported update operation plan format"
        }}
        $added = @($plan.added)
        $modified = @($plan.modified)
        $deleted = @($plan.deleted)

        foreach ($pidItem in $PidsToWait) {{
            Add-UpdateLog ('Waiting for process to exit: ' + $pidItem) 10
            while (Get-Process -Id $pidItem -ErrorAction SilentlyContinue) {{
                Start-Sleep -Milliseconds 500
                [System.Windows.Forms.Application]::DoEvents()
            }}
        }}

        Add-UpdateLog ('Backing up affected files: ' + ($modified.Count + $deleted.Count)) 25
        foreach ($item in @($modified) + @($deleted)) {{
            $sourcePath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
            if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {{
                $backupPath = Resolve-SafeRelativePath $BackupDir ([string]$item.path)
                Copy-UpdateFile $sourcePath $backupPath
            }}
        }}

        Add-UpdateLog ('Writing changed files: ' + ($added.Count + $modified.Count)) 55
        foreach ($item in @($added) + @($modified)) {{
            $sourcePath = Resolve-SafeRelativePath $SourceDir ([string]$item.path)
            $destinationPath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
            if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {{
                throw "Staged update file is missing: $($item.path)"
            }}
            $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($sourceHash -ne ([string]$item.sha256).ToLowerInvariant()) {{
                throw "Staged update file hash mismatch: $($item.path)"
            }}
            Copy-UpdateFile $sourcePath $destinationPath
        }}

        Add-UpdateLog ('Deleting obsolete files: ' + $deleted.Count) 70
        foreach ($item in $deleted) {{
            $destinationPath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
            if (Test-Path -LiteralPath $destinationPath -PathType Leaf) {{
                Remove-Item -LiteralPath $destinationPath -Force
            }}
        }}

        Add-UpdateLog 'Verifying changed files...' 82
        foreach ($item in @($added) + @($modified)) {{
            $destinationPath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
            if (-not (Test-Path -LiteralPath $destinationPath -PathType Leaf)) {{
                throw "Installed update file is missing: $($item.path)"
            }}
            $installedHash = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($installedHash -ne ([string]$item.sha256).ToLowerInvariant()) {{
                throw "Installed update file hash mismatch: $($item.path)"
            }}
        }}

        Add-UpdateLog 'Preserved user_data directory.' 86
        Add-UpdateLog 'Starting updated application...' 90
        Start-Process -FilePath (Join-Path $InstallDir $ExeName) -WorkingDirectory $InstallDir -WindowStyle Hidden
        Add-UpdateLog 'Update completed. This window will close automatically.' 100
        Start-Sleep -Seconds 2
        $form.Close()
    }} catch {{
        Add-UpdateLog ("Update failed: " + $_.Exception.Message) 100
        $form.WindowState = 'Normal'
        $form.Show()
        $form.Activate()
        try {{
            Add-UpdateLog 'Attempting differential rollback...' 100
            foreach ($item in $added) {{
                $destinationPath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
                if (Test-Path -LiteralPath $destinationPath -PathType Leaf) {{
                    Remove-Item -LiteralPath $destinationPath -Force
                }}
            }}
            foreach ($item in @($modified) + @($deleted)) {{
                $backupPath = Resolve-SafeRelativePath $BackupDir ([string]$item.path)
                $destinationPath = Resolve-SafeRelativePath $InstallDir ([string]$item.path)
                if (Test-Path -LiteralPath $backupPath -PathType Leaf) {{
                    Copy-UpdateFile $backupPath $destinationPath
                }}
            }}
            Start-Process -FilePath (Join-Path $InstallDir $ExeName) -WorkingDirectory $InstallDir -WindowStyle Hidden
            Add-UpdateLog 'Rolled back affected files and restarted the previous version.' 100
        }} catch {{
            Add-UpdateLog ("Rollback failed: " + $_.Exception.Message) 100
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


def create_windows_update_script(install_dir, staged_app_dir, change_plan_path, current_pid=None, exe_name=APP_EXE_NAME, target_dir=None, extra_wait_pids=None, background_install=False):
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    install_dir = os.path.abspath(install_dir)
    staged_app_dir = os.path.abspath(staged_app_dir)
    if not _is_valid_app_dir(staged_app_dir):
        raise UpdaterError("待安装目录结构无效。")
    change_plan_path = os.path.abspath(change_plan_path)
    if not os.path.isfile(change_plan_path):
        raise UpdaterError("更新操作清单不存在。")
    try:
        with open(change_plan_path, "r", encoding="utf-8") as handle:
            change_plan = json.load(handle)
    except Exception as exc:
        raise UpdaterError(f"读取更新操作清单失败：{exc}") from exc
    if not isinstance(change_plan, dict) or change_plan.get("format_version") != UPDATE_PLAN_FORMAT:
        raise UpdaterError("更新操作清单格式无效。")
    for operation_name in ("added", "modified", "deleted"):
        operations = change_plan.get(operation_name)
        if not isinstance(operations, list):
            raise UpdaterError(f"更新操作清单缺少 {operation_name} 列表。")
        for operation in operations:
            if not isinstance(operation, dict):
                raise UpdaterError("更新操作清单包含无效条目。")
            relative_path = _normalized_relative_path(operation.get("path"))
            if _is_update_excluded(relative_path):
                raise UpdaterError(f"更新操作不能修改用户数据：{relative_path}")
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(target_dir, f"backup-{stamp}")
    log_path = os.path.join(target_dir, "update.log")
    ps_script_path, cmd_script_path = _update_script_paths(target_dir, stamp)
    wait_pids = _normalize_wait_pids(current_pid, extra_wait_pids)
    _write_gui_ps_update_script(
        ps_script_path,
        install_dir,
        staged_app_dir,
        backup_dir,
        log_path,
        change_plan_path,
        wait_pids,
        exe_name,
        background_install=background_install,
    )
    _write_fallback_cmd_script(cmd_script_path, ps_script_path, log_path)
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
