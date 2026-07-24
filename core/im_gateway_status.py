import json
import os
import re
import time

from core.env_utils import get_app_data_dir


IM_GATEWAY_STATUS_FILENAME = "im_gateway_status.json"
IM_GATEWAY_STATES = {
    "stopped",
    "connecting",
    "connected",
    "reconnecting",
    "disconnected",
    "error",
}


def im_gateway_status_path():
    return os.path.join(get_app_data_dir(), IM_GATEWAY_STATUS_FILENAME)


def write_im_gateway_status(provider="", state="stopped", error=""):
    normalized_state = str(state or "").strip().lower()
    if normalized_state not in IM_GATEWAY_STATES:
        raise ValueError(f"未知企业消息网关状态：{state}")
    error_text = str(error or "").strip()
    error_text = re.sub(r"https?://\S+", "<地址已省略>", error_text)
    error_text = re.sub(
        r"(?i)\b(secret|token|authorization)\b\s*[:=]\s*[^\s,;]+",
        r"\1=<已隐藏>",
        error_text,
    )
    payload = {
        "provider": str(provider or "").strip().lower(),
        "state": normalized_state,
        "error": error_text[:800],
        "updated_at": int(time.time()),
        "pid": os.getpid(),
    }
    path = im_gateway_status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + f".{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temp_path, path)
    return payload


def read_im_gateway_status():
    path = im_gateway_status_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    state = str(value.get("state") or "").strip().lower()
    if state not in IM_GATEWAY_STATES:
        return {}
    return {
        "provider": str(value.get("provider") or "").strip().lower(),
        "state": state,
        "error": str(value.get("error") or "").strip(),
        "updated_at": value.get("updated_at"),
        "pid": value.get("pid"),
    }
