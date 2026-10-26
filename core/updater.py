import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests

from core.app_version import APP_VERSION, compare_versions, is_newer_version
from core.process_utils import subprocess_kwargs_no_window, popen_external_program
from core.env_utils import get_app_data_dir
from core.update_manifest import validate_manifest

PUBLIC_UPDATE_BASE_URL = ""
GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/chuancyzhang/deepseek-cowork/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/chuancyzhang/deepseek-cowork/releases"
GITHUB_MANIFEST_URL = GITHUB_RELEASES_URL + "/latest/download/latest.json"
PUBLIC_UPDATE_PATH = "/updates/windows-x64"
_github_api_retry_at = 0.0
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


class UpdateCancelled(UpdaterError):
    pass


class UpdateNetworkError(UpdaterError):
    """Only transient transport failures spend the bounded retry budget."""
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class UpdateSourceUnavailable(UpdateNetworkError):
    """The public mirror cannot serve this request; GitHub may be tried."""


def _public_source_error(exc, message):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    detail = f"HTTP {status}" if status is not None else type(exc).__name__
    return UpdateSourceUnavailable(f"{message}（{detail}）。")


def check_cancel(cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled("已取消本次更新。")


def _network_error(exc, message):
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    if status in {403, 429} and (status == 429 or headers.get("x-ratelimit-remaining") == "0"
                                or headers.get("retry-after") or "rate limit" in str(getattr(response, "text", "")).lower()):
        waits = [60.0]
        for value, offset in ((headers.get("retry-after"), 0), (headers.get("x-ratelimit-reset"), time.time())):
            try:
                wait = float(value) - offset
                if math.isfinite(wait):
                    waits.append(wait)
            except (TypeError, ValueError):
                pass
        delay = max(waits)
        return UpdateNetworkError(f"更新服务请求过于频繁，将在 {int(delay) + 1} 秒后重试。", delay)
    retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout)) or status in {408, 429, 500, 502, 503, 504}
    return (UpdateNetworkError if retryable else UpdaterError)(f"{message}：{exc}")


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    asset_id: str = ""
    digest: str = ""

    def as_dict(self):
        return {
            "name": self.name,
            "browser_download_url": self.download_url,
            "size": self.size,
            "id": self.asset_id,
            "digest": self.digest,
        }


def updates_dir():
    path = os.path.join(get_app_data_dir(), UPDATE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def community_source_config():
    base = PUBLIC_UPDATE_BASE_URL.strip().rstrip("/")
    if base:
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise UpdaterError("公共更新地址必须是不含凭据、查询参数或片段的 HTTPS 地址。")
        try:
            parsed.port
        except ValueError:
            raise UpdaterError("公共更新地址端口无效。") from None
    return {"public_base_url": base, "github_releases_url": GITHUB_RELEASES_URL, "manifest_schema": 1}


def github_asset_url(version, filename):
    # Both arguments originate from the validated static manifest.
    return f"{GITHUB_RELEASES_URL}/download/v{version.lstrip('vV')}/{filename}"


def _fetch_github_api_release(timeout):
    global _github_api_retry_at
    remaining = _github_api_retry_at - time.monotonic()
    if remaining > 0:
        raise UpdateNetworkError(f"GitHub 请求过于频繁，将在 {int(remaining) + 1} 秒后重试。", remaining)
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
        error = _network_error(exc, "无法连接 GitHub 更新服务")
        if isinstance(error, UpdateNetworkError) and error.retry_after:
            _github_api_retry_at = time.monotonic() + error.retry_after
        raise error from exc
    if not isinstance(data, dict):
        raise UpdaterError("GitHub Releases 返回格式无效。")
    if data.get("draft") or data.get("prerelease"):
        raise UpdaterError("最新 Release 不是正式版本。")
    return {**data, "source_name": "github"}


def fetch_latest_release(timeout=12, *, source=None, source_config=None):
    config = source_config if source_config is not None else community_source_config()
    source = source or ("public" if config["public_base_url"] else "github")
    if source not in {"public", "github"}:
        raise UpdaterError("未知更新来源。")
    base = config["public_base_url"] + PUBLIC_UPDATE_PATH
    url = base + "/latest.json" if source == "public" else GITHUB_MANIFEST_URL
    try:
        with requests.get(url, headers={"User-Agent": "deepseek-cowork-updater", "Cache-Control": "no-cache"}, timeout=timeout) as response:
            # Compatibility is limited to a genuinely missing attachment.
            if source == "github" and response.status_code == 404:
                return _fetch_github_api_release(timeout)
            response.raise_for_status()
            try:
                manifest = validate_manifest(response.json())
            except (ValueError, TypeError) as exc:
                raise UpdaterError(str(exc) if isinstance(exc, ValueError) else "更新清单格式无效。") from exc
    except UpdaterError:
        raise
    except requests.RequestException as exc:
        if source == "public":
            raise _public_source_error(exc, "公共更新源暂时不可用") from exc
        raise _network_error(exc, "无法读取 GitHub 更新清单") from exc
    version, filename = manifest["version"], manifest["filename"]
    asset = ReleaseAsset(filename, base + "/" + filename if source == "public" else github_asset_url(version, filename),
                         manifest["size"], version, "sha256:" + manifest["sha256"])
    return {"tag_name": version, "body": manifest["notes"], "html_url": GITHUB_RELEASES_URL + "/tag/v" + version,
            "assets": [asset.as_dict()], "source_name": source}


def update_connection(install_dir):
    """The installed distribution chooses the channel, not a past account login."""
    from core.enterprise_connection import read_connection, validate_connection
    from core.enterprise_client import EnterpriseStore
    import sqlite3
    try:
        packaged = read_connection(install_dir)
        if packaged is None:
            return None
        return validate_connection(EnterpriseStore().read().get("connection") or packaged)
    except (ValueError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise UpdaterError("企业连接文件或本机状态无法读取，请在企业账号中检查后再更新。") from exc


def validate_online_package(staged_app_dir, connection):
    from core.enterprise_connection import read_connection, check_update_connection, EnterpriseConfigurationError
    try:
        candidate = read_connection(staged_app_dir)
        check_update_connection(connection, candidate)
        if connection is None and candidate is not None:
            raise UpdaterError("社区更新服务返回了企业安装包，请检查发布文件。")
    except EnterpriseConfigurationError as exc:
        raise UpdaterError(str(exc)) from None


@contextmanager
def _update_response(url, *, connection=None, **kwargs):
    if connection is None:
        with requests.get(url, **kwargs) as response:
            yield response
        return
    base = connection["platform_url"]
    if not url.startswith(base + "/api/v1/"):
        raise UpdaterError("企业更新地址不属于当前平台。")
    with requests.Session() as session:
        # Windows system proxies may intercept local development deployments.
        session.trust_env = urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}
        with session.get(url, allow_redirects=False, **kwargs) as response:
            if 300 <= response.status_code < 400:
                raise UpdaterError("企业更新服务发生重定向，请检查企业平台地址。")
            if url.endswith("/download") and response.status_code in {404, 410}:
                raise UpdaterError("企业安装包已撤回或不可用，请重新检查更新或联系管理员。")
            yield response


def fetch_enterprise_release(connection, timeout=12):
    base = connection["platform_url"]
    def read(path):
        try:
            with _update_response(base + path, connection=connection, timeout=timeout) as response:
                response.raise_for_status()
                data = response.json()
        except UpdaterError:
            raise
        except Exception as exc:
            raise _network_error(exc, "无法读取企业更新服务") from exc
        if not isinstance(data, dict):
            raise UpdaterError("企业更新服务返回格式无效。")
        return data
    info = read("/api/v1/platform-info")
    if info.get("platform_id") != connection["platform_id"]:
        raise UpdaterError("企业更新服务的平台标识与当前连接不一致。")
    if not isinstance(info.get("supported_api_versions"), list) or 1 not in info["supported_api_versions"]:
        raise UpdaterError("企业更新服务协议不兼容，请联系管理员。")
    response = read("/api/v1/client-releases/recommended?target_os=windows&arch=x64")
    if "release" not in response:
        raise UpdaterError("企业更新服务缺少推荐版本信息。")
    release = response["release"]
    if release is None:
        return None
    if (not isinstance(release, dict) or release.get("state") != "published"
            or release.get("recommended") is not True or release.get("target_os") != "windows"
            or release.get("arch") != "x64" or not re.fullmatch(r"\d+\.\d+\.\d+", str(release.get("app_version", "")))
            or not isinstance(release.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", release["id"])
            or not re.fullmatch(r"[a-fA-F0-9]{64}", str(release.get("sha256", "")))
            or type(release.get("bytes")) is not int or release["bytes"] <= 0):
        raise UpdaterError("企业推荐版本的发布状态或安装包信息无效。")
    version = release["app_version"]
    asset = ReleaseAsset(f"deepseek-cowork-v{version}.zip",
                         base + f"/api/v1/client-releases/{release['id']}/download",
                         release["bytes"], release["id"], "sha256:" + release["sha256"].lower())
    return {"tag_name": version, "body": release.get("notes") or "", "html_url": base + "/downloads",
            "assets": [asset.as_dict()]}


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
                asset_id=str(asset.get("id") or ""),
                digest=str(asset.get("digest") or ""),
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

    from core.enterprise_connection import read_connection, check_update_connection, EnterpriseConfigurationError
    try:
        current = update_connection(install_dir)
        check_update_connection(current, read_connection(staged_app_dir))
    except EnterpriseConfigurationError as exc:
        raise UpdaterError(str(exc)) from None

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
            operation["before_sha256"] = None
            added.append(operation)
            continue
        if current_info["size"] == staged_info["size"] and _hash_file(current_info["full_path"]) == staged_hash:
            unchanged += 1
            continue
        operation["before_sha256"] = _hash_file(current_info["full_path"])
        modified.append(operation)

    deleted = [
        {"path": relative_path, "before_sha256": _hash_file(current_files[relative_path]["full_path"])}
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
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
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


def same_release_asset(left, right):
    """Mirrors may transport the same verified bytes under different URLs."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    digest = str(left.get("digest") or "")
    if re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest) and type(left.get("size")) is int and left["size"] > 0:
        return all(left.get(key) == right.get(key) for key in ("name", "size", "digest"))
    return left == right


def is_cached_asset_valid(asset, zip_path):
    if not os.path.isfile(zip_path):
        return False
    expected_size = int((asset.get("size") if isinstance(asset, dict) else getattr(asset, "size", 0)) or 0)
    if expected_size and os.path.getsize(zip_path) != expected_size:
        return False
    digest = str((asset.get("digest") if isinstance(asset, dict) else getattr(asset, "digest", "")) or "")
    if digest.startswith("sha256:") and _hash_file(zip_path) != digest[7:]:
        return False
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def cleanup_update_artifacts(target_dir=None, keep_paths=None, progress_callback=None):
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    keep = {os.path.abspath(path) for path in (keep_paths or []) if path}
    removed = 0
    patterns = (
        re.compile(r"^deepseek-cowork.*\.zip$", re.IGNORECASE),
        re.compile(r"^deepseek-cowork.*\.zip\.download$", re.IGNORECASE),
    )
    for name in os.listdir(target_dir):
        path = os.path.join(target_dir, name)
        abs_path = os.path.abspath(path)
        if abs_path in keep:
            continue
        if not any(pattern.match(name) for pattern in patterns) or not os.path.isfile(path):
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UpdaterError(f"无法清理旧安装包：{name}：{exc}") from exc
    _emit(progress_callback, f"已清理历史更新痕迹：{removed} 项", None)
    return removed


def download_asset(asset, target_dir=None, progress_callback=None, timeout=30, cancel_event=None, connection=None, public_source=False):
    if isinstance(asset, dict):
        asset = ReleaseAsset(
            name=str(asset.get("name") or ""),
            download_url=str(asset.get("browser_download_url") or ""),
            size=int(asset.get("size") or 0),
            asset_id=str(asset.get("id") or ""),
            digest=str(asset.get("digest") or ""),
        )
    if not asset.download_url:
        raise UpdaterError("Release 资源缺少下载地址。")
    target_dir = target_dir or updates_dir()
    os.makedirs(target_dir, exist_ok=True)
    zip_path = expected_asset_path(asset, target_dir=target_dir)
    check_cancel(cancel_event)
    if is_cached_asset_valid(asset, zip_path):
        _emit(progress_callback, f"已找到本地安装包，跳过下载：{zip_path}", 100)
        return zip_path
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    temp_path = zip_path + ".download"
    _emit(progress_callback, f"正在下载 {asset.name}", 0)
    try:
        with _update_response(asset.download_url, connection=connection, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            expected = asset.size or int(response.headers.get("content-length") or 0)
            downloaded = 0
            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    check_cancel(cancel_event)
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
        check_cancel(cancel_event)
        if not is_cached_asset_valid(asset, temp_path):
            raise UpdaterError("安装包校验失败，请重新下载。")
        os.replace(temp_path, zip_path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        if isinstance(exc, UpdaterError):
            raise
        if public_source and isinstance(exc, requests.RequestException):
            raise _public_source_error(exc, "公共更新源暂时无法下载安装包") from exc
        raise _network_error(exc, "下载更新包失败") from exc
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
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    extract_dir = os.path.join(target_dir, f"staged-{stamp}")
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
    if compare_versions(package_version, current_version) < 0:
        raise UpdaterError(
            f"安装包版本 {package_version} 低于当前版本 {current_version}，不能降级安装。"
        )

    package_size = os.path.getsize(source_path)
    _emit(progress_callback, f"正在验证本地安装包 {package_name}", 5)
    target_dir = updates_dir()
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



def create_windows_update_script(install_dir, staged_app_dir, change_plan_path, current_pid=None, exe_name=APP_EXE_NAME, target_dir=None, extra_wait_pids=None, theme=None, version=""):
    from core.update_installer import write_installer
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    install_dir = os.path.abspath(install_dir)
    staged_app_dir = os.path.abspath(staged_app_dir)
    if not _is_valid_app_dir(staged_app_dir):
        raise UpdaterError("待安装目录结构无效。")
    with open(change_plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)
    if plan.get("format_version") != UPDATE_PLAN_FORMAT:
        raise UpdaterError("更新操作清单格式无效。")
    for group in ("added", "modified", "deleted"):
        if not isinstance(plan.get(group), list):
            raise UpdaterError("更新操作清单格式无效。")
        for operation in plan[group]:
            relative = _normalized_relative_path(operation.get("path"))
            if _is_update_excluded(relative):
                raise UpdaterError("更新操作不能修改用户数据。")
    pids = list(dict.fromkeys(int(pid) for pid in [current_pid or os.getpid(), *(extra_wait_pids or [])] if int(pid) > 0))
    return write_installer(install_dir, staged_app_dir, os.path.abspath(change_plan_path), target_dir or updates_dir(), pids, exe_name, theme, version)


def launch_windows_update_script(script_path):
    if sys.platform != "win32":
        raise UpdaterError("自动安装更新仅支持 Windows。")
    script_path = os.path.abspath(script_path)
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    with open(os.path.join(os.path.dirname(script_path), "update-launch.log"), "a", encoding="utf-8") as handle:
        return popen_external_program(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", script_path],
            close_fds=True, cwd=os.path.dirname(script_path), stdout=handle, stderr=handle,
            **subprocess_kwargs_no_window(),
        )
