import json
import os
import re
import urllib.parse

from PySide6.QtCore import QObject, Qt

from core.browser_skill_component import (
    BROWSER_SKILL_COMPONENT_ID,
    log_browser_skill_event,
    run_browser_skill_cli,
)
from core.env_utils import get_app_data_dir


_ALLOWED_COMMANDS = {
    "browsers",
    "click",
    "doctor",
    "evaluate",
    "fill",
    "get-html",
    "navigate",
    "navigate-back",
    "navigate-forward",
    "press",
    "reload",
    "request-help",
    "screenshot",
    "select",
    "session",
    "snapshot",
    "status",
    "tab",
    "wait-for-navigation",
    "wait-ms",
}
_SENSITIVE_EVALUATE_RE = re.compile(
    r"(document\s*\.\s*cookie|localStorage|sessionStorage|indexedDB|"
    r"authorization|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)


def _json(payload):
    return json.dumps(payload, ensure_ascii=False)


def _workspace_root(workspace_dir, context):
    if workspace_dir:
        return os.path.abspath(workspace_dir)
    if isinstance(context, dict):
        config = context.get("config_manager")
        if config:
            value = config.get("default_workspace", "")
            if value:
                return os.path.abspath(value)
    return os.getcwd()


def _is_within(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except (OSError, ValueError):
        return False


def _validated_file_url(value, workspace_root):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme.lower() != "file":
        return
    local_path = urllib.parse.unquote(parsed.path)
    if os.name == "nt" and local_path.startswith("/"):
        local_path = local_path[1:]
    if not _is_within(local_path, workspace_root):
        raise ValueError("file URL must stay inside the active workspace")


def _validate_output_path(value, workspace_root):
    raw_path = os.path.expanduser(str(value or ""))
    output_path = os.path.abspath(
        raw_path if os.path.isabs(raw_path) else os.path.join(workspace_root, raw_path)
    )
    app_root = os.path.abspath(os.path.join(get_app_data_dir(), "browser-automation"))
    if not (_is_within(output_path, workspace_root) or _is_within(output_path, app_root)):
        raise ValueError("screenshot output must stay inside the workspace or app data directory")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    return output_path


def _normalize_args(args, workspace_root):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError) as exc:
            raise ValueError("args must be a JSON array, not a shell command string") from exc
    if not isinstance(args, list) or not args:
        raise ValueError("args must be a non-empty array")
    normalized = []
    for item in args:
        if not isinstance(item, (str, int, float)):
            raise ValueError("each BrowserSkill argument must be a string or number")
        text = str(item)
        if "\x00" in text:
            raise ValueError("BrowserSkill arguments cannot contain NUL bytes")
        normalized.append(text)
    command = normalized[0].strip().lower()
    if command not in _ALLOWED_COMMANDS:
        raise ValueError(f"unsupported BrowserSkill command: {command}")
    normalized[0] = command
    if command == "navigate" and len(normalized) >= 2:
        _validated_file_url(normalized[1], workspace_root)
    if command == "screenshot":
        for index, item in enumerate(normalized):
            if item == "--out":
                if index + 1 >= len(normalized):
                    raise ValueError("--out requires a path")
                normalized[index + 1] = _validate_output_path(normalized[index + 1], workspace_root)
    if command == "evaluate":
        expression = " ".join(normalized[1:])
        if _SENSITIVE_EVALUATE_RE.search(expression):
            raise ValueError(
                "evaluate cannot read cookies, browser storage, authorization data, or access tokens"
            )
    return normalized


def _init_abort_state(context):
    state = {"aborted": False, "bridge": None}
    if not isinstance(context, dict) or not context.get("abort_signal"):
        return state

    class SignalBridge(QObject):
        def trigger(self):
            state["aborted"] = True

    bridge = SignalBridge()
    context["abort_signal"].connect(bridge.trigger, Qt.DirectConnection)
    state["bridge"] = bridge
    return state


def browser_skill_cli(
    args,
    timeout_seconds=120,
    workspace_dir=None,
    _context=None,
):
    try:
        workspace_root = _workspace_root(workspace_dir, _context)
        normalized = _normalize_args(args, workspace_root)
        abort_state = _init_abort_state(_context)
        command = normalized[0]
        if command == "session" and len(normalized) > 1 and normalized[1] == "start":
            log_browser_skill_event("session_start")
        elif command == "session" and len(normalized) > 1 and normalized[1] == "stop":
            log_browser_skill_event("session_stop")
        result = run_browser_skill_cli(
            normalized,
            timeout_seconds=timeout_seconds,
            abort_check=lambda: bool(abort_state["aborted"]),
        )
        if result.get("status") == "completed":
            log_browser_skill_event("finish", command=normalized[0])
        return _json(result)
    except Exception as exc:
        log_browser_skill_event("error", operation="tool", error=str(exc))
        return _json({
            "status": "incomplete",
            "error": {
                "code": "browser_skill_invalid_request",
                "message": str(exc),
            },
        })


TOOL_EXPORTS = [
    {
        "name": "browser_skill_cli",
        "handler": browser_skill_cli,
        "description": (
            "Run one Tencent BrowserSkill bsk command with structured arguments. "
            "Use session start/stop and pass the returned session id to every browser command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "number"},
                        ]
                    },
                    "description": (
                        "bsk arguments without the executable, for example "
                        "['snapshot', '--session', 'abcd']."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1800,
                    "description": "Command timeout in seconds.",
                },
            },
            "required": ["args"],
        },
        "destructive": True,
        "search_hint": (
            "browser automation bsk chromium navigate snapshot click fill "
            "scrape logged in session borrow tab"
        ),
        "result_format": "json",
        "metadata": {"component_id": BROWSER_SKILL_COMPONENT_ID},
    },
]
