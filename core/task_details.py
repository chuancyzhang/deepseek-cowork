"""Read-only presentation helpers. They never modify model or tool data."""

import json
import re


def redact_diagnostic(value):
    if isinstance(value, dict):
        return {key: ("[已隐藏]" if str(key).lower().replace("-", "_") in {
            "api_key", "authorization", "access_token", "refresh_token", "x_api_key", "password",
        } else redact_diagnostic(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_diagnostic(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [已隐藏]", value)
        value = re.sub(r"(?i)([\"']?(?:api[_-]?key|access_token|refresh_token|authorization|password)[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
                       lambda m: m[1] + m[2] + "[已隐藏]" + m[2], value)
    return value


def parsed_result(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def tool_result_status(value):
    value = parsed_result(value)
    if not isinstance(value, dict):
        if isinstance(value, str) and value.strip().lower().startswith(("error:", "error executing ")):
            return "error"
        return "done"
    status = str(value.get("status") or "").lower()
    if value.get("background_tool") is True and status in {"running", "queued"}:
        return "running"
    if status == "partial_apply":
        return "partial"
    if value.get("ok") is False or status in {"error", "failed", "unknown", "cancelled", "denied", "invalid_tool_call"}:
        return "error"
    if (value.get("error") and value.get("ok") is not True
            and status not in {"ok", "success", "succeeded", "completed", "complete"}):
        return "error"
    return "done"


def tool_result_summary(value, fallback=""):
    value = parsed_result(value)
    if isinstance(value, dict):
        for key in ("message", "summary", "error", "content"):
            text = value.get(key)
            if isinstance(text, dict):
                text = text.get("message") or text.get("code")
            if isinstance(text, str) and text.strip():
                return " ".join(redact_diagnostic(text).split())[:160]
        return fallback or "调用已结束，点击查看完整结果"
    if isinstance(value, str):
        return " ".join(redact_diagnostic(value).split())[:160]
    return fallback or "调用已结束，点击查看完整结果"
