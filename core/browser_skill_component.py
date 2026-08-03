import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

from .env_utils import get_app_data_dir, get_resource_dir
from .process_utils import popen_external_program, subprocess_kwargs_no_window


BROWSER_SKILL_COMPONENT_ID = "browser-skill"
BROWSER_SKILL_VERSION = "0.1.8"
BROWSER_SKILL_ARCHIVE = "bsk-v0.1.8-x86_64-pc-windows-msvc.zip"
BROWSER_SKILL_SHA256 = "A5FEF16F7247F5BA6AE2ED032DF8C3704F124291884FEA40C19E6492AD442E13"
BROWSER_SKILL_DOWNLOAD_URL = (
    "https://github.com/Tencent/BrowserSkill/releases/download/"
    f"cli-v{BROWSER_SKILL_VERSION}/{BROWSER_SKILL_ARCHIVE}"
)
BROWSER_SKILL_EXTENSION_VERSION = "0.1.4"
BROWSER_SKILL_EXTENSION_ARCHIVE = "browser-skill-extension-v0.1.4-chrome.zip"
BROWSER_SKILL_EXTENSION_SHA256 = "0C7A0B371CC15AC42AF155A55ED0C1BDAF257916F1ACC71C0C2BC56AAE366C3E"
BROWSER_SKILL_EXTENSION_DOWNLOAD_URL = (
    "https://github.com/Tencent/BrowserSkill/releases/download/"
    f"ext-v{BROWSER_SKILL_EXTENSION_VERSION}/{BROWSER_SKILL_EXTENSION_ARCHIVE}"
)
BROWSER_SKILL_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/"
    "hhcmgoofomhgciiibhipgmgkgnoenaoi"
)
BROWSER_SKILL_MARKER_SCHEMA = 2
BROWSER_SKILL_BUNDLE_SCHEMA = 1
BROWSER_SKILL_PROBE_TIMEOUT_SECONDS = 45

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


def browser_skill_bundle_root():
    return os.path.join(get_resource_dir(), "resources", "browser_skill")


def browser_skill_extension_component_root():
    return os.path.join(
        get_app_data_dir(),
        "runtime_sandbox",
        "v1",
        "components",
        "browser-skill-extension",
    )


def browser_skill_extension_path():
    return os.path.join(browser_skill_extension_component_root(), "extension")


def _extension_marker_path(root=None):
    return os.path.join(root or browser_skill_extension_component_root(), "component.json")


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


def _read_json_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON 根节点必须是对象：{path}")
    return payload


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _browser_skill_bundle_manifest():
    manifest_path = os.path.join(browser_skill_bundle_root(), "bundle.json")
    license_path = os.path.join(browser_skill_bundle_root(), "LICENSE.txt")
    if not os.path.isfile(manifest_path):
        raise RuntimeError(f"随包 BrowserSkill 清单缺失：{manifest_path}")
    if not os.path.isfile(license_path):
        raise RuntimeError(f"随包 BrowserSkill MIT 许可证缺失：{license_path}")
    try:
        manifest = _read_json_file(manifest_path)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise RuntimeError(f"随包 BrowserSkill 清单无效：{exc}") from exc
    if (
        manifest.get("schema") != BROWSER_SKILL_BUNDLE_SCHEMA
        or manifest.get("component_id") != BROWSER_SKILL_COMPONENT_ID
        or manifest.get("license") != "LICENSE.txt"
    ):
        raise RuntimeError("随包 BrowserSkill 清单版本或组件标识不受支持。")
    try:
        with open(license_path, "r", encoding="utf-8") as handle:
            license_text = handle.read()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"随包 BrowserSkill MIT 许可证无法读取：{exc}") from exc
    if "MIT License" not in license_text or "Copyright (c) 2026 Tencent" not in license_text:
        raise RuntimeError("随包 BrowserSkill MIT 许可证内容不匹配。")
    return manifest


def _bundled_artifact_status(kind):
    expected = {
        "cli": {
            "version": BROWSER_SKILL_VERSION,
            "archive": BROWSER_SKILL_ARCHIVE,
            "sha256": BROWSER_SKILL_SHA256,
            "url": BROWSER_SKILL_DOWNLOAD_URL,
        },
        "extension": {
            "version": BROWSER_SKILL_EXTENSION_VERSION,
            "archive": BROWSER_SKILL_EXTENSION_ARCHIVE,
            "sha256": BROWSER_SKILL_EXTENSION_SHA256,
            "url": BROWSER_SKILL_EXTENSION_DOWNLOAD_URL,
        },
    }.get(str(kind or ""))
    if expected is None:
        raise ValueError(f"未知 BrowserSkill 随包制品：{kind}")
    result = {
        "kind": kind,
        "available": False,
        "version": expected["version"],
        "archive": expected["archive"],
        "sha256": expected["sha256"],
        "path": "",
        "error": "",
    }
    try:
        manifest = _browser_skill_bundle_manifest()
        declared = manifest.get(kind) or {}
        mismatches = [
            key
            for key in ("version", "archive", "sha256", "url")
            if str(declared.get(key) or "").strip().upper()
            != str(expected[key]).strip().upper()
        ]
        if mismatches:
            raise RuntimeError(
                f"随包 BrowserSkill {kind} 清单与固定版本不一致："
                + "、".join(mismatches)
            )
        artifact_path = os.path.join(
            browser_skill_bundle_root(),
            "artifacts",
            expected["archive"],
        )
        result["path"] = artifact_path
        if not os.path.isfile(artifact_path):
            raise RuntimeError(f"随包 BrowserSkill {kind} 制品缺失：{artifact_path}")
        actual_sha256 = _file_sha256(artifact_path)
        result["actual_sha256"] = actual_sha256
        if actual_sha256 != expected["sha256"]:
            raise RuntimeError(
                f"随包 BrowserSkill {kind} SHA-256 校验失败："
                f"期望 {expected['sha256']}，实际 {actual_sha256}"
            )
        result["available"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _browser_skill_bundle_status():
    cli = _bundled_artifact_status("cli")
    extension = _bundled_artifact_status("extension")
    errors = [item["error"] for item in (cli, extension) if item.get("error")]
    return {
        "ready": bool(cli.get("available") and extension.get("available")),
        "error": "；".join(errors),
        "cli": cli,
        "extension": extension,
    }


def _extension_preparation_status():
    extension_path = browser_skill_extension_path()
    marker_path = _extension_marker_path()
    result = {
        "prepared": False,
        "version": "",
        "path": extension_path if os.path.isdir(extension_path) else "",
        "error": "",
    }
    if not os.path.isdir(extension_path):
        return result
    try:
        marker = _read_json_file(marker_path)
        manifest = _read_json_file(os.path.join(extension_path, "manifest.json"))
        version = str(manifest.get("version") or "").strip()
        result["version"] = version
        if (
            marker.get("schema") != BROWSER_SKILL_MARKER_SCHEMA
            or marker.get("id") != "browser-skill-extension"
            or str(marker.get("archive_sha256") or "").upper()
            != BROWSER_SKILL_EXTENSION_SHA256
            or version != BROWSER_SKILL_EXTENSION_VERSION
            or int(manifest.get("manifest_version") or 0) != 3
        ):
            raise RuntimeError("已准备的 BrowserSkill 扩展版本或校验标记不一致。")
        result["prepared"] = True
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result["error"] = str(exc)
    return result


def _registry_browser_paths(executable_name):
    if os.name != "nt" or winreg is None:
        return []
    paths = []
    key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}"
    access_modes = [getattr(winreg, "KEY_READ", 0)]
    for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, flag_name, 0)
        if flag:
            access_modes.append(getattr(winreg, "KEY_READ", 0) | flag)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in access_modes:
            try:
                with winreg.OpenKey(hive, key_path, 0, access) as key:
                    value, _value_type = winreg.QueryValueEx(key, "")
                if value:
                    paths.append(str(value).strip().strip('"'))
            except OSError:
                continue
    return paths


def _browser_candidate_paths(browser_id):
    environment = os.environ
    program_files = [
        environment.get("PROGRAMFILES"),
        environment.get("PROGRAMFILES(X86)"),
        environment.get("ProgramW6432"),
    ]
    local_app_data = environment.get("LOCALAPPDATA")
    if browser_id == "chrome":
        executable_name = "chrome.exe"
        suffix = os.path.join("Google", "Chrome", "Application", executable_name)
    elif browser_id == "edge":
        executable_name = "msedge.exe"
        suffix = os.path.join("Microsoft", "Edge", "Application", executable_name)
    else:
        return []
    candidates = [shutil.which(executable_name)]
    candidates.extend(_registry_browser_paths(executable_name))
    candidates.extend(
        os.path.join(root, suffix)
        for root in [local_app_data, *program_files]
        if root
    )
    return candidates


def browser_skill_browser_candidates():
    browsers = []
    definitions = (
        ("chrome", "Google Chrome", "chrome://extensions/"),
        ("edge", "Microsoft Edge", "edge://extensions/"),
    )
    for browser_id, name, extensions_url in definitions:
        seen = set()
        executable = ""
        for candidate in _browser_candidate_paths(browser_id):
            normalized = os.path.abspath(str(candidate or "").strip().strip('"'))
            key = os.path.normcase(normalized)
            if not candidate or key in seen:
                continue
            seen.add(key)
            if os.path.isfile(normalized):
                executable = normalized
                break
        if executable:
            browsers.append({
                "id": browser_id,
                "name": name,
                "path": executable,
                "extensions_url": extensions_url,
            })
    return browsers


def launch_browser_skill_extension_manager(browser_id):
    browser_id = str(browser_id or "").strip().lower()
    supported = {"chrome": "Google Chrome", "edge": "Microsoft Edge"}
    if browser_id not in supported:
        raise RuntimeError("请选择 Google Chrome 或 Microsoft Edge。")
    candidates = {
        item["id"]: item
        for item in browser_skill_browser_candidates()
    }
    browser = candidates.get(browser_id)
    if browser is None:
        available = [item["name"] for item in candidates.values()]
        suggestion = f"，可改选 {'、'.join(available)}" if available else ""
        raise RuntimeError(f"未找到 {supported[browser_id]}{suggestion}。")
    log_browser_skill_event(
        "browser_launch_start",
        browser=browser_id,
        executable=browser["path"],
        url=browser["extensions_url"],
    )
    try:
        process = popen_external_program(
            [browser["path"], browser["extensions_url"]],
            **subprocess_kwargs_no_window(),
        )
    except Exception as exc:
        available = [
            item["name"]
            for item in candidates.values()
            if item["id"] != browser_id
        ]
        suggestion = f" 请明确改选 {'、'.join(available)} 后重试。" if available else ""
        error = f"无法启动 {browser['name']}：{exc}。{suggestion}".strip()
        log_browser_skill_event(
            "browser_launch_error",
            browser=browser_id,
            error=error,
        )
        raise RuntimeError(error) from exc
    log_browser_skill_event(
        "browser_launch_finish",
        browser=browser_id,
        pid=getattr(process, "pid", None),
    )
    return {
        "ok": True,
        "browser": browser,
        "pid": getattr(process, "pid", None),
    }


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


def _run_bsk_executable(executable, args, timeout=20):
    process = popen_external_program(
        [executable, *list(args or [])],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_kwargs_no_window(),
    )
    timeout = max(1, int(timeout or 20))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            process.args,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    return {
        "returncode": process.returncode,
        "stdout": (stdout or "").strip(),
        "stderr": (stderr or "").strip(),
    }


def _run_bsk(args, timeout=20):
    return _run_bsk_executable(browser_skill_executable(), args, timeout)


def _wait_process_while_monitoring(process, timeout_seconds, abort_check=None):
    """Monitor a process whose output is redirected to non-blocking files."""
    started = time.monotonic()
    aborted = False
    timed_out = False
    while process.poll() is None:
        if callable(abort_check) and abort_check():
            aborted = True
            break
        if time.monotonic() - started > timeout_seconds:
            timed_out = True
            break
        time.sleep(0.1)
    if aborted or timed_out:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            process.wait(timeout=3)
    return {
        "aborted": aborted,
        "timed_out": timed_out,
    }


def _read_redirected_output(handle):
    handle.flush()
    handle.seek(0)
    return handle.read().decode("utf-8", errors="replace")


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


_DOCTOR_CHECK_NAMES = {
    "bsk home writable",
    "agent skill up to date",
    "daemon running",
    "protocol compatible",
    "extension connected",
    "browser protocol compatible",
}


def _structured_doctor_checks(payload):
    checks = {}
    for check in _doctor_checks(payload):
        name = str(check.get("name") or check.get("check") or "").strip().lower()
        if name in _DOCTOR_CHECK_NAMES:
            checks[name] = check
    return checks


def _doctor_check_ok(check):
    if not isinstance(check, dict):
        return None
    if check.get("ok") is True:
        return True
    if check.get("ok") is False:
        return False
    status = str(check.get("status") or check.get("state") or "").strip().lower()
    if status in {"ok", "pass", "passed", "healthy", "ready", "connected"}:
        return True
    if status in {"fail", "failed", "error", "missing", "disconnected"}:
        return False
    return None


def _doctor_check_detail(check):
    if not isinstance(check, dict):
        return ""
    return "；".join(
        str(check.get(key) or "").strip()
        for key in ("detail", "message", "hint")
        if str(check.get(key) or "").strip()
    )


def _classify_doctor_result(payload, combined_text):
    checks = _structured_doctor_checks(payload)
    local_failures = []
    for name in ("bsk home writable", "daemon running", "protocol compatible"):
        check = checks.get(name)
        if _doctor_check_ok(check) is False:
            local_failures.append((name, check))
    if local_failures:
        details = [
            _doctor_check_detail(check) or name
            for name, check in local_failures
        ]
        return {
            "kind": "cli_daemon_failed",
            "detail": "；".join(details),
            "checks": checks,
        }

    extension_check = checks.get("extension connected")
    extension_ok = _doctor_check_ok(extension_check)
    if extension_ok is False:
        return {
            "kind": "extension_disconnected",
            "detail": _doctor_check_detail(extension_check) or "0 browsers connected",
            "checks": checks,
        }

    browser_protocol_check = checks.get("browser protocol compatible")
    if extension_ok is True and _doctor_check_ok(browser_protocol_check) is False:
        return {
            "kind": "extension_incompatible",
            "detail": (
                _doctor_check_detail(browser_protocol_check)
                or "浏览器扩展协议与当前 CLI 不兼容。"
            ),
            "checks": checks,
        }
    if extension_ok is True:
        return {"kind": "connected", "detail": "", "checks": checks}

    legacy_extension_ok = _extension_health(payload, combined_text)
    if legacy_extension_ok is True:
        return {"kind": "connected", "detail": "", "checks": checks}
    if legacy_extension_ok is False:
        return {
            "kind": "extension_disconnected",
            "detail": "Chrome 或 Edge 扩展尚未连接。",
            "checks": checks,
        }
    return {
        "kind": "unknown",
        "detail": str(combined_text or "").strip(),
        "checks": checks,
    }


def _session_id_from_payload(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("session_id", "session", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def browser_skill_execution_probe(timeout_seconds=BROWSER_SKILL_PROBE_TIMEOUT_SECONDS):
    """Verify the extension can execute a real, privacy-scoped tab command."""
    started = time.monotonic()
    session_id = ""
    cleanup = {
        "attempted": False,
        "session_id": "",
        "stopped": False,
        "exit_code": None,
        "error": "",
    }
    probe_result = None
    log_browser_skill_event("probe_start")
    try:
        start_result = _run_bsk(
            ["--json", "--quiet", "session", "start"],
            timeout=min(15, timeout_seconds),
        )
        start_payload = _parse_json_output(
            start_result["stdout"],
            start_result["stderr"],
        )
        session_id = _session_id_from_payload(start_payload)
        if start_result["returncode"] != 0 or not session_id:
            raise RuntimeError(
                start_result["stderr"]
                or start_result["stdout"]
                or "BrowserSkill 未返回临时会话 ID。"
            )
        list_result = _run_bsk(
            [
                "--json",
                "--quiet",
                "tab",
                "list",
                "--session",
                session_id,
                "--scope",
                "agent",
            ],
            timeout=timeout_seconds,
        )
        list_payload = _parse_json_output(
            list_result["stdout"],
            list_result["stderr"],
        )
        if list_result["returncode"] != 0:
            raise RuntimeError(
                list_result["stderr"]
                or list_result["stdout"]
                or "BrowserSkill 执行通道检查失败。"
            )
        tabs = list_payload.get("tabs") if isinstance(list_payload, dict) else None
        if not isinstance(tabs, list):
            raise RuntimeError("BrowserSkill tab list 未返回可识别的 tabs 数组。")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        probe_result = {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "tab_count": len(tabs),
            "error": "",
        }
        return probe_result
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        error = f"浏览器执行通道在 {elapsed_ms}ms 内未响应。"
        probe_result = {
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "tab_count": 0,
            "error": error,
            "code": "execution_unresponsive",
        }
        return probe_result
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        error = str(exc)
        probe_result = {
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "tab_count": 0,
            "error": error,
            "code": "execution_unresponsive",
        }
        return probe_result
    finally:
        if session_id:
            try:
                cleanup = _stop_session_after_interruption(session_id)
            except Exception as cleanup_exc:
                cleanup = {
                    "attempted": True,
                    "session_id": session_id,
                    "stopped": False,
                    "exit_code": None,
                    "error": str(cleanup_exc),
                }
                log_browser_skill_event(
                    "error",
                    operation="probe_cleanup",
                    session_id=session_id,
                    error=str(cleanup_exc),
                )
            if probe_result is not None:
                probe_result["session_cleanup"] = cleanup
                if not cleanup.get("stopped"):
                    previous_error = str(probe_result.get("error") or "").strip()
                    cleanup_error = (
                        cleanup.get("error")
                        or "临时 BrowserSkill 会话未能清理。"
                    )
                    probe_result.update({
                        "ok": False,
                        "code": "probe_cleanup_failed",
                        "error": "；".join(
                            item
                            for item in (previous_error, cleanup_error)
                            if item
                        ),
                    })
            log_browser_skill_event(
                "session_cleanup",
                source="probe",
                **cleanup,
            )
        if probe_result is not None:
            log_browser_skill_event(
                "probe_finish",
                ok=bool(probe_result.get("ok")),
                elapsed_ms=probe_result.get("elapsed_ms"),
                tab_count=probe_result.get("tab_count"),
                error=probe_result.get("error") or "",
                cleanup_stopped=(
                    (probe_result.get("session_cleanup") or {}).get("stopped")
                ),
            )


def browser_skill_status(run_diagnostics=False):
    root = browser_skill_root()
    executable = browser_skill_executable()
    marker = _read_marker()
    installed = os.path.isfile(executable)
    bundle = _browser_skill_bundle_status()
    extension = _extension_preparation_status()
    browsers = browser_skill_browser_candidates()
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
        "bundle_ready": bundle["ready"],
        "bundle_error": bundle["error"],
        "bundled_cli_available": bool(bundle["cli"].get("available")),
        "bundled_extension_available": bool(bundle["extension"].get("available")),
        "expected_extension_version": BROWSER_SKILL_EXTENSION_VERSION,
        "extension_prepared": extension["prepared"],
        "extension_prepared_version": extension["version"],
        "extension_path": extension["path"],
        "extension_prepare_error": extension["error"],
        "available_browsers": browsers,
        "protocol_incompatible": False,
        "extension_url": BROWSER_SKILL_EXTENSION_URL,
        "diagnostics": {},
    }
    if not installed:
        if not bundle["cli"].get("available"):
            result.update({
                "needs_repair": True,
                "health_error": bundle["cli"].get("error") or bundle["error"],
                "state": "bundle_unavailable",
                "state_text": "随包安装文件不可用",
            })
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

    if version_result.get("returncode") != 0:
        result.update({
            "needs_repair": True,
            "health_error": (
                version_result.get("stderr")
                or version_result.get("stdout")
                or "bsk --version 执行失败。"
            ),
            "state": "check_failed",
            "state_text": "检查失败",
        })
        return result

    version = _parse_version(version_result["stdout"] or version_result["stderr"])
    result["version"] = version
    marker_valid = (
        marker.get("schema") == BROWSER_SKILL_MARKER_SCHEMA
        and str(marker.get("sha256") or "").upper() == BROWSER_SKILL_SHA256
        and str(marker.get("source") or "") == "bundled"
    )
    if version != BROWSER_SKILL_VERSION:
        result.update({
            "needs_update": True,
            "health_error": (
                f"CLI 版本不兼容：当前 {version or '未知'}，"
                f"需要 {BROWSER_SKILL_VERSION}"
            ),
            "state": "version_mismatch",
            "state_text": "需要更新",
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
        cached_state = cached.get("state")
        if cached_state in {
            "ready",
            "extension_disconnected",
            "extension_incompatible",
            "cli_daemon_failed",
            "execution_unresponsive",
            "check_failed",
        }:
            result.update({
                "healthy": cached_state in {
                    "ready",
                    "extension_disconnected",
                    "extension_incompatible",
                },
                "ready": bool(cached.get("ready")),
                "health_error": cached.get("health_error") or "",
                "state": cached_state,
                "state_text": cached.get("state_text") or "检查完成",
                "diagnostics": cached.get("diagnostics") or {},
                "protocol_incompatible": cached_state == "extension_incompatible",
                "needs_repair": cached_state == "cli_daemon_failed",
            })
            return result
        result.update({
            "healthy": True,
            "state": "cli_installed",
            "state_text": "本地支持已准备，扩展连接待检查",
        })
        return result

    log_browser_skill_event("doctor_start", executable=executable)
    try:
        doctor = _run_bsk(["--json", "--quiet", "doctor"], timeout=30)
    except subprocess.TimeoutExpired:
        result.update({
            "health_error": "bsk doctor 检查超时。",
            "state": "check_failed",
            "state_text": "检查失败",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event("doctor_error", error=result["health_error"])
        return result
    except Exception as exc:
        result.update({
            "health_error": f"bsk doctor 执行失败：{exc}",
            "state": "check_failed",
            "state_text": "检查失败",
        })
        _write_diagnostics_cache(result)
        log_browser_skill_event("doctor_error", error=result["health_error"])
        return result

    payload = _parse_json_output(doctor["stdout"], doctor["stderr"])
    combined = "\n".join([doctor["stdout"], doctor["stderr"]]).strip()
    classification = _classify_doctor_result(payload, combined)
    result["diagnostics"] = {
        "returncode": doctor["returncode"],
        "payload": payload,
        "stdout": doctor["stdout"],
        "stderr": doctor["stderr"],
        "classification": classification["kind"],
        "checks": classification["checks"],
        "extension_connected": classification["kind"] in {
            "connected",
            "extension_incompatible",
        },
    }

    if classification["kind"] == "cli_daemon_failed":
        result.update({
            "needs_repair": True,
            "health_error": classification["detail"] or "BrowserSkill CLI 或 daemon 检查失败。",
            "state": "cli_daemon_failed",
            "state_text": "CLI 或后台服务故障",
        })
    elif classification["kind"] == "extension_disconnected":
        result.update({
            "healthy": True,
            "health_error": "Chrome 或 Edge 扩展尚未连接。",
            "state": "extension_disconnected",
            "state_text": "本地支持已准备，扩展未连接",
        })
    elif classification["kind"] == "extension_incompatible":
        result.update({
            "healthy": True,
            "protocol_incompatible": True,
            "health_error": classification["detail"],
            "state": "extension_incompatible",
            "state_text": "浏览器扩展需要更新",
        })
    elif classification["kind"] == "connected":
        probe = browser_skill_execution_probe()
        result["diagnostics"]["probe"] = probe
        if probe.get("ok"):
            result.update({
                "healthy": True,
                "ready": True,
                "state": "ready",
                "state_text": "已就绪",
            })
        else:
            result.update({
                "health_error": (
                    f"{probe.get('error') or '浏览器执行通道无响应'} "
                    "请重新加载 BrowserSkill 扩展或重启 Chrome/Edge 后再次检查。"
                ),
                "state": "execution_unresponsive",
                "state_text": "执行探测失败",
            })
    else:
        result.update({
            "health_error": (
                classification["detail"]
                or "bsk doctor 未返回可识别的结构化检查结果。"
            ),
            "state": "check_failed",
            "state_text": "检查失败",
        })

    _write_diagnostics_cache(result)
    log_browser_skill_event(
        "doctor_finish",
        state=result["state"],
        returncode=doctor["returncode"],
        ready=result["ready"],
        error=result["health_error"],
    )
    return result


def _safe_extract_zip(archive_path, target_dir):
    target_abs = os.path.abspath(target_dir)
    os.makedirs(target_abs, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            normalized_name = str(member.filename or "").replace("\\", "/")
            parts = [part for part in normalized_name.split("/") if part not in {"", "."}]
            candidate = os.path.abspath(os.path.join(target_abs, *parts))
            unsafe = (
                not normalized_name
                or normalized_name.startswith(("/", "\\"))
                or any(part == ".." for part in parts)
                or bool(os.path.splitdrive(normalized_name)[0])
                or stat.S_ISLNK(member.external_attr >> 16)
                or bool(member.flag_bits & 0x1)
            )
            try:
                outside_target = os.path.commonpath([target_abs, candidate]) != target_abs
            except ValueError:
                outside_target = True
            if unsafe or outside_target:
                raise RuntimeError("BrowserSkill 压缩包包含不安全路径。")
        archive.extractall(target_dir)


def _replace_component_root(staged_root, target_root):
    backup_root = target_root + ".previous"
    if os.path.isdir(backup_root):
        _remove_tree_with_retry(backup_root)
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
        _remove_tree_with_retry(backup_root)


def _remove_tree_with_retry(path, timeout_seconds=5):
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    last_error = None
    while os.path.isdir(path):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
    if os.path.isdir(path):
        raise RuntimeError(
            f"无法删除 BrowserSkill 组件目录，文件可能仍被占用：{last_error}"
        )


def _stop_managed_daemon():
    executable = browser_skill_executable()
    if not os.path.isfile(executable):
        return {"attempted": False, "stopped": True, "exit_code": None, "error": ""}
    result = _run_bsk(["--json", "--quiet", "daemon", "stop"], timeout=12)
    text = "\n".join([result["stdout"], result["stderr"]]).strip()
    lowered = text.lower()
    already_stopped = any(
        token in lowered
        for token in (
            "not running",
            "no daemon",
            "daemon is not registered",
            "daemon not registered",
        )
    )
    stopped = result["returncode"] == 0 or already_stopped
    payload = {
        "attempted": True,
        "stopped": stopped,
        "exit_code": result["returncode"],
        "error": "" if stopped else (text or "无法停止 BrowserSkill daemon。"),
    }
    log_browser_skill_event("daemon_stop", **payload)
    if not stopped:
        raise RuntimeError(payload["error"])
    return payload


def install_browser_skill(progress_callback=None):
    components_root = os.path.dirname(browser_skill_root())
    os.makedirs(components_root, exist_ok=True)
    staged_root = tempfile.mkdtemp(prefix=".browser-skill-", dir=components_root)
    artifact = _bundled_artifact_status("cli")
    log_browser_skill_event(
        "install_start",
        version=BROWSER_SKILL_VERSION,
        source="bundled",
        archive=artifact.get("path"),
    )
    try:
        if not artifact.get("available"):
            raise RuntimeError(artifact.get("error") or "随包 BrowserSkill CLI 不可用。")
        archive_path = artifact["path"]
        if progress_callback:
            progress_callback("正在校验随 Cowork 提供的 BrowserSkill CLI…", 10)
        actual_sha256 = _file_sha256(archive_path)
        if actual_sha256 != BROWSER_SKILL_SHA256:
            raise RuntimeError(
                "BrowserSkill SHA-256 校验失败："
                f"期望 {BROWSER_SKILL_SHA256}，实际 {actual_sha256}"
            )
        extract_root = os.path.join(staged_root, "extract")
        os.makedirs(extract_root)
        if progress_callback:
            progress_callback("正在安全解压 BrowserSkill CLI…", 48)
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
        if progress_callback:
            progress_callback("正在验证 BrowserSkill CLI 版本…", 72)
        completed = _run_bsk_executable(
            os.path.join(install_root, "bsk.exe"),
            ["--version"],
            timeout=10,
        )
        detected_version = _parse_version(
            completed["stdout"] or completed["stderr"]
        )
        if completed["returncode"] != 0 or detected_version != BROWSER_SKILL_VERSION:
            raise RuntimeError(
                "BrowserSkill CLI 验证失败："
                f"期望 {BROWSER_SKILL_VERSION}，实际 {detected_version or '未知'}"
            )
        marker = {
            "schema": BROWSER_SKILL_MARKER_SCHEMA,
            "id": BROWSER_SKILL_COMPONENT_ID,
            "version": BROWSER_SKILL_VERSION,
            "sha256": BROWSER_SKILL_SHA256,
            "source": "bundled",
            "source_url": BROWSER_SKILL_DOWNLOAD_URL,
            "archive": BROWSER_SKILL_ARCHIVE,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "verified": True,
        }
        with open(_marker_path(install_root), "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback("正在原子更新 Tencent BrowserSkill…", 90)
        target_root = browser_skill_root()
        if os.path.isdir(target_root):
            _stop_managed_daemon()
        _replace_component_root(install_root, target_root)
        if progress_callback:
            progress_callback("CLI 已离线安装，请继续准备浏览器扩展。", 100)
        log_browser_skill_event(
            "install_finish",
            version=BROWSER_SKILL_VERSION,
            sha256=actual_sha256,
            source="bundled",
            ok=True,
        )
        return browser_skill_status(run_diagnostics=False)
    except Exception as exc:
        log_browser_skill_event(
            "install_finish",
            version=BROWSER_SKILL_VERSION,
            source="bundled",
            ok=False,
            error=str(exc),
        )
        raise
    finally:
        if os.path.isdir(staged_root):
            shutil.rmtree(staged_root, ignore_errors=True)


def prepare_browser_skill_extension(progress_callback=None):
    components_root = os.path.dirname(browser_skill_extension_component_root())
    os.makedirs(components_root, exist_ok=True)
    staged_root = tempfile.mkdtemp(prefix=".browser-skill-extension-", dir=components_root)
    artifact = _bundled_artifact_status("extension")
    log_browser_skill_event(
        "extension_prepare_start",
        version=BROWSER_SKILL_EXTENSION_VERSION,
        source="bundled",
        archive=artifact.get("path"),
    )
    try:
        if not artifact.get("available"):
            raise RuntimeError(artifact.get("error") or "随包 BrowserSkill 扩展不可用。")
        if progress_callback:
            progress_callback("正在校验随 Cowork 提供的浏览器扩展…", 12)
        actual_sha256 = _file_sha256(artifact["path"])
        if actual_sha256 != BROWSER_SKILL_EXTENSION_SHA256:
            raise RuntimeError(
                "BrowserSkill 扩展 SHA-256 校验失败："
                f"期望 {BROWSER_SKILL_EXTENSION_SHA256}，实际 {actual_sha256}"
            )
        install_root = os.path.join(staged_root, "install")
        extension_root = os.path.join(install_root, "extension")
        os.makedirs(extension_root)
        if progress_callback:
            progress_callback("正在安全解压 BrowserSkill 扩展…", 46)
        _safe_extract_zip(artifact["path"], extension_root)
        manifest_path = os.path.join(extension_root, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise RuntimeError("BrowserSkill 扩展包根目录缺少 manifest.json。")
        try:
            manifest = _read_json_file(manifest_path)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f"BrowserSkill 扩展 manifest.json 无效：{exc}") from exc
        detected_version = str(manifest.get("version") or "").strip()
        if (
            detected_version != BROWSER_SKILL_EXTENSION_VERSION
            or int(manifest.get("manifest_version") or 0) != 3
        ):
            raise RuntimeError(
                "BrowserSkill 扩展验证失败："
                f"期望 {BROWSER_SKILL_EXTENSION_VERSION} / Manifest V3，"
                f"实际 {detected_version or '未知'} / "
                f"Manifest V{manifest.get('manifest_version') or '未知'}"
            )
        marker = {
            "schema": BROWSER_SKILL_MARKER_SCHEMA,
            "id": "browser-skill-extension",
            "version": BROWSER_SKILL_EXTENSION_VERSION,
            "archive": BROWSER_SKILL_EXTENSION_ARCHIVE,
            "archive_sha256": BROWSER_SKILL_EXTENSION_SHA256,
            "source": "bundled",
            "source_url": BROWSER_SKILL_EXTENSION_DOWNLOAD_URL,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "verified": True,
        }
        with open(_extension_marker_path(install_root), "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback("正在发布稳定的扩展目录…", 82)
        _replace_component_root(install_root, browser_skill_extension_component_root())
        prepared = _extension_preparation_status()
        if not prepared.get("prepared"):
            raise RuntimeError(prepared.get("error") or "扩展目录发布后验证失败。")
        if progress_callback:
            progress_callback("离线扩展已准备，请在浏览器中加载该目录。", 100)
        log_browser_skill_event(
            "extension_prepare_finish",
            version=BROWSER_SKILL_EXTENSION_VERSION,
            sha256=actual_sha256,
            path=prepared["path"],
            ok=True,
        )
        return browser_skill_status(run_diagnostics=False)
    except Exception as exc:
        log_browser_skill_event(
            "extension_prepare_finish",
            version=BROWSER_SKILL_EXTENSION_VERSION,
            ok=False,
            error=str(exc),
        )
        raise
    finally:
        if os.path.isdir(staged_root):
            shutil.rmtree(staged_root, ignore_errors=True)


def uninstall_browser_skill():
    root = browser_skill_root()
    log_browser_skill_event("uninstall", root=root)
    if os.path.isdir(root):
        _stop_managed_daemon()
        _remove_tree_with_retry(root)
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
    cleanup = {
        "attempted": bool(session_id),
        "session_id": session_id or "",
        "stopped": False,
        "exit_code": None,
        "error": "",
    }
    if not session_id:
        return cleanup
    cleanup = _run_bsk(
        ["--json", "--quiet", "session", "stop", session_id],
        timeout=10,
    )
    payload = _parse_json_output(cleanup["stdout"], cleanup["stderr"])
    detail = cleanup["stderr"] or cleanup["stdout"]
    lowered = detail.lower()
    already_stopped = (
        "session is not registered" in lowered
        or "session" in lowered and "not registered" in lowered
        or "session" in lowered and "no longer exists" in lowered
    )
    stopped = cleanup["returncode"] == 0 or already_stopped
    result = {
        "attempted": True,
        "session_id": session_id,
        "stopped": stopped,
        "exit_code": cleanup["returncode"],
        "already_stopped": already_stopped,
        "error": "" if stopped else (detail or "BrowserSkill 会话清理失败。"),
        "result": payload,
    }
    log_browser_skill_event(
        "session_stop",
        session_id=session_id,
        cleanup=True,
        returncode=cleanup["returncode"],
        stopped=stopped,
        already_stopped=already_stopped,
        error=result["error"],
    )
    return result


def run_browser_skill_cli(args, timeout_seconds=120, abort_check=None):
    status = browser_skill_status(run_diagnostics=False)
    if not status.get("ready"):
        return {
            "status": "incomplete",
            "error": {
                "code": "browser_skill_not_ready",
                "message": (
                    "浏览器自动化尚未就绪。请前往"
                    "“AI 能力商城 → 浏览器自动化”完成设置和连接检查。"
                ),
                "detail": status.get("health_error") or status.get("state_text"),
            },
            "component": status,
        }
    command_args = [str(item) for item in (args or [])]
    timeout_seconds = max(1, min(int(timeout_seconds or 120), 1800))
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        process = popen_external_program(
            [browser_skill_executable(), "--json", "--quiet", *command_args],
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            **subprocess_kwargs_no_window(),
        )
        log_browser_skill_event(
            "command",
            command=command_args[0] if command_args else "",
            pid=process.pid,
        )
        streams = _wait_process_while_monitoring(
            process,
            timeout_seconds,
            abort_check=abort_check,
        )
        stdout = _read_redirected_output(stdout_file)
        stderr = _read_redirected_output(stderr_file)
    timed_out = streams["timed_out"]
    aborted = streams["aborted"]
    payload = _parse_json_output(stdout, stderr)
    returncode = process.returncode if process.returncode is not None else -1
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if aborted or timed_out:
        code = "aborted" if aborted else "timeout"
        message = (
            "BrowserSkill 命令已由用户中止。"
            if aborted
            else "BrowserSkill CLI 在 Cowork 外层时限内未结束。"
        )
        session_id = _session_id_from_args(command_args)
        try:
            session_cleanup = _stop_session_after_interruption(session_id)
        except Exception as cleanup_exc:
            session_cleanup = {
                "attempted": bool(session_id),
                "session_id": session_id,
                "stopped": False,
                "exit_code": None,
                "error": str(cleanup_exc),
            }
            log_browser_skill_event(
                "error",
                operation="session_cleanup",
                session_id=session_id,
                error=str(cleanup_exc),
            )
        if session_cleanup.get("stopped"):
            message += " Cowork 已结束该 BrowserSkill 会话。"
        log_browser_skill_event(
            "session_cleanup",
            source=code,
            attempted=session_cleanup.get("attempted"),
            session_id=session_cleanup.get("session_id"),
            stopped=session_cleanup.get("stopped"),
            exit_code=session_cleanup.get("exit_code"),
            already_stopped=session_cleanup.get("already_stopped"),
            error=session_cleanup.get("error") or "",
        )
        log_browser_skill_event(
            "command_finish",
            command=command_args[0] if command_args else "",
            ok=False,
            elapsed_ms=elapsed_ms,
            returncode=returncode,
            stdout_chars=len(stdout or ""),
            stderr_chars=len(stderr or ""),
            code=code,
        )
        log_browser_skill_event(
            "timeout" if timed_out else "error",
            operation="command",
            code=code,
            command=command_args[0] if command_args else "",
            elapsed_ms=elapsed_ms,
            stdout_chars=len(stdout or ""),
            stderr_chars=len(stderr or ""),
            cleanup_stopped=session_cleanup.get("stopped"),
        )
        return {
            "status": "incomplete",
            "result": payload,
            "artifacts": _collect_artifacts(payload),
            "stdout": (stdout or "").strip(),
            "stderr": (stderr or "").strip(),
            "error": {"code": code, "message": message},
            "session_cleanup": session_cleanup,
            "elapsed_ms": elapsed_ms,
        }
    if returncode != 0:
        log_browser_skill_event(
            "command_finish",
            command=command_args[0] if command_args else "",
            ok=False,
            elapsed_ms=elapsed_ms,
            returncode=returncode,
            stdout_chars=len(stdout or ""),
            stderr_chars=len(stderr or ""),
        )
        log_browser_skill_event(
            "error",
            operation="command",
            code="bsk_command_failed",
            returncode=returncode,
            command=command_args[0] if command_args else "",
            elapsed_ms=elapsed_ms,
            stdout_chars=len(stdout or ""),
            stderr_chars=len(stderr or ""),
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
            "elapsed_ms": elapsed_ms,
        }
    result = {
        "status": "completed",
        "result": payload if payload not in ({}, None) else (stdout or "").strip(),
        "artifacts": _collect_artifacts(payload),
        "stderr": (stderr or "").strip(),
        "error": None,
        "elapsed_ms": elapsed_ms,
    }
    log_browser_skill_event(
        "command_finish",
        command=command_args[0] if command_args else "",
        ok=True,
        elapsed_ms=elapsed_ms,
        returncode=returncode,
        stdout_chars=len(stdout or ""),
        stderr_chars=len(stderr or ""),
    )
    return result
