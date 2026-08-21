import json
import os
import time

from PySide6.QtCore import QObject, Qt

from core.wecom_capability import (
    WECOM_CLI_COMPONENT_ID,
    WecomCapabilityError,
    get_wecom_authorization_status,
    run_wecom_cli,
)


_SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
_REFERENCE_ROOT = os.path.join(_SKILL_ROOT, "references")
_FILE_COMMANDS = {
    ("media", "upload"),
    ("media", "download"),
    ("disk", "files", "upload"),
    ("disk", "files", "download"),
    ("doc", "import"),
    ("doc", "contents", "get"),
    ("mail", "get"),
    ("meeting", "original", "get"),
}
_FILE_ACTIONS = {"download", "export", "import", "upload"}
_OUTPUT_FLAGS = {"--output", "--output-dir", "--output-file", "-o"}


def _json(payload):
    return json.dumps(payload, ensure_ascii=False)


def _is_within(path, root):
    try:
        return os.path.normcase(os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])) == os.path.normcase(os.path.abspath(root))
    except (OSError, TypeError, ValueError):
        return False


def wecom_reference_read(path):
    try:
        raw = str(path or "").strip().replace("\\", "/")
        if not raw or os.path.isabs(raw) or raw.startswith("/"):
            raise ValueError("path 必须是 references 目录内的相对 Markdown 路径。")
        if raw.startswith("references/"):
            raw = raw[len("references/"):]
        if any(part in {"", ".", ".."} for part in raw.split("/")):
            raise ValueError("path 包含不安全的路径片段。")
        target = os.path.realpath(os.path.join(_REFERENCE_ROOT, *raw.split("/")))
        reference_root = os.path.realpath(_REFERENCE_ROOT)
        if not _is_within(target, reference_root) or not target.lower().endswith(".md"):
            raise ValueError("只能读取企业微信能力已声明的 Markdown 参考文件。")
        if not os.path.isfile(target):
            raise FileNotFoundError(f"参考文件不存在：{raw}")
        with open(target, "r", encoding="utf-8") as handle:
            content = handle.read()
        return _json({"status": "completed", "path": f"references/{raw}", "content": content})
    except Exception as exc:
        return _json({
            "status": "incomplete",
            "error": {"code": "wecom_reference_invalid_path", "message": str(exc)},
        })


def _normalize_args(args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError) as exc:
            raise ValueError("args 必须是 JSON 数组，不能是 shell 命令字符串。") from exc
    if not isinstance(args, list) or not args:
        raise ValueError("args 必须是非空数组。")
    normalized = []
    for item in args:
        if not isinstance(item, (str, int, float)):
            raise ValueError("每个企业微信 CLI 参数必须是字符串或数字。")
        text = str(item)
        if "\x00" in text:
            raise ValueError("企业微信 CLI 参数不能包含 NUL 字节。")
        normalized.append(text)
    if any(value.strip().lower() == "auth" for value in normalized):
        raise ValueError("对话内禁止执行授权命令，请前往能力商店连接企业微信。")
    return normalized


def _workspace_root(workspace_dir):
    if not workspace_dir:
        return ""
    root = os.path.abspath(str(workspace_dir))
    return root if os.path.isdir(root) else ""


def _command_prefix(args, length):
    return tuple(str(value).strip().lower() for value in args[:length])


def _uses_local_files(args):
    command_tokens = {
        str(value).strip().lower()
        for value in args[:4]
        if not str(value).startswith("-")
    }
    if command_tokens.intersection(_FILE_ACTIONS):
        return True
    if any(str(value).strip().lower() in _OUTPUT_FLAGS for value in args):
        return True
    for command in _FILE_COMMANDS:
        if _command_prefix(args, len(command)) == command:
            return True
    return False


def _validate_json_paths(args, workspace_root):
    def walk(value, key=""):
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and (key.endswith("path") or key.endswith("_path")):
            candidate = os.path.realpath(
                value if os.path.isabs(value) else os.path.join(workspace_root, value)
            )
            if not _is_within(candidate, os.path.realpath(workspace_root)):
                raise ValueError("企业微信本地文件路径必须位于当前会话工作区内。")

    for index, item in enumerate(args[:-1]):
        if item != "--json":
            continue
        try:
            payload = json.loads(args[index + 1])
        except (TypeError, ValueError) as exc:
            raise ValueError("--json 后必须是有效 JSON。") from exc
        walk(payload)

    for index, item in enumerate(args):
        if str(item).strip().lower() not in _OUTPUT_FLAGS:
            continue
        if index + 1 >= len(args):
            raise ValueError(f"{item} 需要工作区内的输出路径。")
        candidate = os.path.realpath(
            args[index + 1]
            if os.path.isabs(args[index + 1])
            else os.path.join(workspace_root, args[index + 1])
        )
        if not _is_within(candidate, os.path.realpath(workspace_root)):
            raise ValueError("企业微信输出路径必须位于当前会话工作区内。")


def _json_has_local_path(args):
    def contains(value, key=""):
        if isinstance(value, dict):
            return any(contains(child, str(child_key).lower()) for child_key, child in value.items())
        if isinstance(value, list):
            return any(contains(child, key) for child in value)
        return isinstance(value, str) and (key.endswith("path") or key.endswith("_path"))

    for index, item in enumerate(args[:-1]):
        if item != "--json":
            continue
        try:
            if contains(json.loads(args[index + 1])):
                return True
        except (TypeError, ValueError) as exc:
            raise ValueError("--json 后必须是有效 JSON。") from exc
    return False


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


def _emit_diagnostic(context, status, started_at, command_category, **fields):
    signal = context.get("observability_signal") if isinstance(context, dict) else None
    if not hasattr(signal, "emit"):
        return
    payload = {
        "type": "wecom_cli",
        "status": status,
        "command_category": command_category,
        "duration_seconds": round(max(time.time() - started_at, 0.0), 3),
        "timestamp": time.time(),
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    signal.emit(payload)


def wecom_cli(args, timeout_seconds=120, workspace_dir=None, _context=None):
    started_at = time.time()
    command_category = "unknown"
    try:
        normalized = _normalize_args(args)
        command_category = normalized[0].strip().lower()[:40]
        _emit_diagnostic(_context, "start", started_at, command_category)
        auth = get_wecom_authorization_status(verify_remote=False)
        if not auth.get("authorized"):
            raise WecomCapabilityError(
                "wecom_not_authorized", "企业微信尚未连接，请前往能力商店扫码连接后重试。"
            )
        workspace_root = _workspace_root(workspace_dir)
        if (_uses_local_files(normalized) or _json_has_local_path(normalized)) and not workspace_root:
            raise ValueError("当前会话没有有效工作区，不能上传、下载或输出本地文件。")
        if workspace_root:
            _validate_json_paths(normalized, workspace_root)
        cwd = workspace_root or os.getcwd()
        abort_state = _init_abort_state(_context)
        _emit_diagnostic(_context, "run", started_at, command_category)
        result = run_wecom_cli(
            normalized,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            abort_check=lambda: bool(abort_state["aborted"]),
        )
        if result.get("status") == "completed":
            _emit_diagnostic(
                _context, "finish", started_at, command_category,
                exit_code=result.get("exit_code"),
            )
        else:
            _emit_diagnostic(
                _context, "error", started_at, command_category,
                exit_code=result.get("exit_code"),
                error_code=(result.get("error") or {}).get("code"),
            )
        return _json(result)
    except WecomCapabilityError as exc:
        _emit_diagnostic(_context, "error", started_at, command_category, error_code=exc.code)
        return _json({
            "status": "incomplete",
            "error": {"code": exc.code, "message": str(exc)},
        })
    except Exception as exc:
        _emit_diagnostic(_context, "error", started_at, command_category, error_code="wecom_cli_invalid_request")
        return _json({
            "status": "incomplete",
            "error": {"code": "wecom_cli_invalid_request", "message": str(exc)},
        })


TOOL_EXPORTS = [
    {
        "name": "wecom_reference_read",
        "handler": wecom_reference_read,
        "description": "Read one declared WeCom business reference Markdown file on demand.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path below references/, such as wecomcli-calendar.md."}
            },
            "required": ["path"],
        },
        "read_only": True,
        "search_hint": "wecom enterprise wechat reference command arguments business domain",
    },
    {
        "name": "wecom_cli",
        "handler": wecom_cli,
        "description": "Run the Cowork-managed WeCom CLI with a structured argument array. Authorization commands are forbidden here.",
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, {"type": "number"}]},
                    "description": "CLI arguments without the executable; never include a shell command string."
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800}
            },
            "required": ["args"],
        },
        "destructive": True,
        "search_hint": "wecom enterprise wechat contact document sheet calendar meeting todo drive mail message",
        "result_format": "json",
        "metadata": {"component_id": WECOM_CLI_COMPONENT_ID},
    },
]
