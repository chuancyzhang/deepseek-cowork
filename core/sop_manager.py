import json
import re
import time
import uuid


SOP_RUN_STATUS_ACTIVE = "active"
SOP_RUN_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
SOP_RUN_STATUS_COMPLETED = "completed"

SOP_STEP_STATUS_PENDING = "pending"
SOP_STEP_STATUS_RUNNING = "running"
SOP_STEP_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
SOP_STEP_STATUS_COMPLETED = "completed"
SOP_STEP_STATUS_SKIPPED = "skipped"

SOP_RUN_STATUSES = {
    SOP_RUN_STATUS_ACTIVE,
    SOP_RUN_STATUS_AWAITING_CONFIRMATION,
    SOP_RUN_STATUS_COMPLETED,
}

SOP_STEP_STATUSES = {
    SOP_STEP_STATUS_PENDING,
    SOP_STEP_STATUS_RUNNING,
    SOP_STEP_STATUS_AWAITING_CONFIRMATION,
    SOP_STEP_STATUS_COMPLETED,
    SOP_STEP_STATUS_SKIPPED,
}


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _slug(value):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
    return text or uuid.uuid4().hex[:8]


def _normalize_string_list(values):
    normalized = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def default_sop_templates():
    now = int(time.time())
    return [
        {
            "id": "office-file-first-placeholder",
            "name": "办公文件优先（示例）",
            "description": "占位示例模板。请按你的真实业务流程修改步骤内容后再正式使用。",
            "triggers": ["办公文件", "文档整理", "示例"],
            "skill_names": [],
            "default_agent_profile_id": "",
            "steps": [
                {
                    "title": "确认当前步骤目标",
                    "instructions": "先围绕用户当前任务确认这一小步要完成什么，缺少关键信息时先反问，不要提前执行后续流程。",
                    "success_criteria": "已经明确本步目标，或已经提出阻塞本步执行的必要问题。",
                    "allow_skip": False,
                },
                {
                    "title": "执行并等待确认",
                    "instructions": "只完成当前小步，并把产出或结论说明清楚，然后等待用户在 SOP 抽屉中确认是否推进。",
                    "success_criteria": "当前小步已经完成，且用户能据此判断确认、重跑或标记不适用。",
                    "allow_skip": True,
                },
            ],
            "created_at": now,
            "updated_at": now,
        }
    ]


def _normalize_sop_step(step, index=0):
    source = dict(step or {})
    title = str(source.get("title") or source.get("name") or "").strip()
    instructions = str(source.get("instructions") or source.get("prompt") or "").strip()
    success_criteria = str(source.get("success_criteria") or "").strip()
    if not title and not instructions and not success_criteria:
        return None
    return {
        "title": title or f"步骤 {index + 1}",
        "instructions": instructions,
        "success_criteria": success_criteria,
        "allow_skip": bool(source.get("allow_skip", False)),
    }


def normalize_sop_template(template, index=0, used_ids=None):
    used_ids = used_ids if used_ids is not None else set()
    source = dict(template or {})
    name = str(source.get("name") or source.get("display_name") or "").strip()
    if not name:
        return None
    template_id = str(source.get("id") or "").strip() or f"sop-{_slug(name)}"
    base_template_id = template_id
    suffix = 2
    while template_id in used_ids:
        template_id = f"{base_template_id}-{suffix}"
        suffix += 1
    used_ids.add(template_id)
    normalized_steps = []
    for step_index, step in enumerate(source.get("steps") or []):
        entry = _normalize_sop_step(step, step_index)
        if entry:
            normalized_steps.append(entry)
    now = int(time.time())
    created_at = int(source.get("created_at") or now)
    updated_at = int(source.get("updated_at") or created_at or now)
    return {
        "id": template_id,
        "name": name,
        "description": str(source.get("description") or "").strip(),
        "triggers": _normalize_string_list(source.get("triggers")),
        "skill_names": _normalize_string_list(
            source.get("skill_names") or source.get("selected_skill_names")
        ),
        "default_agent_profile_id": str(source.get("default_agent_profile_id") or "").strip(),
        "steps": normalized_steps,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def normalize_sop_templates(value):
    templates = value if isinstance(value, list) else []
    normalized = []
    used_ids = set()
    for index, template in enumerate(templates):
        entry = normalize_sop_template(template, index=index, used_ids=used_ids)
        if entry:
            normalized.append(entry)
    return normalized


def normalize_sop_run(run):
    source = dict(run or {})
    template_id = str(source.get("template_id") or "").strip()
    template_name = str(source.get("template_name") or "").strip()
    raw_steps = source.get("steps") if isinstance(source.get("steps"), list) else []
    steps = []
    for index, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "title": str(step.get("title") or f"步骤 {index + 1}").strip(),
                "instructions": str(step.get("instructions") or "").strip(),
                "success_criteria": str(step.get("success_criteria") or "").strip(),
                "allow_skip": bool(step.get("allow_skip", False)),
                "status": str(step.get("status") or SOP_STEP_STATUS_PENDING).strip()
                if str(step.get("status") or "").strip() in SOP_STEP_STATUSES
                else SOP_STEP_STATUS_PENDING,
                "confirmed_at": int(step.get("confirmed_at") or 0),
                "confirmation_action": str(step.get("confirmation_action") or "").strip(),
                "skip_reason": str(step.get("skip_reason") or "").strip(),
                "last_execution": _json_copy(step.get("last_execution"), {}),
            }
        )
    if not steps or not template_id or not template_name:
        return None
    try:
        current_step_index = int(source.get("current_step_index") or 0)
    except Exception:
        current_step_index = 0
    current_step_index = max(0, min(current_step_index, len(steps) - 1))
    status = str(source.get("status") or SOP_RUN_STATUS_ACTIVE).strip()
    if status not in SOP_RUN_STATUSES:
        status = SOP_RUN_STATUS_ACTIVE
    run_obj = {
        "template_id": template_id,
        "template_name": template_name,
        "template_description": str(source.get("template_description") or "").strip(),
        "template_skill_names": _normalize_string_list(source.get("template_skill_names")),
        "default_agent_profile_id": str(source.get("default_agent_profile_id") or "").strip(),
        "current_step_index": current_step_index,
        "status": status,
        "steps": steps,
        "confirmation_records": [],
        "last_execution": _json_copy(source.get("last_execution"), {}),
        "created_at": int(source.get("created_at") or int(time.time())),
        "updated_at": int(source.get("updated_at") or int(time.time())),
    }
    for record in source.get("confirmation_records") or []:
        if not isinstance(record, dict):
            continue
        try:
            step_index = int(record.get("step_index"))
        except Exception:
            continue
        action = str(record.get("action") or "").strip()
        if action not in {"confirm", "rerun", "skip"}:
            continue
        run_obj["confirmation_records"].append(
            {
                "step_index": step_index,
                "action": action,
                "reason": str(record.get("reason") or "").strip(),
                "ts": int(record.get("ts") or 0),
            }
        )
    if status == SOP_RUN_STATUS_COMPLETED:
        for step in run_obj["steps"]:
            if step["status"] == SOP_STEP_STATUS_PENDING:
                step["status"] = SOP_STEP_STATUS_COMPLETED
    return run_obj


def create_sop_run(template):
    normalized_template = normalize_sop_template(template)
    if not normalized_template or not normalized_template.get("steps"):
        return None
    now = int(time.time())
    steps = []
    for index, step in enumerate(normalized_template.get("steps") or []):
        steps.append(
            {
                "title": step.get("title") or f"步骤 {index + 1}",
                "instructions": step.get("instructions") or "",
                "success_criteria": step.get("success_criteria") or "",
                "allow_skip": bool(step.get("allow_skip", False)),
                "status": SOP_STEP_STATUS_PENDING,
                "confirmed_at": 0,
                "confirmation_action": "",
                "skip_reason": "",
                "last_execution": {},
            }
        )
    return normalize_sop_run(
        {
            "template_id": normalized_template.get("id"),
            "template_name": normalized_template.get("name"),
            "template_description": normalized_template.get("description"),
            "template_skill_names": normalized_template.get("skill_names"),
            "default_agent_profile_id": normalized_template.get("default_agent_profile_id"),
            "current_step_index": 0,
            "status": SOP_RUN_STATUS_ACTIVE,
            "steps": steps,
            "confirmation_records": [],
            "last_execution": {},
            "created_at": now,
            "updated_at": now,
        }
    )


def get_current_step(run):
    normalized = normalize_sop_run(run)
    if not normalized:
        return None
    return _get_current_step_ref(normalized)


def _get_current_step_ref(normalized_run):
    if not isinstance(normalized_run, dict):
        return None
    normalized = normalized_run
    index = normalized.get("current_step_index", 0)
    steps = normalized.get("steps") or []
    if index < 0 or index >= len(steps):
        return None
    return steps[index]


def is_sop_completed(run):
    normalized = normalize_sop_run(run)
    return bool(normalized and normalized.get("status") == SOP_RUN_STATUS_COMPLETED)


def is_sop_awaiting_confirmation(run):
    normalized = normalize_sop_run(run)
    return bool(
        normalized and normalized.get("status") == SOP_RUN_STATUS_AWAITING_CONFIRMATION
    )


def _touch_run(run):
    run["updated_at"] = int(time.time())
    return run


def mark_step_running(run, execution_info=None):
    normalized = normalize_sop_run(run)
    if not normalized or is_sop_completed(normalized):
        return normalized
    step = _get_current_step_ref(normalized)
    if not step:
        return normalized
    step["status"] = SOP_STEP_STATUS_RUNNING
    if execution_info is not None:
        info = _json_copy(step.get("last_execution"), {})
        info.update(_json_copy(execution_info, {}))
        step["last_execution"] = info
        normalized["last_execution"] = info
    normalized["status"] = SOP_RUN_STATUS_ACTIVE
    return _touch_run(normalized)


def mark_step_awaiting_confirmation(run, execution_info=None):
    normalized = normalize_sop_run(run)
    if not normalized or is_sop_completed(normalized):
        return normalized
    step = _get_current_step_ref(normalized)
    if not step:
        return normalized
    step["status"] = SOP_STEP_STATUS_AWAITING_CONFIRMATION
    if execution_info is not None:
        info = _json_copy(step.get("last_execution"), {})
        info.update(_json_copy(execution_info, {}))
        step["last_execution"] = info
        normalized["last_execution"] = info
    normalized["status"] = SOP_RUN_STATUS_AWAITING_CONFIRMATION
    return _touch_run(normalized)


def confirm_current_step(run, reason=""):
    normalized = normalize_sop_run(run)
    if not normalized:
        return normalized
    step = _get_current_step_ref(normalized)
    if not step:
        return normalized
    now = int(time.time())
    current_index = normalized.get("current_step_index", 0)
    step["status"] = SOP_STEP_STATUS_COMPLETED
    step["confirmed_at"] = now
    step["confirmation_action"] = "confirm"
    normalized["confirmation_records"].append(
        {
            "step_index": current_index,
            "action": "confirm",
            "reason": str(reason or "").strip(),
            "ts": now,
        }
    )
    if current_index + 1 >= len(normalized.get("steps") or []):
        normalized["status"] = SOP_RUN_STATUS_COMPLETED
    else:
        normalized["current_step_index"] = current_index + 1
        normalized["status"] = SOP_RUN_STATUS_ACTIVE
    return _touch_run(normalized)


def rerun_current_step(run, reason=""):
    normalized = normalize_sop_run(run)
    if not normalized:
        return normalized
    step = _get_current_step_ref(normalized)
    if not step:
        return normalized
    now = int(time.time())
    current_index = normalized.get("current_step_index", 0)
    normalized["confirmation_records"].append(
        {
            "step_index": current_index,
            "action": "rerun",
            "reason": str(reason or "").strip(),
            "ts": now,
        }
    )
    step["status"] = SOP_STEP_STATUS_PENDING
    step["confirmation_action"] = "rerun"
    normalized["status"] = SOP_RUN_STATUS_ACTIVE
    return _touch_run(normalized)


def skip_current_step(run, reason=""):
    normalized = normalize_sop_run(run)
    if not normalized:
        return normalized
    step = _get_current_step_ref(normalized)
    if not step:
        return normalized
    now = int(time.time())
    current_index = normalized.get("current_step_index", 0)
    step["status"] = SOP_STEP_STATUS_SKIPPED
    step["confirmed_at"] = now
    step["confirmation_action"] = "skip"
    step["skip_reason"] = str(reason or "").strip()
    normalized["confirmation_records"].append(
        {
            "step_index": current_index,
            "action": "skip",
            "reason": str(reason or "").strip(),
            "ts": now,
        }
    )
    if current_index + 1 >= len(normalized.get("steps") or []):
        normalized["status"] = SOP_RUN_STATUS_COMPLETED
    else:
        normalized["current_step_index"] = current_index + 1
        normalized["status"] = SOP_RUN_STATUS_ACTIVE
    return _touch_run(normalized)


def build_sop_prompt_fragment(run):
    normalized = normalize_sop_run(run)
    if not normalized:
        return ""
    step = get_current_step(normalized)
    if not step:
        return ""
    step_index = normalized.get("current_step_index", 0) + 1
    total_steps = len(normalized.get("steps") or [])
    lines = [
        "# SOP 当前步骤",
        f"当前 SOP: {normalized.get('template_name')}",
    ]
    description = normalized.get("template_description") or ""
    if description:
        lines.append(f"SOP 目标: {description}")
    lines.extend(
        [
            f"当前步骤: {step_index}/{total_steps} - {step.get('title') or '未命名步骤'}",
            "执行指令:",
            step.get("instructions") or "按当前步骤要求完成本轮任务。",
            "成功标准:",
            step.get("success_criteria") or "达到当前步骤目标后停止，并等待用户确认。",
            "严格规则:",
            "1. 本轮只允许完成当前步骤，不得提前执行任何后续步骤。",
            "2. 如果缺少完成当前步骤所需的关键信息，先提出必要问题。",
            "3. 当前步骤完成后，明确说明已完成本步，并等待用户在 SOP 抽屉中确认、重跑或标记不适用。",
        ]
    )
    return "\n".join(lines)
