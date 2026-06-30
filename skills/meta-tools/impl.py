import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


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
    if "run_context" in child_context:
        child_context["run_context"] = _json_copy(child_context.get("run_context"), {})
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
    return {
        "status": overall_status,
        "count": total_count,
        "duration": round(max(time.time() - started_at, 0.0), 6),
        "results": ordered_results,
    }


def update_experience(skill_name=None, experience=None, description=None, instructions=None, tool_name=None, task_type=None, error_pattern=None, tags=None, _context=None):
    """
    Update the experience/lessons learned, description, or instructions for a specific skill.

    Args:
        skill_name (str, optional): Name of the skill to update. If omitted, record into general-experience.
        experience (str, optional): New lesson learned (appended to existing).
        description (str, optional): New skill description (replaces existing).
        instructions (str, optional): New usage instructions/body (replaces existing).
    """
    if not _context:
        return "Error: Context not available."

    skill_manager = _context.get("skill_manager")
    if not skill_manager:
        return "Error: SkillManager not found in context."

    updates = []

    if description or instructions:
        target_skill = skill_name or "general-experience"
        success, message = skill_manager.update_skill(
            target_skill,
            description=description,
            instructions=instructions,
        )
        if not success:
            return f"Failed to update '{target_skill}': {message}"
        if description:
            updates.append("description")
        if instructions:
            updates.append("instructions")

    if experience:
        success, message = skill_manager.record_experience(
            experience_text=experience,
            skill_name=skill_name,
            tool_name=tool_name,
            task_type=task_type,
            error_pattern=error_pattern,
            tags=tags if isinstance(tags, list) else None,
            source="meta_tool",
        )
        if not success:
            target_skill = skill_name or "general-experience"
            return f"Failed to update '{target_skill}': {message}"
        updates.append("experience")

    if not updates:
        return "No changes requested."

    skill_manager.load_skills()
    target_name = skill_name or "general-experience"
    return f"Successfully updated '{target_name}': {', '.join(updates)}"


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
        "description": "Record lessons learned or update a skill's guidance based on execution feedback.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill to update."},
                "experience": {"type": "string", "description": "Concise lesson learned to append."},
                "description": {"type": "string", "description": "Replacement description for the target skill."},
                "instructions": {"type": "string", "description": "Replacement markdown instructions for the target skill."},
                "tool_name": {"type": "string", "description": "Tool associated with the lesson."},
                "task_type": {"type": "string", "description": "Task category for the lesson."},
                "error_pattern": {"type": "string", "description": "Error signature associated with the lesson."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for the lesson."},
            },
            "required": [],
        },
        "kind": "meta_learning",
        "search_hint": "experience lessons learned improve skill guidance",
    },
]
