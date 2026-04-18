import json


PLAN_MODE_DISABLED = "disabled"
PLAN_MODE_DRAFTING = "drafting"
PLAN_MODE_READY = "ready"
PLAN_MODE_EXECUTING = "executing"
PLAN_MODE_COMPLETED = "completed"

PLAN_PHASES = {
    PLAN_MODE_DISABLED,
    PLAN_MODE_DRAFTING,
    PLAN_MODE_READY,
    PLAN_MODE_EXECUTING,
    PLAN_MODE_COMPLETED,
}

PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_READY = "ready"
PLAN_STATUS_EXECUTING = "executing"
PLAN_STATUS_COMPLETED = "completed"

PLAN_STATUSES = {
    PLAN_STATUS_DRAFT,
    PLAN_STATUS_READY,
    PLAN_STATUS_EXECUTING,
    PLAN_STATUS_COMPLETED,
}

STEP_STATUS_PENDING = "pending"
STEP_STATUS_IN_PROGRESS = "in_progress"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_BLOCKED = "blocked"
STEP_STATUS_SKIPPED = "skipped"

STEP_STATUSES = {
    STEP_STATUS_PENDING,
    STEP_STATUS_IN_PROGRESS,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_BLOCKED,
    STEP_STATUS_SKIPPED,
}

RUN_MODE_NORMAL = "normal"
RUN_MODE_PLANNING = "planning"
RUN_MODE_EXECUTION = "execution"

RUN_MODES = {
    RUN_MODE_NORMAL,
    RUN_MODE_PLANNING,
    RUN_MODE_EXECUTION,
}

PLAN_DETAIL_LEVELS = {"quick", "standard", "detailed"}
DEFAULT_PLAN_CONFIG = {"detail_level": "standard"}

PLANNING_ALLOWED_TOOLS = {
    "update_execution_plan",
    "request_user_approval",
    "request_user_input",
    "list_files",
    "read_file",
    "glob",
    "grep",
    "read_docx",
    "read_pptx",
    "read_excel",
    "read_pdf",
    "read_memories",
}


def json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def normalize_plan_detail_level(value):
    text = str(value or "").strip().lower()
    if text in PLAN_DETAIL_LEVELS:
        return text
    return DEFAULT_PLAN_CONFIG["detail_level"]


def normalize_plan_config(config):
    cfg = dict(config or {})
    return {
        "detail_level": normalize_plan_detail_level(cfg.get("detail_level")),
    }


def normalize_plan_phase(value, default=PLAN_MODE_DISABLED):
    text = str(value or "").strip().lower()
    if text in PLAN_PHASES:
        return text
    return default


def normalize_plan_status(value, default=PLAN_STATUS_DRAFT):
    text = str(value or "").strip().lower()
    if text in PLAN_STATUSES:
        return text
    return default


def normalize_step_status(value, default=STEP_STATUS_PENDING):
    text = str(value or "").strip().lower()
    if text in STEP_STATUSES:
        return text
    return default


def normalize_run_mode(value, default=RUN_MODE_NORMAL):
    text = str(value or "").strip().lower()
    if text in RUN_MODES:
        return text
    return default


def normalize_execution_plan(plan):
    if not isinstance(plan, dict):
        return {}
    title = str(plan.get("title") or "").strip()
    if not title:
        return {}
    normalized = {
        "title": title,
        "summary": str(plan.get("summary") or "").strip(),
        "plan_status": normalize_plan_status(plan.get("plan_status")),
        "current_step_id": str(plan.get("current_step_id") or "").strip(),
        "note": str(plan.get("note") or "").strip(),
        "steps": [],
    }
    seen_ids = set()
    for index, raw_step in enumerate(plan.get("steps") or [], start=1):
        if not isinstance(raw_step, dict):
            continue
        step_id = str(raw_step.get("id") or f"step-{index}").strip()
        if not step_id or step_id in seen_ids:
            continue
        seen_ids.add(step_id)
        title_text = str(raw_step.get("title") or "").strip()
        if not title_text:
            title_text = f"步骤 {index}"
        tool_ids = []
        for tool_id in raw_step.get("tool_ids") or []:
            tool_text = str(tool_id or "").strip()
            if tool_text and tool_text not in tool_ids:
                tool_ids.append(tool_text)
        normalized["steps"].append(
            {
                "id": step_id,
                "title": title_text,
                "description": str(raw_step.get("description") or "").strip(),
                "success_criteria": str(raw_step.get("success_criteria") or "").strip(),
                "status": normalize_step_status(raw_step.get("status")),
                "tool_ids": tool_ids,
            }
        )
    if normalized["current_step_id"] and normalized["current_step_id"] not in {
        step["id"] for step in normalized["steps"]
    }:
        normalized["current_step_id"] = ""
    return normalized


def normalize_run_context(run_context):
    ctx = dict(run_context or {})
    normalized = {
        "mode": normalize_run_mode(ctx.get("mode")),
        "plan_config": normalize_plan_config(ctx.get("plan_config")),
        "confirmed_plan": normalize_execution_plan(ctx.get("confirmed_plan")),
        "active_plan_step_id": str(ctx.get("active_plan_step_id") or "").strip(),
    }
    if normalized["active_plan_step_id"] and normalized["confirmed_plan"]:
        valid_ids = {step["id"] for step in normalized["confirmed_plan"].get("steps") or []}
        if normalized["active_plan_step_id"] not in valid_ids:
            normalized["active_plan_step_id"] = ""
    return normalized


def derive_plan_phase(plan_mode_enabled, draft_plan=None, confirmed_plan=None, explicit_phase=""):
    if not plan_mode_enabled:
        return PLAN_MODE_DISABLED
    phase = normalize_plan_phase(explicit_phase, default="")
    if phase:
        return phase
    confirmed = normalize_execution_plan(confirmed_plan)
    if confirmed:
        status = normalize_plan_status(confirmed.get("plan_status"))
        if status == PLAN_STATUS_COMPLETED:
            return PLAN_MODE_COMPLETED
        return PLAN_MODE_EXECUTING
    draft = normalize_execution_plan(draft_plan)
    if draft:
        status = normalize_plan_status(draft.get("plan_status"))
        if status == PLAN_STATUS_READY:
            return PLAN_MODE_READY
        return PLAN_MODE_DRAFTING
    return PLAN_MODE_DRAFTING


def is_planning_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_PLANNING


def is_execution_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_EXECUTION


def is_tool_allowed_in_planning(tool_name):
    return str(tool_name or "").strip() in PLANNING_ALLOWED_TOOLS
