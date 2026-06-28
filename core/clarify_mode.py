import json
from .sop_manager import normalize_sop_run


CLARIFY_MODE_DISABLED = "disabled"
CLARIFY_MODE_EXPLORING = "exploring"
CLARIFY_MODE_AWAITING_USER_INPUT = "awaiting_user_input"

CLARIFY_PHASES = {
    CLARIFY_MODE_DISABLED,
    CLARIFY_MODE_EXPLORING,
    CLARIFY_MODE_AWAITING_USER_INPUT,
}

RUN_MODE_NORMAL = "normal"
RUN_MODE_CLARIFYING = "clarifying"
RUN_MODE_EXECUTION = "execution"

# Backward-compatible input value only. New code should use RUN_MODE_CLARIFYING.
RUN_MODE_PLANNING = "planning"

RUN_MODES = {
    RUN_MODE_NORMAL,
    RUN_MODE_CLARIFYING,
    RUN_MODE_EXECUTION,
    RUN_MODE_PLANNING,
}

CLARIFY_INTERACTION_TOOLS = (
    "tool_search",
    "request_user_input",
)

CLARIFY_READ_TOOLS = (
    "workspace_list_files",
    "text_file_read",
    "glob",
    "grep",
    "document_read",
    "search_files",
    "search_codebase",
    "read_memories",
)

CLARIFY_ALLOWED_TOOLS = set(CLARIFY_INTERACTION_TOOLS) | set(CLARIFY_READ_TOOLS)

WORKFLOW_MODE_OFFICE_HTML_FIRST = "office_html_first"
WORKFLOW_MODE_OFFICE_FILE_CONVERSION = "office_file_conversion"
WORKFLOW_MODES = {
    "",
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
    WORKFLOW_MODE_OFFICE_FILE_CONVERSION,
}

OFFICE_OUTPUT_PROFILE_FREE = "free"
OFFICE_OUTPUT_PROFILE_PPT = "ppt"
OFFICE_OUTPUT_PROFILE_DESIGN = "design"
OFFICE_OUTPUT_PROFILE_DOCX = "docx"
OFFICE_OUTPUT_PROFILES = {
    OFFICE_OUTPUT_PROFILE_FREE,
    OFFICE_OUTPUT_PROFILE_PPT,
    OFFICE_OUTPUT_PROFILE_DESIGN,
    OFFICE_OUTPUT_PROFILE_DOCX,
}


def json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def normalize_clarify_phase(value, default=CLARIFY_MODE_DISABLED):
    text = str(value or "").strip().lower()
    if text in CLARIFY_PHASES:
        return text
    return default


def normalize_run_mode(value, default=RUN_MODE_EXECUTION):
    text = str(value or "").strip().lower()
    if text == RUN_MODE_NORMAL:
        return RUN_MODE_EXECUTION
    if text == RUN_MODE_PLANNING:
        return RUN_MODE_CLARIFYING
    if text in RUN_MODES:
        return text
    return default


def _normalize_question_options(options):
    normalized = []
    for item in options or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("value") or "").strip()
        value = str(item.get("value") or label).strip()
        description = str(item.get("description") or "").strip()
        if not label:
            continue
        payload = {"label": label, "description": description}
        if value:
            payload["value"] = value
        normalized.append(payload)
    return normalized


def normalize_pending_clarify_questions(questions):
    normalized = []
    seen_ids = set()
    for item in questions or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        if not qid or qid in seen_ids:
            continue
        seen_ids.add(qid)
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        normalized.append(
            {
                "header": str(item.get("header") or "").strip(),
                "id": qid,
                "question": question,
                "options": _normalize_question_options(item.get("options")),
            }
        )
    return normalized


def normalize_clarify_mode_state(value):
    return normalize_clarify_phase(value, default=CLARIFY_MODE_EXPLORING)


def normalize_selected_skill_names(values):
    normalized = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def normalize_workflow_mode(value):
    text = str(value or "").strip().lower()
    return text if text in WORKFLOW_MODES else ""


def normalize_office_output_profile(value):
    text = str(value or "").strip().lower()
    return text if text in OFFICE_OUTPUT_PROFILES else OFFICE_OUTPUT_PROFILE_FREE


def normalize_run_context(run_context):
    ctx = dict(run_context or {})
    pending_questions = ctx.get("pending_clarify_questions")
    if pending_questions is None:
        pending_questions = ctx.get("pending_plan_questions")
    state = ctx.get("clarify_mode_state")
    if state is None:
        state = ctx.get("plan_mode_state")
    return {
        "mode": normalize_run_mode(ctx.get("mode")),
        "clarify_mode_state": normalize_clarify_mode_state(state),
        "pending_clarify_questions": normalize_pending_clarify_questions(
            pending_questions
        ),
        "selected_skill_names": normalize_selected_skill_names(
            ctx.get("selected_skill_names")
        ),
        "allowed_skill_names": normalize_selected_skill_names(
            ctx.get("allowed_skill_names")
        ),
        "agent_profile_id": str(ctx.get("agent_profile_id") or "").strip(),
        "agent_profile_name": str(ctx.get("agent_profile_name") or "").strip(),
        "agent_description": str(ctx.get("agent_description") or "").strip(),
        "agent_system_prompt": str(ctx.get("agent_system_prompt") or "").strip(),
        "agent_summon_source": str(ctx.get("agent_summon_source") or "").strip(),
        "selected_model_id": str(ctx.get("selected_model_id") or "").strip(),
        "reasoning_effort": str(ctx.get("reasoning_effort") or "").strip().lower(),
        "im_provider": str(ctx.get("im_provider") or "").strip().lower(),
        "channel": str(ctx.get("channel") or "").strip().lower(),
        "workspace_mode": "chat_only" if str(ctx.get("workspace_mode") or "").strip().lower() == "chat_only" else "project",
        "workflow_mode": normalize_workflow_mode(ctx.get("workflow_mode")),
        "office_output_profile": normalize_office_output_profile(
            ctx.get("office_output_profile")
        ),
        "sop_run": normalize_sop_run(ctx.get("sop_run")),
    }


def derive_clarify_phase(
    clarify_mode_enabled,
    clarify_mode_state="",
    explicit_phase="",
):
    if not clarify_mode_enabled:
        return CLARIFY_MODE_DISABLED
    phase = normalize_clarify_phase(explicit_phase, default="")
    if phase:
        return phase
    state = normalize_clarify_mode_state(clarify_mode_state)
    if state == CLARIFY_MODE_DISABLED:
        return CLARIFY_MODE_EXPLORING
    return state or CLARIFY_MODE_EXPLORING


def is_clarifying_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_CLARIFYING


def is_execution_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_EXECUTION


def get_clarifying_read_tools(available_tool_names):
    available = {
        text for text in (str(name or "").strip() for name in (available_tool_names or [])) if text
    }
    return [name for name in CLARIFY_READ_TOOLS if name in available]


def is_tool_allowed_in_clarifying(tool_name):
    return str(tool_name or "").strip() in CLARIFY_ALLOWED_TOOLS
