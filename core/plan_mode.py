import json


PLAN_MODE_DISABLED = "disabled"
PLAN_MODE_EXPLORING = "exploring"
PLAN_MODE_AWAITING_USER_INPUT = "awaiting_user_input"
PLAN_MODE_READY_TO_PRESENT = "ready_to_present"

PLAN_PHASES = {
    PLAN_MODE_DISABLED,
    PLAN_MODE_EXPLORING,
    PLAN_MODE_AWAITING_USER_INPUT,
    PLAN_MODE_READY_TO_PRESENT,
}

RUN_MODE_NORMAL = "normal"
RUN_MODE_PLANNING = "planning"
RUN_MODE_EXECUTION = "execution"

RUN_MODES = {
    RUN_MODE_NORMAL,
    RUN_MODE_PLANNING,
    RUN_MODE_EXECUTION,
}

PLAN_PROTOCOL_VERSION = 2

PLAN_DETAIL_LEVELS = {"quick", "standard", "detailed"}
DEFAULT_PLAN_CONFIG = {"detail_level": "standard"}

PLANNING_INTERACTION_TOOLS = (
    "tool_search",
    "request_user_input",
)

PLANNING_READ_TOOLS = (
    "list_files",
    "read_file",
    "glob",
    "grep",
    "read_docx",
    "read_pptx",
    "read_excel",
    "read_pdf",
    "search_files",
    "search_codebase",
    "read_memories",
)

PLANNING_ALLOWED_TOOLS = set(PLANNING_INTERACTION_TOOLS) | set(PLANNING_READ_TOOLS)


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


def normalize_run_mode(value, default=RUN_MODE_EXECUTION):
    text = str(value or "").strip().lower()
    if text == RUN_MODE_NORMAL:
        return RUN_MODE_EXECUTION
    if text in RUN_MODES:
        return text
    return default


def normalize_plan_document(value):
    return str(value or "").strip()


def _normalize_question_options(options):
    normalized = []
    for item in options or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        if not label:
            continue
        normalized.append({"label": label, "description": description})
    return normalized


def normalize_pending_plan_questions(questions):
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


def normalize_plan_mode_state(value):
    return normalize_plan_phase(value, default=PLAN_MODE_EXPLORING)


def normalize_run_context(run_context):
    ctx = dict(run_context or {})
    return {
        "mode": normalize_run_mode(ctx.get("mode")),
        "plan_config": normalize_plan_config(ctx.get("plan_config")),
        "plan_protocol_version": int(ctx.get("plan_protocol_version") or PLAN_PROTOCOL_VERSION),
        "plan_mode_state": normalize_plan_mode_state(ctx.get("plan_mode_state")),
        "plan_document": normalize_plan_document(ctx.get("plan_document")),
        "pending_plan_questions": normalize_pending_plan_questions(
            ctx.get("pending_plan_questions")
        ),
        "selected_model_id": str(ctx.get("selected_model_id") or "").strip(),
        "im_provider": str(ctx.get("im_provider") or "").strip().lower(),
        "channel": str(ctx.get("channel") or "").strip().lower(),
    }


def derive_plan_phase(
    plan_mode_enabled,
    plan_mode_state="",
    plan_document="",
    explicit_phase="",
):
    if not plan_mode_enabled:
        return PLAN_MODE_DISABLED
    phase = normalize_plan_phase(explicit_phase, default="")
    if phase:
        return phase
    state = normalize_plan_mode_state(plan_mode_state)
    if state:
        if state == PLAN_MODE_DISABLED:
            return PLAN_MODE_EXPLORING
        return state
    if normalize_plan_document(plan_document):
        return PLAN_MODE_READY_TO_PRESENT
    return PLAN_MODE_EXPLORING


def is_planning_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_PLANNING


def is_execution_mode(run_context):
    return normalize_run_mode((run_context or {}).get("mode")) == RUN_MODE_EXECUTION


def get_planning_read_tools(available_tool_names):
    available = {
        text for text in (str(name or "").strip() for name in (available_tool_names or [])) if text
    }
    return [name for name in PLANNING_READ_TOOLS if name in available]


def is_tool_allowed_in_planning(tool_name):
    return str(tool_name or "").strip() in PLANNING_ALLOWED_TOOLS
