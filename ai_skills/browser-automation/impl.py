from core.tool_cancellation import init_abort_state as _init_abort_state
import json
import os
import re
import urllib.parse
import uuid
import time


from core.browser_skill_component import (
    BROWSER_SKILL_COMPONENT_ID,
    log_browser_skill_event,
    run_browser_skill_cli,
)
from core.env_utils import get_app_data_dir
from core.tool_images import ToolImageError, persist_tool_image
from core.browser_backend import browser_operation, BrowserBackendError, EXTERNAL


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
    "observe",
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
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
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
        # Own the output path: never infer image access from arbitrary paths in
        # CLI JSON, and do not leave the CLI's default shared temp file behind.
        outputs = [i for i, item in enumerate(normalized) if item == "--out" or item.startswith("--out=")]
        if len(outputs) > 1:
            raise ValueError("screenshot accepts only one --out path")
        if outputs:
            index = outputs[0]
            if normalized[index].startswith("--out="):
                normalized[index:index + 1] = ["--out", normalized[index].split("=", 1)[1]]
            if index + 1 >= len(normalized) or not normalized[index + 1] or normalized[index + 1].startswith("--"):
                raise ValueError("--out requires a path")
            normalized[index + 1] = _validate_output_path(normalized[index + 1], workspace_root)
        else:
            output = os.path.join(get_app_data_dir(), "browser-automation", "screenshots", uuid.uuid4().hex + ".png")
            normalized.extend(["--out", _validate_output_path(output, workspace_root)])
    if command == "evaluate":
        expression = " ".join(normalized[1:])
        if _SENSITIVE_EVALUATE_RE.search(expression):
            raise ValueError(
                "evaluate cannot read cookies, browser storage, authorization data, or access tokens"
            )
    return normalized


def _attach_screenshot(result, output_path, context, abort_state):
    session_id = context.get("session_id") or ""
    call_id = context.get("tool_call_id") or ""
    log_browser_skill_event("screenshot_image_start", session_id=session_id, call_id=call_id)
    try:
        if abort_state["aborted"]:
            raise ToolImageError("cancelled", "操作已停止，截图文件已保留。")
        if context.get("model_api_protocol") not in {"responses", "chat_completions"}:
            raise ToolImageError("unsupported_protocol", "截图看图支持 Responses 和 Chat Completions 协议。")
        if not context.get("model_supports_vision"):
            raise ToolImageError("vision_not_supported", "当前模型未启用图片输入，请切换到支持图片的模型；只保存截图可设置 include_image=false。")
        config = context.get("config_manager")
        if not session_id or config is None or not (history_dir := config.get_chat_history_dir()):
            raise ToolImageError("session_unavailable", "当前会话的图片存储上下文不可用，无法保存可重放的图片结果。")
        part = persist_tool_image(output_path, history_dir, session_id)
    except Exception as exc:
        code = getattr(exc, "code", "image_read_failed")
        log_browser_skill_event("screenshot_image_failed", session_id=session_id, call_id=call_id, code=code)
        result.update({
            "status": "cancelled" if code == "cancelled" else "incomplete",
            "screenshot_status": "completed",
            "image_status": "cancelled" if code == "cancelled" else "failed",
            "error": {"code": code, "message": f"截图命令已完成，但图片未能提供给模型：{exc} 已生成的文件和原始结果已保留。"},
        })
        return
    result.update({
        "image_status": "ready",
        "content": "截图已保存，图片随本次工具结果提供给模型，无需再调用 view_image。",
        "content_parts": [part],
    })
    log_browser_skill_event("screenshot_image_ready", session_id=session_id, call_id=call_id,
                           mime_type=part["mime_type"], size_bytes=part["size_bytes"])


def _run_owned_command(normalized, context, timeout_seconds, abort_state):
    from core.browser_skill_component import _session_id_from_args
    with browser_operation(context, EXTERNAL) as (coordinator, auth):
        normalized = list(normalized)
        help_only = "--help" in normalized or "-h" in normalized
        if any(item.startswith("--session=") or item.startswith("--request-id") for item in normalized) or normalized.count("--session") > 1:
            raise BrowserBackendError("invalid_session", "请只使用一次 --session 参数；会话创建标识由应用管理。")
        if normalized[0] == "session" and not help_only and (len(normalized) < 2 or normalized[1] not in {"start", "stop"} or any(item.startswith("--all") for item in normalized)):
            raise BrowserBackendError("invalid_session", "只允许创建或结束当前任务自己的会话，不能管理其他应用的会话。")
        if normalized[:2] == ["session", "stop"] and not help_only and (len(normalized) < 3 or normalized[2].startswith("-") or "--session" in normalized):
            raise BrowserBackendError("invalid_session", "结束会话请使用 session stop 后紧接本任务的会话 ID。")
        external_id = _session_id_from_args(normalized)
        if not external_id and not help_only and normalized[0] not in {"browsers", "doctor", "status", "session"}:
            raise BrowserBackendError("invalid_session", "网页命令必须提供当前任务创建的 --session。")
        if external_id and not coordinator.owns_session(auth.session_id, auth.run_id, external_id):
            raise BrowserBackendError("browser_session_unavailable", "此 BrowserSkill 会话不属于当前任务，请先创建本任务的浏览器会话。")
        request_token = ""
        if normalized[:2] == ["session", "start"] and not help_only:
            request_token = f"{int(time.time() * 1000) + 600000}:{uuid.uuid4()}"
            coordinator.track_session(auth.session_id, auth.run_id, "request:" + request_token)
            normalized.extend(["--request-id", request_token])
        result = run_browser_skill_cli(normalized, timeout_seconds=timeout_seconds,
                                       abort_check=lambda: bool(abort_state["aborted"]))
        payload = result.get("result") or {}
        if normalized[:2] == ["session", "start"] and isinstance(payload, dict) and payload.get("session_id"):
            coordinator.track_session(auth.session_id, auth.run_id, str(payload["session_id"]))
            if request_token:
                coordinator.track_session(auth.session_id, auth.run_id, "request:" + request_token, stopped=True)
        elif request_token and result.get("status") == "completed":
            result.update(status="incomplete", error={"code": "session_result_unknown", "message": "未获得可确认的会话 ID，请勿重复创建。任务结束时会清理本次创建请求，可在实验功能中查看释放状态。"})
        stopped = not help_only and normalized[:2] == ["session", "stop"] and result.get("status") == "completed"
        if external_id and (stopped or (result.get("session_cleanup") or {}).get("stopped")):
            coordinator.track_session(auth.session_id, auth.run_id, external_id, stopped=True)
        return result


def browser_skill_cli(
    args,
    timeout_seconds=120,
    workspace_dir=None,
    _context=None,
    include_image=True,
):
    try:
        if not isinstance(include_image, bool):
            raise ValueError("include_image must be a boolean")
        context = _context if isinstance(_context, dict) else {}
        workspace_root = _workspace_root(workspace_dir, _context)
        normalized = _normalize_args(args, workspace_root)
        abort_state = _init_abort_state(_context)
        command = normalized[0]
        if command == "session" and len(normalized) > 1 and normalized[1] == "start":
            log_browser_skill_event("session_start")
        elif command == "session" and len(normalized) > 1 and normalized[1] == "stop":
            log_browser_skill_event("session_stop")
        result = _run_owned_command(normalized, context, timeout_seconds, abort_state)
        if result.get("status") == "completed":
            if command == "screenshot":
                output_path = normalized[normalized.index("--out") + 1]
                result["artifacts"] = [{"type": "screenshot", "path": output_path}]
                if include_image:
                    _attach_screenshot(result, output_path, context, abort_state)
                else:
                    result["image_status"] = "not_requested"
                    result["content"] = "截图已保存为文件；本次未向模型提供图片内容。"
            if result.get("status") == "completed":
                log_browser_skill_event("finish", command=normalized[0])
        # Keep structured image parts visible to the Agent's shared result pipeline.
        return result
    except Exception as exc:
        log_browser_skill_event("error", operation="tool", error=str(exc))
        return {
            "status": "incomplete",
            "error": {
                "code": getattr(exc, "code", "browser_skill_invalid_request"),
                "message": str(exc),
            },
        }


TOOL_EXPORTS = [
    {
        "name": "browser_skill_cli",
        "handler": browser_skill_cli,
        "description": (
            "Run one Tencent BrowserSkill bsk command with structured arguments. "
            "Use session start/stop and pass the returned session id to every browser command. "
            "Screenshot commands return the actual image to vision-capable models by default, "
            "without a separate view_image call."
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
                "include_image": {
                    "type": "boolean",
                    "description": "For screenshot only: return the actual image to the model (default true). Set false to save the file only. Other commands are unaffected.",
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
