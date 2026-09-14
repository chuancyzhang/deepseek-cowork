"""Read-only, pre-provider text estimates for the usage popover."""

import json
import re


CONTEXT_CATEGORIES = (
    ("system", "系统提示词", "text_secondary"),
    ("tools", "工具定义", "accent_tool"),
    ("user", "用户消息", "accent_user"),
    ("assistant", "助手消息", "accent_ai"),
    ("results", "工具结果", "accent_success"),
    ("injected", "注入内容", "text_tertiary"),
)


def _text_weight(value):
    if not value:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # Match the existing speed estimate, without tokenizing on the UI thread.
    non_ascii = len(re.findall(r"[^\x00-\x7f\s]", text))
    ascii_chars = len(re.findall(r"[\x21-\x7e]", text))
    return non_ascii + (ascii_chars + 3) // 4


def estimate_context_usage(messages, tools):
    """Never modify the message ledger or include image/base64 payload weights."""
    counts = {key: 0 for key, _label, _color in CONTEXT_CATEGORIES}
    has_media = False
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        if index == 0 and role in {"system", "developer"}:
            category = "system"
        elif role in {"system", "developer"} or meta.get("kind") in {
            "runtime_context", "runtime_context_update", "skill_context",
            "skill_context_update", "skill_state_update", "runtime_instruction",
            "bootstrap_transition",
        }:
            category = "injected"
        else:
            category = {"user": "user", "assistant": "assistant", "tool": "results"}.get(role)
        if category is None:
            continue
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = content
            content = "\n".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            )
            has_media |= any(
                isinstance(part, dict) and part.get("type") not in {"text", "input_text", "output_text"}
                for part in parts
            )
        parts = message.get("content_parts") or []
        has_media |= bool(parts)
        counts[category] += _text_weight(content)
        if role == "assistant":
            counts[category] += _text_weight(message.get("tool_calls"))
            counts[category] += _text_weight(message.get("reasoning_content") or message.get("reasoning"))
    counts["tools"] = _text_weight(tools)
    return {
        "status": "available",
        "method": "pre_provider_text_estimate_v1",
        "counts": counts,
        "total": sum(counts.values()),
        "has_media": has_media,
    }


def normalize_context_usage(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("status") != "available":
        return {"status": "unavailable"} if snapshot else {}
    source = snapshot.get("counts")
    if not isinstance(source, dict):
        return {"status": "unavailable"}
    counts = {}
    for key, _label, _color in CONTEXT_CATEGORIES:
        try:
            counts[key] = max(0, int(source.get(key, 0)))
        except (ValueError, TypeError, OverflowError):
            return {"status": "unavailable"}
    return {
        "status": "available", "method": "pre_provider_text_estimate_v1",
        "counts": counts, "total": sum(counts.values()),
        "has_media": bool(snapshot.get("has_media")),
    }
