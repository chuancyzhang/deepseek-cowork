import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone

import requests

from .env_utils import get_app_data_dir
from .process_utils import subprocess_kwargs_no_window


BROWSER_SKILL_COMPONENT_ID = "browser-skill"
BROWSER_SKILL_VERSION = "0.1.7"
BROWSER_SKILL_ARCHIVE = "bsk-v0.1.7-x86_64-pc-windows-msvc.zip"
BROWSER_SKILL_SHA256 = "C941BE54D0C0CE56212BF38A512AC9F017A4A74A4760BCD40BBD5399C489CB75"
BROWSER_SKILL_DOWNLOAD_URL = (
    "https://github.com/Tencent/BrowserSkill/releases/download/"
    f"cli-v{BROWSER_SKILL_VERSION}/{BROWSER_SKILL_ARCHIVE}"
)
BROWSER_SKILL_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/"
    "hhcmgoofomhgciiibhipgmgkgnoenaoi"
)
BROWSER_SKILL_MARKER_SCHEMA = 1

_LOG_LOCK = threading.RLock()


def browser_skill_root():
    return os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "components",
        BROWSER_SKILL_COMPONENT_ID,
    )


def browser_skill_executable():
    return os.path.join(browser_skill_root(), "bsk.exe")


def _marker_path(root=None):
    return os.path.join(root or browser_skill_root(), "component.json")


def _diagnostics_path():
    return os.path.join(browser_skill_root(), "diagnostics.json")


def _read_diagnostics_cache():
    try:
        with open(_diagnostics_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_diagnostics_cache(status):
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "state": status.get("state"),
        "state_text": status.get("state_text"),
        "ready": bool(status.get("ready")),
        "health_error": status.get("health_error") or "",
        "diagnostics": status.get("diagnostics") or {},
    }
    with open(_diagnostics_path(), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _log_path():
    path = os.path.join(get_app_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, "browser_skill_runtime.log")


def log_browser_skill_event(stage, **fields):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": str(stage or "event"),
        **{
            str(key): value
            for key, value in fields.items()
            if value is not None
        },
    }
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with _LOG_LOCK:
        with open(_log_path(), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _read_marker():
    try:
        with open(_marker_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _directory_size(path):
    total = 0
    if not os.path.isdir(path):
        return total
    for root, _dirs, filenames in os.walk(path):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                continue
    return total


def _parse_version(text):
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)", str(text or ""))
    return match.group(1) if match else ""


def _run_bsk(args, timeout=20):
    executable = browser_skill_executable()
    completed = subprocess.run(
        [executable, *list(args or [])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout or 20)),
        **subprocess_kwargs_no_window(),
    )
    return {
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def _parse_json_output(stdout, stderr=""):
    for raw in (stdout, stderr):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            pass
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except (ValueError, TypeError):
                continue
    return {}


def _collect_artifacts(payload):
    artifacts = []
    seen = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    isinstance(item, str)
                    and str(key).lower() in {"path", "output", "out", "file"}
                    and item.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                ):
                    path = os.path.abspath(item)
                    if path not in seen:
                        seen.add(path)
                        artifacts.append({"type": "screenshot", "path": path})
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return artifacts


def _doctor_checks(payload):
    checks = []

    def walk(value):
        if isinstance(value, dict):
            lowered = {str(key).lower(): item for key, item in value.items()}
            if any(key in lowered for key in ("status", "state", "ok", "name", "check")):
                checks.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return checks


def _extension_health(payload, combined_text):
    text = str(combined_text or "").lower()
    for check in _doctor_checks(payload):
        label = " ".join(
            str(check.get(key) or "")
            for key in ("name", "check", "label", "message", "hint")
        ).lower()
        if not any(token in label for token in ("extension", "browser", "浏览器", "扩展")):
            continue
        status = str(check.get("status") or check.get("state") or "").strip().lower()
        ok = check.get("ok")
        if ok is True or status in {"ok", "pass", "passed", "healthy", "ready", "connected"}:
            return True
        if ok is False or status in {"fail", "failed", "error", "missing", "disconnected"}:
            return False
    if re.search(r"\b[1-9]\d*\s+browsers?\s+connected\b", text):
        return True
    if "0 browsers connected" in text or "extension connected" in text and "fail" in text:
        return False
    return None


def browser_skill_status(run_diagnostics=False):
    root = browser_skill_root()
    executable = browser_skill_executable()
    marker = _read_marker()
    installed = os.path.isfile(executable)
    result = {
        "id": BROWSER_SKILL_COMPONENT_ID,
        "name": "Tencent BrowserSkill",
        "description": "连接真实登录态的 Chrome 或 Edge，让 AI 读取和操作网页。",
        "installed": installed,
        "healthy": False,
        "ready": False,
        "needs_update": False,
        "needs_repair": False,
        "health_error": "",
        "state": "not_installed",
        "state_text": "未安装",
        "version": "",
        "expected_version": BROWSER_SKILL_VERSION,
        "path": executable if installed else "",
        "size": _directory_size(root),
        "extension_url": BROWSER_SKILL_EXTENSION_URL,
        "diagnostics": {},
    }
    if not installed:
        return result

    try:
        version_result = _run_bsk(["--version"], timeout=8)
    except Exception as exc:
        result.update({
            "needs_repair": True,
            "health_error": f"无法启动 bsk：{exc}",
            "state": "check_failed",
            "state_text": "检查失败",
        })
        return result

    version = _parse_version(version_result["stdout"] or version_result["stderr"])
    result["version"] = version
    marker_valid = (
        marker.get("schema") == BROWSER_SKILL_MARKER_SCHEMA
        and str(marker.get("sha256") or "").upper() == BROWSER_SKILL_SHA256
    )
    if version != BROWSER_SKILL_VERSION:
        result.update({
            "needs_update": True,
            "health_error": (
                f"CLI 版本不兼容：当前 {version or '未知'}，"
                f"需要 {BROWSER_SKILL_VERSION}"
            ),
            "state": "version_mismatch",
            "state_text": "版本不兼容",
        })
        return result
    if not marker_valid:
        result.update({
            "needs_repair": True,
            "health_error": "BrowserSkill 安装标记缺失或校验信息不一致。",
            "state": "check_failed",
            "state_text": "需要修复",
        })
        return result
    if not run_diagnostics:
        cached = _read_diagnostics_cache()
        if cached.get("state") in {"ready", "extension_disconnected", "check_failed"}:
            result.update({
                "healthy": cached.get("state") in {"ready", "extension_disconnected"},
                "ready": bool(cached.get("ready")),
                "health_error": cached.get("health_error") or "",
                "state": cached.get("state"),
                "state_text": cached.get("state_text") or "检查完成",
                "diagnostics": cached.get("diagnostics") or {},
            })
            return result
        result.update({
            "healthy": True,
            "state": "cli_installed",
            "state_text": "CLI 已安装，扩展连接待检查",
        })
        return result

    log_browser_skill_event("doctor", executable=executable)
    try:
        doctor = _run_bsk(["--json", "--quiet", "doctor"], timeout=30)
    except subprocess.TimeoutExpired:
        result.update({
            "health_error": "bsk doctor 检查超时。",
            "state": "check_failed",
            "state_text": "检查失败",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event(
            "error",
            operation="doctor",
            error=result["health_error"],
        )
        return result
    except Exception as exc:
        result.update({
            "health_error": f"bsk doctor 执行失败：{exc}",
            "state": "check_failed",
            "state_text": "检查失败",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event(
            "error",
            operation="doctor",
            error=result["health_error"],
        )
        return result

    payload = _parse_json_output(doctor["stdout"], doctor["stderr"])
    combined = "\n".join([doctor["stdout"], doctor["stderr"]]).strip()
    extension_connected = _extension_health(payload, combined)
    result["diagnostics"] = {
        "returncode": doctor["returncode"],
        "payload": payload,
        "stdout": doctor["stdout"],
        "stderr": doctor["stderr"],
        "extension_connected": extension_connected,
    }
    if extension_connected is True and doctor["returncode"] == 0:
        result.update({
            "healthy": True,
            "ready": True,
            "state": "ready",
            "state_text": "已就绪",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event("doctor_finish", state="ready", returncode=0)
        return result
    if extension_connected is False:
        result.update({
            "healthy": True,
            "health_error": "CLI 已安装，但 Chrome 或 Edge 扩展尚未连接。",
            "state": "extension_disconnected",
            "state_text": "CLI 已安装，扩展未连接",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event(
            "doctor_finish",
            state="extension_disconnected",
            returncode=doctor["returncode"],
        )
        return result
    detail = doctor["stderr"] or doctor["stdout"] or "bsk doctor 未返回可识别的连接状态。"
    result.update({
        "health_error": detail,
        "state": "check_failed",
        "state_text": "检查失败",
    })
    _write_diagnostics_cache(result)
    log_browser_skill_event(
        "error",
        operation="doctor",
        returncode=doctor["returncode"],
        error=detail,
    )
    return result


def _safe_extract_zip(archive_path, target_dir):
    target_abs = os.path.abspath(target_dir)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            candidate = os.path.abspath(os.path.join(target_abs, member.filename))
            if os.path.commonpath([target_abs, candidate]) != target_abs:
                raise RuntimeError("BrowserSkill 压缩包包含不安全路径。")
        archive.extractall(target_dir)


def _replace_component_root(staged_root, target_root):
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


def install_browser_skill(progress_callback=None):
    components_root = os.path.dirname(browser_skill_root())
    os.makedirs(components_root, exist_ok=True)
    staged_root = tempfile.mkdtemp(prefix=".browser-skill-", dir=components_root)
    log_browser_skill_event(
        "download_start",
        version=BROWSER_SKILL_VERSION,
        url=BROWSER_SKILL_DOWNLOAD_URL,
    )
    try:
        archive_path = os.path.join(staged_root, BROWSER_SKILL_ARCHIVE)
        digest = hashlib.sha256()
        if progress_callback:
            progress_callback("正在下载 Tencent BrowserSkill…", 1)
        with requests.get(
            BROWSER_SKILL_DOWNLOAD_URL,
            stream=True,
            timeout=30,
            headers={"User-Agent": "deepseek-cowork-components"},
        ) as response:
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
                        progress_callback(
                            "正在下载 Tencent BrowserSkill…",
                            min(78, int(downloaded * 78 / expected)),
                        )
        actual_sha256 = digest.hexdigest().upper()
        if actual_sha256 != BROWSER_SKILL_SHA256:
            raise RuntimeError(
                "BrowserSkill SHA-256 校验失败："
                f"期望 {BROWSER_SKILL_SHA256}，实际 {actual_sha256}"
            )
        extract_root = os.path.join(staged_root, "extract")
        os.makedirs(extract_root)
        if progress_callback:
            progress_callback("正在校验并解压 BrowserSkill…", 82)
        _safe_extract_zip(archive_path, extract_root)
        candidates = []
        for root, _dirs, filenames in os.walk(extract_root):
            if "bsk.exe" in filenames:
                candidates.append(os.path.join(root, "bsk.exe"))
        if len(candidates) != 1:
            raise RuntimeError("BrowserSkill 发布包中未找到唯一的 bsk.exe。")
        install_root = os.path.join(staged_root, "install")
        os.makedirs(install_root)
        shutil.copy2(candidates[0], os.path.join(install_root, "bsk.exe"))
        completed = subprocess.run(
            [os.path.join(install_root, "bsk.exe"), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **subprocess_kwargs_no_window(),
        )
        detected_version = _parse_version(completed.stdout or completed.stderr)
        if completed.returncode != 0 or detected_version != BROWSER_SKILL_VERSION:
            raise RuntimeError(
                "BrowserSkill CLI 验证失败："
                f"期望 {BROWSER_SKILL_VERSION}，实际 {detected_version or '未知'}"
            )
        marker = {
            "schema": BROWSER_SKILL_MARKER_SCHEMA,
            "id": BROWSER_SKILL_COMPONENT_ID,
            "version": BROWSER_SKILL_VERSION,
            "sha256": BROWSER_SKILL_SHA256,
            "source_url": BROWSER_SKILL_DOWNLOAD_URL,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "verified": True,
        }
        with open(_marker_path(install_root), "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback("正在启用 Tencent BrowserSkill…", 94)
        target_root = browser_skill_root()
        _replace_component_root(install_root, target_root)
        if progress_callback:
            progress_callback("BrowserSkill CLI 已安装，请继续安装并启用浏览器扩展。", 100)
        log_browser_skill_event(
            "download_finish",
            version=BROWSER_SKILL_VERSION,
            sha256=actual_sha256,
            ok=True,
        )
        return browser_skill_status(run_diagnostics=False)
    except Exception as exc:
        log_browser_skill_event(
            "download_finish",
            version=BROWSER_SKILL_VERSION,
            ok=False,
        )
        log_browser_skill_event("error", operation="install", error=str(exc))
        raise
    finally:
        if os.path.isdir(staged_root):
            shutil.rmtree(staged_root, ignore_errors=True)


def uninstall_browser_skill():
    root = browser_skill_root()
    log_browser_skill_event("uninstall", root=root)
    if os.path.isdir(root):
        shutil.rmtree(root)
    return browser_skill_status(run_diagnostics=False)


def _session_id_from_args(args):
    values = list(args or [])
    for index, value in enumerate(values):
        if value == "--session" and index + 1 < len(values):
            return str(values[index + 1]).strip()
    if len(values) >= 3 and values[0] == "session" and values[1] == "stop":
        return str(values[2]).strip()
    return ""


def _stop_session_after_interruption(session_id):
    if not session_id:
        return
    cleanup = _run_bsk(
        ["--json", "--quiet", "session", "stop", session_id],
        timeout=10,
    )
    log_browser_skill_event(
        "session_stop",
        session_id=session_id,
        cleanup=True,
        returncode=cleanup["returncode"],
        error=cleanup["stderr"] if cleanup["returncode"] else "",
    )


def run_browser_skill_cli(args, timeout_seconds=120, abort_check=None):
    status = browser_skill_status(run_diagnostics=False)
    if not status.get("ready"):
        return {
            "status": "incomplete",
            "error": {
                "code": "browser_skill_not_ready",
                "message": (
                    "Tencent BrowserSkill 尚未就绪。请前往"
                    "“设置 → 组件与依赖 → 可选浏览器能力”完成安装和连接检查。"
                ),
                "detail": status.get("health_error") or status.get("state_text"),
            },
            "component": status,
        }
    command_args = [str(item) for item in (args or [])]
    timeout_seconds = max(1, min(int(timeout_seconds or 120), 1800))
    started = time.time()
    process = subprocess.Popen(
        [browser_skill_executable(), "--json", "--quiet", *command_args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_kwargs_no_window(),
    )
    log_browser_skill_event(
        "command",
        command=command_args[0] if command_args else "",
        pid=process.pid,
    )
    timed_out = False
    aborted = False
    while process.poll() is None:
        if callable(abort_check) and abort_check():
            aborted = True
            process.terminate()
            break
        if time.time() - started > timeout_seconds:
            timed_out = True
            process.terminate()
            break
        time.sleep(0.1)
    try:
        stdout, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    payload = _parse_json_output(stdout, stderr)
    returncode = process.returncode if process.returncode is not None else -1
    if aborted or timed_out:
        code = "aborted" if aborted else "timeout"
        message = "BrowserSkill 命令已由用户中止。" if aborted else "BrowserSkill 命令执行超时。"
        session_id = _session_id_from_args(command_args)
        try:
            _stop_session_after_interruption(session_id)
        except Exception as cleanup_exc:
            log_browser_skill_event(
                "error",
                operation="session_cleanup",
                session_id=session_id,
                error=str(cleanup_exc),
            )
        log_browser_skill_event(
            "error",
            operation="command",
            code=code,
            command=command_args[0] if command_args else "",
        )
        return {
            "status": "incomplete",
            "result": payload,
            "artifacts": _collect_artifacts(payload),
            "stdout": (stdout or "").strip(),
            "stderr": (stderr or "").strip(),
            "error": {"code": code, "message": message},
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    if returncode != 0:
        log_browser_skill_event(
            "error",
            operation="command",
            code="bsk_command_failed",
            returncode=returncode,
            command=command_args[0] if command_args else "",
        )
        return {
            "status": "incomplete",
            "result": payload,
            "artifacts": _collect_artifacts(payload),
            "stdout": (stdout or "").strip(),
            "stderr": (stderr or "").strip(),
            "error": {
                "code": "bsk_command_failed",
                "message": (stderr or stdout or "BrowserSkill 命令执行失败。").strip(),
                "exit_code": returncode,
            },
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    return {
        "status": "completed",
        "result": payload if payload not in ({}, None) else (stdout or "").strip(),
        "artifacts": _collect_artifacts(payload),
        "stderr": (stderr or "").strip(),
        "error": None,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
