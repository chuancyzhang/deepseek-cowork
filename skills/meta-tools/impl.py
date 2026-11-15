import json
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.tool_images import tool_image_content_parts


PARALLEL_TOOL_BLOCKLIST = {
    "parallel_tools",
    "tool_search",
    "request_user_approval",
    "request_user_input",
    "update_experience",
    "write_memories",
    "bash",
    "run_python_code",
    "run_node_code",
    "run_skill_script",
    "spawn_agent",
    "send_input",
    "wait_agent",
    "close_agent",
    "list_agents",
    "publish_artifacts",
}


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _json_safe(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return str(value)


def _build_parallel_tool_error(index, call_id, name, message, *, status="error"):
    return {
        "index": index,
        "id": str(call_id or ""),
        "name": str(name or ""),
        "status": status,
        "duration": 0.0,
        "error": str(message or "Unknown error"),
    }


def _execute_parallel_subcall(index, call_id, name, args, context):
    start_time = time.time()
    skill_manager = (context or {}).get("skill_manager")
    if not skill_manager:
        return _build_parallel_tool_error(index, call_id, name, "SkillManager not found in context.")

    run_context = context.get("run_context") if isinstance(context, dict) else None
    discovered_tool_names = context.get("discovered_tool_names") if isinstance(context, dict) else None
    access = skill_manager.validate_tool_run_access(
        name,
        run_context=run_context,
        discovered_tool_names=discovered_tool_names,
        require_read_only=True,
        deny_tool_names=PARALLEL_TOOL_BLOCKLIST,
    )
    resolved_name = access.get("name") or str(name or "").strip()
    if not access.get("ok"):
        return _build_parallel_tool_error(
            index,
            call_id,
            resolved_name or name,
            access.get("error") or "Tool access denied.",
            status="denied",
        )

    safe_args = args if isinstance(args, dict) else {}
    child_context = dict(context or {})
    child_context.pop("authorization_started", None)
    if "run_context" in child_context:
        child_context["run_context"] = _json_copy(child_context.get("run_context"), {})
    child_context["tool_call_id"] = str(call_id or f"{context.get('tool_call_id') or 'parallel'}:{index}")
    result = skill_manager.call_tool(resolved_name, safe_args, context=child_context)
    duration = round(max(time.time() - start_time, 0.0), 6)
    if isinstance(result, str) and result.startswith("Error"):
        return {
            "index": index,
            "id": str(call_id or ""),
            "name": resolved_name,
            "status": "error",
            "duration": duration,
            "error": result,
        }
    if isinstance(result, dict) and (
        result.get("ok") is False
        or str(result.get("status") or "").lower() in {"error", "failed", "denied"}
    ):
        return {
            "index": index,
            "id": str(call_id or ""),
            "name": resolved_name,
            "status": "error",
            "duration": duration,
            "error": _json_safe(result.get("error") or result.get("content") or "Tool failed."),
            "result": _json_safe(result),
        }
    return {
        "index": index,
        "id": str(call_id or ""),
        "name": resolved_name,
        "status": "ok",
        "duration": duration,
        "result": _json_safe(result),
    }


def parallel_tools(calls, max_concurrency=4, _context=None):
    started_at = time.time()
    context = _context if isinstance(_context, dict) else {}
    if not isinstance(calls, list) or not calls:
        return {
            "status": "error",
            "count": 0,
            "duration": round(max(time.time() - started_at, 0.0), 6),
            "results": [],
            "error": "calls must be a non-empty list.",
        }

    try:
        concurrency = int(max_concurrency if max_concurrency is not None else 4)
    except Exception:
        concurrency = 4
    concurrency = max(1, min(concurrency, 8))

    normalized_calls = []
    for index, item in enumerate(calls):
        if not isinstance(item, dict):
            normalized_calls.append(
                {
                    "index": index,
                    "id": "",
                    "name": "",
                    "args": {},
                    "invalid": "Each call must be an object with name and args.",
                }
            )
            continue
        normalized_calls.append(
            {
                "index": index,
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "").strip(),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "invalid": "" if str(item.get("name") or "").strip() else "Each call must include a tool name.",
            }
        )

    results = [None] * len(normalized_calls)
    futures = {}
    with ThreadPoolExecutor(max_workers=min(concurrency, len(normalized_calls))) as executor:
        for item in normalized_calls:
            index = item["index"]
            if item["invalid"]:
                results[index] = _build_parallel_tool_error(
                    index,
                    item["id"],
                    item["name"],
                    item["invalid"],
                )
                continue
            future = executor.submit(
                contextvars.copy_context().run,
                _execute_parallel_subcall,
                index,
                item["id"],
                item["name"],
                item["args"],
                context,
            )
            futures[future] = index
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                item = normalized_calls[index]
                results[index] = _build_parallel_tool_error(
                    index,
                    item["id"],
                    item["name"],
                    f"parallel_tools worker failed: {exc}",
                )

    ordered_results = [item for item in results if item is not None]
    ok_count = sum(1 for item in ordered_results if item.get("status") == "ok")
    total_count = len(ordered_results)
    overall_status = "ok"
    if ok_count == 0 and total_count > 0:
        overall_status = "error"
    elif ok_count < total_count:
        overall_status = "partial_error"
    payload = {
        "status": overall_status,
        "count": total_count,
        "duration": round(max(time.time() - started_at, 0.0), 6),
        "results": ordered_results,
    }
    content_parts = []
    for item in ordered_results:
        parts = tool_image_content_parts(item.get("result"))
        if not parts:
            continue
        content_parts.append({
            "type": "text",
            "text": f"Tool result {item['index'] + 1}: {item['name']} (id={item['id']})",
        })
        content_parts.extend(_json_copy(parts, []))
    if content_parts:
        payload["content_parts"] = content_parts
    return payload


def update_experience(scope, experience, previous_experience=None, _context=None):
    """Persist one verified lesson in the current user's applicable memory."""
    import logging
    from core.memory_store import MemoryStore, memory_project_from_context
    from core.clarify_mode import normalize_run_mode
    context = _context or {}
    log = logging.getLogger("memory.experience")
    session = context.get("session_id") or ""
    log.info("experience_started session=%s scope=%s", session, scope)
    try:
        if normalize_run_mode((context.get("run_context") or {}).get("mode")) != "execution":
            raise ValueError("拷问阶段仅可读取记忆，请进入执行阶段后再判断是否记录。")
        if scope not in {"project", "global"}:
            raise ValueError("scope 必须是 project 或 global。")
        config = context.get("config_manager")
        if config is None:
            raise ValueError("无法确定当前用户的记忆存储。")
        project = memory_project_from_context(context) if scope == "project" else ""
        if scope == "project" and not project:
            raise ValueError("当前任务未绑定项目，未写入记忆；不要自动改写全局记忆。")
        action = MemoryStore(config.get_chat_history_dir()).update_experience(
            experience, "workspace" if project else "global", project, previous_experience)
        log.info("experience_finished session=%s scope=%s action=%s", session, scope, action)
        return {"status": "ok", "scope": scope, "action": action,
                "content": "经验未变化。" if action == "unchanged" else "经验已保存，从下一轮任务生效。"}
    except Exception as exc:
        log.warning("experience_failed session=%s scope=%s reason=%s", session, scope, type(exc).__name__)
        return {"status": "error", "scope": scope, "error": str(exc),
                "content": "经验未保存，原有记忆保留。请核对原因后重试，不要通过文件或命令工具绕过。"}


TOOL_EXPORTS = [
    {
        "name": "parallel_tools",
        "handler": parallel_tools,
        "description": "Execute multiple independent read-only tools concurrently and return ordered results.",
        "parameters": {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "description": "Independent read-only tool calls to execute concurrently.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Optional client-specified identifier for the call."},
                            "name": {"type": "string", "description": "Tool name to execute."},
                            "args": {"type": "object", "description": "Arguments passed to the tool."},
                        },
                        "required": ["name"],
                    },
                },
                "max_concurrency": {
                    "type": "integer",
                    "description": "Maximum number of worker threads to use. Defaults to 4 and is capped at 8.",
                },
            },
            "required": ["calls"],
        },
        "kind": "meta_parallel",
        "read_only": True,
        "destructive": False,
        "allowed_modes": ["execution"],
        "always_load": True,
        "should_defer": False,
        "search_hint": "parallel concurrent read-only tools files search",
    },
    {
        "name": "update_experience",
        "handler": update_experience,
        "description": "Autonomously save a verified, reusable lesson to project or global memory during execution. Read memory first and merge existing lessons. Never edits Skills.",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["project", "global"], "description": "Project-specific or reusable across projects; the host resolves the current project."},
                "experience": {"type": "string", "description": "One concise paragraph without line breaks: applicability, action and verified evidence."},
                "previous_experience": {"type": "string", "description": "Exact old experience text (without the Markdown bullet) to replace after reading memory. Omit to add."},
            },
            "required": ["scope", "experience"],
            "additionalProperties": False,
        },
        "kind": "meta_learning",
        "read_only": False,
        "allowed_modes": ["execution"],
        "search_hint": "experience lessons learned project global memory",
    },
]
