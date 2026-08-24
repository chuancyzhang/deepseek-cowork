import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests


API_BASE_URL = "https://mcp.mysteel.com"
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


class MysteelAPIError(RuntimeError):
    def __init__(self, code, message, *, http_status=None):
        self.code = str(code or "API_ERROR")
        self.http_status = http_status
        super().__init__(str(message or "钢联数据 API 返回错误。"))


def require_env(name):
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} 未配置，请在 Cowork 能力中心完成配置。")
    return value


def configured_api_key():
    return require_env("MYSTEEL_API_KEY")


def workspace_root():
    root = Path(require_env("COWORK_WORKSPACE_DIR")).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Cowork 工作区不存在：{root}")
    return root


def resolve_workspace_path(value, *, default_relative, create_directory=False):
    root = workspace_root()
    candidate = Path(value).expanduser() if value else root / default_relative
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"输出路径必须位于当前 Cowork 工作区内：{candidate}") from exc
    if create_directory:
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def resolve_output_dir(category, value=None):
    safe_category = re.sub(r"[^a-z0-9-]+", "-", str(category or "output").lower()).strip("-") or "output"
    return resolve_workspace_path(
        value,
        default_relative=Path("mysteel") / "output" / safe_category,
        create_directory=True,
    )


def unique_stem(prefix):
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(prefix or "result")).strip("-") or "result"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def diagnostic(operation, endpoint, status, *, started_at=None, http_status=None, error_code="", attempt=None):
    payload = {
        "skill": "mysteel-data",
        "operation": str(operation or ""),
        "endpoint": str(endpoint or ""),
        "status": str(status or ""),
    }
    if started_at is not None:
        payload["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    if http_status is not None:
        payload["http_status"] = int(http_status)
    if error_code:
        payload["error_code"] = str(error_code)
    if attempt is not None:
        payload["attempt"] = int(attempt)
    print("[mysteel-diagnostic] " + json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def _business_error(payload):
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    message = payload.get("message") or payload.get("mess") or payload.get("msg") or "钢联数据 API 返回业务错误。"
    code_text = str(code) if code is not None else ""
    if code_text == "400" and ("token" in str(message).lower() or "密钥" in str(message)):
        return MysteelAPIError("AUTH_ERROR", "钢联 API Key 无效或已过期。")
    if code is not None and code_text not in {"0", "200"}:
        return MysteelAPIError(code_text, message)
    if payload.get("success") is False:
        return MysteelAPIError(code_text or "API_ERROR", message)
    return None


def request_json(method, path, *, operation, params=None, json_body=None, timeout_seconds=30):
    endpoint = str(path or "")
    url = API_BASE_URL.rstrip("/") + "/" + endpoint.lstrip("/")
    started_at = time.monotonic()
    api_key = configured_api_key()
    headers = {"Accept": "application/json", "token": api_key}
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    for attempt in (1, 2):
        diagnostic(operation, endpoint, "start", started_at=started_at, attempt=attempt)
        try:
            response = requests.request(
                method=str(method or "GET").upper(),
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            if attempt == 1:
                diagnostic(operation, endpoint, "retry", started_at=started_at, error_code=type(exc).__name__, attempt=attempt)
                continue
            diagnostic(operation, endpoint, "error", started_at=started_at, error_code=type(exc).__name__, attempt=attempt)
            raise

        if response.status_code in RETRYABLE_STATUS_CODES and attempt == 1:
            diagnostic(operation, endpoint, "retry", started_at=started_at, http_status=response.status_code, error_code="HTTP_5XX", attempt=attempt)
            continue
        if response.status_code in {401, 403}:
            diagnostic(operation, endpoint, "error", started_at=started_at, http_status=response.status_code, error_code="AUTH_ERROR", attempt=attempt)
            raise MysteelAPIError("AUTH_ERROR", "钢联 API Key 无效或已过期。", http_status=response.status_code)
        if response.status_code >= 400:
            diagnostic(operation, endpoint, "error", started_at=started_at, http_status=response.status_code, error_code="HTTP_ERROR", attempt=attempt)
            raise MysteelAPIError("HTTP_ERROR", f"钢联数据请求失败：HTTP {response.status_code}", http_status=response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            diagnostic(operation, endpoint, "error", started_at=started_at, http_status=response.status_code, error_code="PARSE_ERROR", attempt=attempt)
            raise MysteelAPIError("PARSE_ERROR", "钢联数据响应不是有效 JSON。", http_status=response.status_code) from exc

        business_error = _business_error(payload)
        if business_error:
            diagnostic(operation, endpoint, "error", started_at=started_at, http_status=response.status_code, error_code=business_error.code, attempt=attempt)
            raise business_error
        diagnostic(operation, endpoint, "finish", started_at=started_at, http_status=response.status_code, attempt=attempt)
        return payload

    raise RuntimeError("钢联数据请求未产生结果。")


def print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail_cli(exc):
    code = getattr(exc, "code", type(exc).__name__)
    diagnostic("cli", "", "error", error_code=code)
    print_json({"success": False, "error": str(code), "message": str(exc)})
    return 1
