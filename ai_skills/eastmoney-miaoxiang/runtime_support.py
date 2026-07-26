import json
import os
import sys
import time
from pathlib import Path


DEFAULT_API_URL = "https://mkapi2.dfcfs.com/finskillshub"


def require_env(name):
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} 未配置，请在 Cowork 能力中心完成配置。")
    return value


def configured_api_url():
    return require_env("MX_API_URL").rstrip("/")


def resolve_output_dir(value=None):
    if value:
        target = Path(value).expanduser()
    else:
        workspace_dir = str(os.environ.get("COWORK_WORKSPACE_DIR") or "").strip()
        if not workspace_dir:
            raise RuntimeError(
                "COWORK_WORKSPACE_DIR 未注入；请通过 Cowork 的 run_skill_script 运行，"
                "或显式提供 --output-dir。"
            )
        target = Path(workspace_dir) / "mx_data" / "output"
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def diagnostic(operation, endpoint, status, *, started_at=None, http_status=None, error_code=""):
    payload = {
        "skill": "eastmoney-miaoxiang",
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
    print("[mx-diagnostic] " + json.dumps(payload, ensure_ascii=False), file=sys.stderr)
