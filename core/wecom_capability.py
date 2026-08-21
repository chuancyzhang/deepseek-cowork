import hashlib
import json
import os
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone

from .env_utils import get_app_data_dir, get_resource_dir
from .process_utils import (
    popen_external_program,
    subprocess_kwargs_no_window,
    terminate_process_tree,
)


WECOM_CLI_COMPONENT_ID = "wecom-cli"
WECOM_CLI_VERSION = "1.1.0"
WECOM_CLI_SHA256 = "51CCCBA7A9F84E1995C0AB284DD664A2F79E9ABA0C1FF8782AB9B93540297F1B"
WECOM_CLI_BUNDLE_SCHEMA = 1
WECOM_AUTH_PROVIDER_ID = "wecom_cli"
WECOM_SKILL_ID = "wecom-unified"
WECOM_CONNECTION_STATE_FILENAME = "cowork-connection-state.json"

_LOG_LOCK = threading.RLock()
_VERIFIED_BINARY = None


class WecomCapabilityError(RuntimeError):
    def __init__(self, code, message, *, exit_code=None):
        super().__init__(message)
        self.code = str(code)
        self.exit_code = exit_code


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def wecom_bundle_root():
    return os.path.join(get_resource_dir(), "resources", "wecom_cli")


def wecom_cli_path():
    global _VERIFIED_BINARY
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise WecomCapabilityError(
            "wecom_cli_unsupported_platform",
            "企业微信办公套件当前仅支持 Windows x64。",
        )
    root = wecom_bundle_root()
    manifest_path = os.path.join(root, "bundle.json")
    license_path = os.path.join(root, "LICENSE.txt")
    executable_path = os.path.join(root, "bin", "wecom-cli.exe")
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise WecomCapabilityError(
            "wecom_cli_manifest_invalid", f"企业微信 CLI 随包清单不可用：{exc}"
        ) from exc
    expected = {
        "schema": WECOM_CLI_BUNDLE_SCHEMA,
        "component_id": WECOM_CLI_COMPONENT_ID,
        "version": WECOM_CLI_VERSION,
        "platform": "win32-x64",
        "executable": "bin/wecom-cli.exe",
        "executable_sha256": WECOM_CLI_SHA256,
        "license": "LICENSE.txt",
    }
    mismatches = [
        key for key, value in expected.items()
        if str(manifest.get(key)) != str(value)
    ]
    if mismatches:
        raise WecomCapabilityError(
            "wecom_cli_manifest_mismatch",
            "企业微信 CLI 随包清单与固定版本不一致：" + "、".join(mismatches),
        )
    if not os.path.isfile(license_path):
        raise WecomCapabilityError("wecom_cli_license_missing", "企业微信 CLI 许可证文件缺失。")
    if not os.path.isfile(executable_path):
        raise WecomCapabilityError(
            "wecom_cli_missing", "企业微信 CLI 缺失，请修复或重新安装 Cowork。"
        )
    stat = os.stat(executable_path)
    fingerprint = (executable_path, stat.st_size, stat.st_mtime_ns)
    if _VERIFIED_BINARY != fingerprint:
        actual = _sha256(executable_path)
        if actual != WECOM_CLI_SHA256:
            raise WecomCapabilityError(
                "wecom_cli_integrity_error",
                f"企业微信 CLI 完整性校验失败（实际 SHA-256：{actual}）。",
            )
        _VERIFIED_BINARY = fingerprint
    return executable_path


def wecom_config_dir():
    path = os.path.join(
        get_app_data_dir(), "capability_data", WECOM_SKILL_ID, "wecom-cli"
    )
    os.makedirs(path, exist_ok=True)
    return path


def wecom_runtime_env(config_dir=None):
    environment = dict(os.environ)
    resolved_config_dir = os.path.abspath(config_dir or wecom_config_dir())
    os.makedirs(resolved_config_dir, exist_ok=True)
    environment["WECOM_CLI_CONFIG_DIR"] = resolved_config_dir
    return environment


def _config_fingerprint(config_dir):
    root = os.path.realpath(config_dir)
    digest = hashlib.sha256()
    file_count = 0
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if name == WECOM_CONNECTION_STATE_FILENAME:
                continue
            path = os.path.realpath(os.path.join(current_root, name))
            if os.path.commonpath([path, root]) != root:
                raise WecomCapabilityError(
                    "wecom_config_path_invalid", "企业微信配置目录包含越界文件。"
                )
            relative = os.path.relpath(path, root).replace("\\", "/")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            file_count += 1
    return digest.hexdigest().upper() if file_count else ""


def _connection_state_path(config_dir):
    return os.path.join(os.path.abspath(config_dir), WECOM_CONNECTION_STATE_FILENAME)


def _record_verified_connection(config_dir):
    fingerprint = _config_fingerprint(config_dir)
    if not fingerprint:
        raise WecomCapabilityError(
            "wecom_credentials_missing", "企业微信联网验证成功，但没有找到本机凭据文件。"
        )
    payload = {
        "schema": 1,
        "provider": WECOM_AUTH_PROVIDER_ID,
        "credential_fingerprint": fingerprint,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    target = _connection_state_path(config_dir)
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _clear_verified_connection(config_dir):
    target = _connection_state_path(config_dir)
    if os.path.isfile(target):
        os.remove(target)


def _cached_connection_verified(config_dir):
    try:
        with open(_connection_state_path(config_dir), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return bool(
            payload.get("schema") == 1
            and payload.get("provider") == WECOM_AUTH_PROVIDER_ID
            and payload.get("credential_fingerprint")
            and payload.get("credential_fingerprint") == _config_fingerprint(config_dir)
        )
    except (OSError, ValueError, TypeError, WecomCapabilityError):
        return False


def log_wecom_event(stage, **fields):
    allowed = {
        "operation", "command_category", "duration_ms", "exit_code", "state", "error_code"
    }
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": str(stage or "event"),
    }
    payload.update({key: value for key, value in fields.items() if key in allowed and value is not None})
    log_dir = os.path.join(get_app_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with _LOG_LOCK:
        with open(os.path.join(log_dir, "wecom_capability.log"), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def start_wecom_cli(args, *, cwd, config_dir=None):
    executable = wecom_cli_path()
    if not isinstance(args, (list, tuple)) or not args:
        raise WecomCapabilityError("wecom_cli_invalid_args", "企业微信 CLI 参数必须是非空数组。")
    normalized = []
    for value in args:
        if not isinstance(value, (str, int, float)):
            raise WecomCapabilityError("wecom_cli_invalid_args", "企业微信 CLI 参数只允许字符串或数字。")
        value = str(value)
        if "\x00" in value:
            raise WecomCapabilityError("wecom_cli_invalid_args", "企业微信 CLI 参数不能包含 NUL 字节。")
        normalized.append(value)
    if not cwd or not os.path.isdir(cwd):
        raise WecomCapabilityError("wecom_cli_invalid_cwd", "企业微信 CLI 工作目录不存在。")
    kwargs = subprocess_kwargs_no_window(
        cwd=os.path.abspath(cwd),
        env=wecom_runtime_env(config_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return popen_external_program([executable, *normalized], **kwargs)


def run_wecom_cli(args, *, cwd, timeout_seconds=120, abort_check=None, config_dir=None):
    timeout_seconds = max(1, min(int(timeout_seconds or 120), 1800))
    command_category = str(args[0] if args else "unknown").strip().lower()[:40]
    started = time.monotonic()
    log_wecom_event("start", operation="cli", command_category=command_category)
    process = start_wecom_cli(args, cwd=cwd, config_dir=config_dir)
    log_wecom_event("run", operation="cli", command_category=command_category)
    try:
        while True:
            if callable(abort_check) and abort_check():
                terminate_process_tree(process)
                duration = int((time.monotonic() - started) * 1000)
                log_wecom_event("error", operation="cli", command_category=command_category,
                                duration_ms=duration, error_code="cancelled")
                raise WecomCapabilityError("wecom_cli_cancelled", "企业微信操作已取消。")
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                terminate_process_tree(process)
                duration = int((time.monotonic() - started) * 1000)
                log_wecom_event("error", operation="cli", command_category=command_category,
                                duration_ms=duration, error_code="timeout")
                raise WecomCapabilityError("wecom_cli_timeout", "企业微信操作超时。")
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        duration = int((time.monotonic() - started) * 1000)
        result = {
            "status": "completed" if process.returncode == 0 else "incomplete",
            "exit_code": process.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "duration_ms": duration,
        }
        if process.returncode != 0:
            result["error"] = {
                "code": "wecom_cli_command_failed",
                "message": (stderr or stdout or "企业微信 CLI 执行失败。").strip(),
            }
            log_wecom_event("error", operation="cli", command_category=command_category,
                            duration_ms=duration, exit_code=process.returncode,
                            error_code="command_failed")
        else:
            log_wecom_event("finish", operation="cli", command_category=command_category,
                            duration_ms=duration, exit_code=process.returncode)
        return result
    except Exception:
        if process.poll() is None:
            terminate_process_tree(process)
        raise


def get_wecom_authorization_status(*, verify_remote=False, abort_check=None, config_dir=None):
    status = {
        "provider": WECOM_AUTH_PROVIDER_ID,
        "state": "cli_unavailable",
        "authorized": False,
        "verified": False,
        "state_text": "CLI 不可用",
        "detail": "",
    }
    try:
        resolved_config_dir = os.path.abspath(config_dir or wecom_config_dir())
        os.makedirs(resolved_config_dir, exist_ok=True)
        version = run_wecom_cli(
            ["--version"], cwd=resolved_config_dir, timeout_seconds=10,
            abort_check=abort_check, config_dir=resolved_config_dir,
        )
        if version.get("status") != "completed" or f"wecom-cli {WECOM_CLI_VERSION}" not in version.get("stdout", ""):
            raise WecomCapabilityError(
                "wecom_cli_version_mismatch", f"企业微信 CLI 必须为 {WECOM_CLI_VERSION}。"
            )
        local = run_wecom_cli(
            ["auth", "show", "--status"], cwd=resolved_config_dir, timeout_seconds=10,
            abort_check=abort_check, config_dir=resolved_config_dir,
        )
        local_state = local.get("stdout", "").strip().lower()
        if local.get("status") != "completed" or local_state not in {"authorized", "unauthorized"}:
            raise WecomCapabilityError(
                "wecom_auth_status_invalid", "无法确认企业微信本机授权状态。"
            )
        if local_state == "unauthorized":
            status.update(state="unauthorized", state_text="未授权")
            return status
        status.update(
            state="authorized_unverified",
            authorized=True,
            state_text="本机已授权但未验证",
            detail="请检测连接以确认服务端权限仍然有效。",
        )
        if _cached_connection_verified(resolved_config_dir):
            status.update(
                state="connected",
                verified=True,
                state_text="已连接",
                detail="",
            )
        if not verify_remote:
            return status
        try:
            remote = run_wecom_cli(
                ["identity", "whoami"], cwd=resolved_config_dir, timeout_seconds=30,
                abort_check=abort_check, config_dir=resolved_config_dir,
            )
        except WecomCapabilityError as exc:
            if exc.code == "wecom_cli_cancelled":
                raise
            _clear_verified_connection(resolved_config_dir)
            status.update(
                state="authorized_unverified",
                verified=False,
                state_text="本机已授权但未验证",
                detail=f"联网身份校验失败：{exc}",
                error_code=exc.code,
            )
            return status
        if remote.get("status") == "completed":
            _record_verified_connection(resolved_config_dir)
            status.update(state="connected", verified=True, state_text="已连接", detail="")
        else:
            _clear_verified_connection(resolved_config_dir)
            status.update(
                state="needs_reauthorization",
                state_text="需要重新授权",
                detail="服务端身份校验失败，请检查网络或重新授权。",
            )
        return status
    except WecomCapabilityError as exc:
        status["detail"] = str(exc)
        status["error_code"] = exc.code
        return status
    except Exception as exc:
        status["detail"] = str(exc)
        status["error_code"] = "wecom_cli_unavailable"
        return status
