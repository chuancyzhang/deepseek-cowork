import json
import time

from core.automation_manager import normalize_automation_task
from core.sop_manager import normalize_sop_template


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _config_manager(_context):
    cfg = (_context or {}).get("config_manager") if isinstance(_context, dict) else None
    if not cfg:
        raise ValueError("config_manager is required")
    return cfg


def _approval_response(message, *, title, details="", _context=None):
    from skills.interaction.impl import request_user_approval

    return request_user_approval(
        message,
        title=title,
        details=details,
        _context=_context,
    )


def _is_approved(payload):
    response = payload.get("interaction_response") if isinstance(payload, dict) else None
    return bool((response or {}).get("approved"))


def _find_template(templates, identifier):
    ident = str(identifier or "").strip()
    if not ident:
        return None, -1
    for index, item in enumerate(templates):
        if str(item.get("id") or "").strip() == ident:
            return item, index
    for index, item in enumerate(templates):
        if str(item.get("name") or "").strip() == ident:
            return item, index
    return None, -1


def _find_task(tasks, identifier):
    ident = str(identifier or "").strip()
    if not ident:
        return None, -1
    for index, item in enumerate(tasks):
        if str(item.get("id") or "").strip() == ident:
            return item, index
    for index, item in enumerate(tasks):
        if str(item.get("name") or "").strip() == ident:
            return item, index
    return None, -1


def _resolve_template_reference(config_manager, payload):
    template_id = str((payload or {}).get("template_id") or "").strip()
    template_name = str((payload or {}).get("template_name") or "").strip()
    if template_id:
        template = config_manager.get_sop_template(template_id)
        if template:
            return template
    if template_name:
        template = config_manager.get_sop_template(template_name)
        if template:
            return template
    return None


def _summarize_template(item, include_steps=False):
    payload = {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description") or "",
        "trigger_count": len(item.get("triggers") or []),
        "skill_names": list(item.get("skill_names") or []),
        "advance_mode": item.get("advance_mode") or "manual",
        "step_count": len(item.get("steps") or []),
        "default_agent_profile_id": item.get("default_agent_profile_id") or "",
        "updated_at": item.get("updated_at") or 0,
    }
    if include_steps:
        payload["steps"] = _json_copy(item.get("steps"), [])
    return payload


def _summarize_task(item, config_manager=None):
    template = None
    if config_manager:
        template = config_manager.get_sop_template(item.get("template_id"))
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description") or "",
        "template_id": item.get("template_id") or "",
        "template_name": (template or {}).get("name") or "",
        "enabled": bool(item.get("enabled")),
        "schedule_type": item.get("schedule_type") or "",
        "schedule_summary": item.get("schedule_summary") or "",
        "next_run_at": item.get("next_run_at") or 0,
        "last_run_at": item.get("last_run_at") or 0,
        "prompt": item.get("prompt") or "",
    }


def list_automation_templates(include_steps=False, _context=None):
    config_manager = _config_manager(_context)
    templates = config_manager.get_sop_templates()
    return {
        "status": "ok",
        "count": len(templates),
        "items": [_summarize_template(item, include_steps=bool(include_steps)) for item in templates],
        "content": f"Found {len(templates)} automation template(s).",
    }


def upsert_automation_template(template, match="", _context=None):
    config_manager = _config_manager(_context)
    if not isinstance(template, dict):
        return {"status": "error", "error": "template must be an object."}
    templates = config_manager.get_sop_templates()
    existing = None
    existing_index = -1
    for candidate in (
        template.get("id"),
        match,
        template.get("name"),
    ):
        existing, existing_index = _find_template(templates, candidate)
        if existing:
            break
    merged = _json_copy(existing, {}) if existing else {}
    merged.update(_json_copy(template, {}))
    if existing:
        merged["id"] = existing.get("id")
        merged["created_at"] = existing.get("created_at")
    normalized = normalize_sop_template(merged)
    if not normalized:
        return {"status": "error", "error": "template is invalid or empty."}
    if existing_index >= 0:
        templates[existing_index] = normalized
        action = "updated"
    else:
        templates.append(normalized)
        action = "created"
    config_manager.set_sop_templates(templates)
    saved = config_manager.get_sop_template(normalized.get("id")) or normalized
    return {
        "status": "ok",
        "action": action,
        "item": _summarize_template(saved, include_steps=True),
        "content": f"Automation template '{saved.get('name')}' {action}.",
    }


def list_automation_tasks(include_disabled=True, _context=None):
    config_manager = _config_manager(_context)
    tasks = config_manager.get_automation_tasks()
    if not include_disabled:
        tasks = [item for item in tasks if item.get("enabled")]
    return {
        "status": "ok",
        "count": len(tasks),
        "items": [_summarize_task(item, config_manager=config_manager) for item in tasks],
        "content": f"Found {len(tasks)} automation task(s).",
    }


def upsert_automation_task(task, match="", _context=None):
    config_manager = _config_manager(_context)
    if not isinstance(task, dict):
        return {"status": "error", "error": "task must be an object."}
    tasks = config_manager.get_automation_tasks()
    existing = None
    existing_index = -1
    for candidate in (
        task.get("id"),
        match,
        task.get("name"),
    ):
        existing, existing_index = _find_task(tasks, candidate)
        if existing:
            break
    merged = _json_copy(existing, {}) if existing else {}
    merged.update(_json_copy(task, {}))
    template = _resolve_template_reference(config_manager, merged)
    if not template:
        return {
            "status": "error",
            "error": "task must reference an existing template via template_id or template_name.",
        }
    merged["template_id"] = template.get("id")
    merged.pop("template_name", None)
    if existing:
        merged["id"] = existing.get("id")
        merged["created_at"] = existing.get("created_at")
    elif "enabled" not in merged:
        merged["enabled"] = False
    merged["updated_at"] = int(time.time())
    normalized = normalize_automation_task(
        merged,
        valid_template_ids=[template.get("id")],
    )
    if not normalized:
        return {"status": "error", "error": "task is invalid."}
    if existing_index >= 0:
        tasks[existing_index] = normalized
        action = "updated"
    else:
        tasks.append(normalized)
        action = "created"
    config_manager.set_automation_tasks(tasks)
    saved = config_manager.get_automation_task(normalized.get("id")) or normalized
    return {
        "status": "ok",
        "action": action,
        "item": _summarize_task(saved, config_manager=config_manager),
        "content": f"Automation task '{saved.get('name')}' {action}.",
    }


def set_automation_task_enabled(task_id_or_name, enabled, _context=None):
    config_manager = _config_manager(_context)
    tasks = config_manager.get_automation_tasks()
    task, index = _find_task(tasks, task_id_or_name)
    if index < 0 or not task:
        return {"status": "error", "error": f"Automation task '{task_id_or_name}' not found."}
    updated = _json_copy(task, {})
    updated["enabled"] = bool(enabled)
    updated["updated_at"] = int(time.time())
    tasks[index] = updated
    config_manager.set_automation_tasks(tasks)
    saved = config_manager.get_automation_task(updated.get("id")) or updated
    return {
        "status": "ok",
        "item": _summarize_task(saved, config_manager=config_manager),
        "content": f"Automation task '{saved.get('name')}' {'enabled' if saved.get('enabled') else 'paused'}.",
    }


def delete_automation_task(task_id_or_name, _context=None):
    config_manager = _config_manager(_context)
    tasks = config_manager.get_automation_tasks()
    task, index = _find_task(tasks, task_id_or_name)
    if index < 0 or not task:
        return {"status": "error", "error": f"Automation task '{task_id_or_name}' not found."}
    approval = _approval_response(
        f"删除自动化任务“{task.get('name') or task_id_or_name}”？",
        title="删除自动化任务",
        details="删除后将不再按计划触发该任务。",
        _context=_context,
    )
    if not _is_approved(approval):
        return {
            "status": "cancelled",
            "item": _summarize_task(task, config_manager=config_manager),
            "approval": approval,
            "content": f"Deletion cancelled for automation task '{task.get('name')}'.",
        }
    del tasks[index]
    config_manager.set_automation_tasks(tasks)
    return {
        "status": "ok",
        "item": _summarize_task(task, config_manager=config_manager),
        "approval": approval,
        "content": f"Automation task '{task.get('name')}' deleted.",
    }


def run_automation_task_now(task_id_or_name, _context=None):
    config_manager = _config_manager(_context)
    task = config_manager.get_automation_task(task_id_or_name)
    if not task:
        return {"status": "error", "error": f"Automation task '{task_id_or_name}' not found."}
    approval = _approval_response(
        f"立即运行自动化任务“{task.get('name') or task_id_or_name}”？",
        title="运行自动化任务",
        details="任务会创建或切换到对应会话并立刻开始执行当前流程。",
        _context=_context,
    )
    if not _is_approved(approval):
        return {
            "status": "cancelled",
            "item": _summarize_task(task, config_manager=config_manager),
            "approval": approval,
            "content": f"Run cancelled for automation task '{task.get('name')}'.",
        }
    runner = (_context or {}).get("automation_runner") if isinstance(_context, dict) else None
    if not callable(runner):
        return {
            "status": "error",
            "item": _summarize_task(task, config_manager=config_manager),
            "approval": approval,
            "error": "automation_runner is unavailable in the current runtime.",
            "content": "The task is configured, but direct execution is unavailable in the current runtime.",
        }
    result = runner(task.get("id"))
    return {
        "status": "ok" if result else "error",
        "item": _summarize_task(task, config_manager=config_manager),
        "approval": approval,
        "launched": bool(result),
        "content": f"Automation task '{task.get('name')}' started." if result else f"Automation task '{task.get('name')}' failed to start.",
    }


def list_automation_run_history(limit=20, status_filter="", _context=None):
    config_manager = _config_manager(_context)
    history = config_manager.get_automation_run_history()
    wanted = str(status_filter or "").strip().lower()
    if wanted:
        history = [item for item in history if str(item.get("status") or "").strip().lower() == wanted]
    try:
        limit_value = max(1, int(limit or 20))
    except Exception:
        limit_value = 20
    history = history[:limit_value]
    return {
        "status": "ok",
        "count": len(history),
        "items": _json_copy(history, []),
        "content": f"Found {len(history)} automation run record(s).",
    }


TOOL_EXPORTS = [
    {
        "name": "list_automation_templates",
        "handler": list_automation_templates,
        "description": "List available automation SOP templates.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_steps": {"type": "boolean", "description": "Whether to include full step details."}
            },
            "required": [],
        },
        "read_only": True,
        "allowed_modes": ["clarifying", "execution"],
        "search_hint": "automation sop template list schedule workflow",
    },
    {
        "name": "upsert_automation_template",
        "handler": upsert_automation_template,
        "description": "Create or update an automation SOP template.",
        "parameters": {
            "type": "object",
            "properties": {
                "template": {"type": "object", "description": "Template payload to create or update."},
                "match": {"type": "string", "description": "Optional id or exact name to match an existing template."},
            },
            "required": ["template"],
        },
        "allowed_modes": ["execution"],
        "search_hint": "automation sop template create update workflow",
    },
    {
        "name": "list_automation_tasks",
        "handler": list_automation_tasks,
        "description": "List configured scheduled automation tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_disabled": {"type": "boolean", "description": "Whether to include paused tasks."}
            },
            "required": [],
        },
        "read_only": True,
        "allowed_modes": ["clarifying", "execution"],
        "search_hint": "automation scheduled task list cron",
    },
    {
        "name": "upsert_automation_task",
        "handler": upsert_automation_task,
        "description": "Create or update a scheduled automation task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "object", "description": "Task payload to create or update."},
                "match": {"type": "string", "description": "Optional id or exact name to match an existing task."},
            },
            "required": ["task"],
        },
        "allowed_modes": ["execution"],
        "search_hint": "automation scheduled task create update cron daily weekly",
    },
    {
        "name": "set_automation_task_enabled",
        "handler": set_automation_task_enabled,
        "description": "Enable or pause an existing automation task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id_or_name": {"type": "string", "description": "Task id or exact name."},
                "enabled": {"type": "boolean", "description": "True to enable, false to pause."},
            },
            "required": ["task_id_or_name", "enabled"],
        },
        "allowed_modes": ["execution"],
        "search_hint": "automation task enable pause disable",
    },
    {
        "name": "delete_automation_task",
        "handler": delete_automation_task,
        "description": "Delete an automation task after approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id_or_name": {"type": "string", "description": "Task id or exact name."}
            },
            "required": ["task_id_or_name"],
        },
        "allowed_modes": ["execution"],
        "search_hint": "automation task delete remove",
    },
    {
        "name": "run_automation_task_now",
        "handler": run_automation_task_now,
        "description": "Run an automation task immediately after approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id_or_name": {"type": "string", "description": "Task id or exact name."}
            },
            "required": ["task_id_or_name"],
        },
        "allowed_modes": ["execution"],
        "search_hint": "automation task run now trigger execute",
    },
    {
        "name": "list_automation_run_history",
        "handler": list_automation_run_history,
        "description": "List recent automation run history entries.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of run records to return."},
                "status_filter": {"type": "string", "description": "Optional exact status filter."},
            },
            "required": [],
        },
        "read_only": True,
        "allowed_modes": ["clarifying", "execution"],
        "search_hint": "automation history runs status",
    },
]
