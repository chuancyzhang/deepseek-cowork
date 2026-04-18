from core.plan_mode import (
    PLAN_STATUS_DRAFT,
    STEP_STATUS_PENDING,
    json_copy,
    normalize_execution_plan,
    normalize_plan_status,
    normalize_step_status,
)


def update_execution_plan(
    title,
    steps,
    summary="",
    plan_status="draft",
    current_step_id="",
    note="",
    _context=None,
):
    normalized_steps = []
    for index, raw_step in enumerate(steps or [], start=1):
        if not isinstance(raw_step, dict):
            continue
        normalized_steps.append(
            {
                "id": str(raw_step.get("id") or f"step-{index}").strip(),
                "title": str(raw_step.get("title") or "").strip(),
                "description": str(raw_step.get("description") or "").strip(),
                "success_criteria": str(raw_step.get("success_criteria") or "").strip(),
                "status": normalize_step_status(raw_step.get("status"), default=STEP_STATUS_PENDING),
                "tool_ids": list(raw_step.get("tool_ids") or []),
            }
        )
    plan = normalize_execution_plan(
        {
            "title": title,
            "summary": summary,
            "plan_status": normalize_plan_status(plan_status, default=PLAN_STATUS_DRAFT),
            "current_step_id": current_step_id,
            "note": note,
            "steps": normalized_steps,
        }
    )
    plan_event = {
        "title": plan.get("title") or str(title or "").strip(),
        "plan_status": plan.get("plan_status") or normalize_plan_status(plan_status, default=PLAN_STATUS_DRAFT),
        "current_step_id": plan.get("current_step_id") or "",
        "step_count": len(plan.get("steps") or []),
        "summary": plan.get("summary") or str(summary or "").strip(),
        "note": plan.get("note") or str(note or "").strip(),
    }
    message_parts = [
        f"计划已更新：{plan_event['title']}",
        f"状态：{plan_event['plan_status']}",
        f"步骤数：{plan_event['step_count']}",
    ]
    if plan_event["current_step_id"]:
        message_parts.append(f"当前步骤：{plan_event['current_step_id']}")
    if plan_event["summary"]:
        message_parts.append(plan_event["summary"])
    if plan_event["note"]:
        message_parts.append(plan_event["note"])
    content = " | ".join(message_parts)
    return {
        "source_tool": "update_execution_plan",
        "content": content,
        "plan": json_copy(plan, {}),
        "plan_event": plan_event,
        "content_parts": [
            {
                "type": "tool_event",
                "tool_name": "update_execution_plan",
                "status": "completed",
                "summary": content,
            }
        ],
    }


TOOL_EXPORTS = [
    {
        "name": "update_execution_plan",
        "handler": update_execution_plan,
        "description": "Create or update the structured execution plan used by planning mode.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Plan title."},
                "summary": {"type": "string", "description": "Plan summary."},
                "plan_status": {
                    "type": "string",
                    "description": "Plan status: draft, ready, executing, completed.",
                },
                "current_step_id": {
                    "type": "string",
                    "description": "Current active step id, if any.",
                },
                "note": {"type": "string", "description": "Optional short note about this plan update."},
                "steps": {
                    "type": "array",
                    "description": "Structured plan steps.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "success_criteria": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["id", "title", "status"],
                    },
                },
            },
            "required": ["title", "steps"],
        },
        "kind": "explicit_tool",
        "result_format": "structured_plan_update",
    }
]
