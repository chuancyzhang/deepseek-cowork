import calendar
import json
import time
import uuid
from datetime import datetime, timedelta


AUTOMATION_SCHEDULE_ONCE = "once"
AUTOMATION_SCHEDULE_DAILY = "daily"
AUTOMATION_SCHEDULE_WEEKLY = "weekly"
AUTOMATION_SCHEDULE_MONTHLY = "monthly"
AUTOMATION_SCHEDULE_INTERVAL = "interval"

AUTOMATION_SCHEDULE_TYPES = {
    AUTOMATION_SCHEDULE_ONCE,
    AUTOMATION_SCHEDULE_DAILY,
    AUTOMATION_SCHEDULE_WEEKLY,
    AUTOMATION_SCHEDULE_MONTHLY,
    AUTOMATION_SCHEDULE_INTERVAL,
}

AUTOMATION_HISTORY_STATUS_RUNNING = "running"
AUTOMATION_HISTORY_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
AUTOMATION_HISTORY_STATUS_COMPLETED = "completed"
AUTOMATION_HISTORY_STATUS_ERROR = "error"
AUTOMATION_HISTORY_STATUS_INTERRUPTED = "interrupted"
AUTOMATION_HISTORY_STATUS_MISSED = "missed"

AUTOMATION_HISTORY_STATUSES = {
    AUTOMATION_HISTORY_STATUS_RUNNING,
    AUTOMATION_HISTORY_STATUS_AWAITING_CONFIRMATION,
    AUTOMATION_HISTORY_STATUS_COMPLETED,
    AUTOMATION_HISTORY_STATUS_ERROR,
    AUTOMATION_HISTORY_STATUS_INTERRUPTED,
    AUTOMATION_HISTORY_STATUS_MISSED,
}

DEFAULT_AUTOMATION_TIMER_INTERVAL_MS = 30000
AUTOMATION_RUN_GRACE_SECONDS = 90
AUTOMATION_HISTORY_LIMIT = 400


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _parse_time_of_day(value, fallback="09:00"):
    text = str(value or "").strip()
    if not text:
        text = fallback
    parts = text.split(":", 1)
    try:
        hour = max(0, min(int(parts[0]), 23))
    except Exception:
        hour = 9
    try:
        minute = max(0, min(int(parts[1]), 59))
    except Exception:
        minute = 0
    return f"{hour:02d}:{minute:02d}", hour, minute


def _normalize_weekdays(values):
    normalized = []
    seen = set()
    source = values if isinstance(values, list) else []
    for item in source:
        try:
            weekday = int(item)
        except Exception:
            continue
        if weekday < 0 or weekday > 6 or weekday in seen:
            continue
        seen.add(weekday)
        normalized.append(weekday)
    return sorted(normalized) or [0]


def _last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]


def _candidate_for_month(year, month, day, hour, minute):
    day = max(1, min(int(day or 1), _last_day_of_month(year, month)))
    return datetime(year, month, day, hour, minute)


def compute_next_run_at(task, now_ts=None, after_ts=None):
    source = dict(task or {})
    now_ts = int(now_ts or time.time())
    base_ts = int(after_ts if after_ts is not None else now_ts)
    schedule_type = str(source.get("schedule_type") or AUTOMATION_SCHEDULE_DAILY).strip().lower()
    if schedule_type not in AUTOMATION_SCHEDULE_TYPES:
        schedule_type = AUTOMATION_SCHEDULE_DAILY

    if schedule_type == AUTOMATION_SCHEDULE_ONCE:
        try:
            one_time_at = int(source.get("one_time_at") or 0)
        except Exception:
            one_time_at = 0
        return one_time_at

    if schedule_type == AUTOMATION_SCHEDULE_INTERVAL:
        try:
            interval_minutes = max(1, int(source.get("interval_minutes") or 60))
        except Exception:
            interval_minutes = 60
        try:
            anchor_at = int(source.get("interval_anchor_at") or source.get("created_at") or now_ts)
        except Exception:
            anchor_at = now_ts
        interval_seconds = interval_minutes * 60
        if base_ts < anchor_at:
            return anchor_at
        elapsed = max(0, base_ts - anchor_at)
        return anchor_at + ((elapsed // interval_seconds) + 1) * interval_seconds

    time_text, hour, minute = _parse_time_of_day(source.get("time_of_day"))
    current = datetime.fromtimestamp(base_ts)
    today_candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if schedule_type == AUTOMATION_SCHEDULE_DAILY:
        if today_candidate.timestamp() > base_ts:
            return int(today_candidate.timestamp())
        return int((today_candidate + timedelta(days=1)).timestamp())

    if schedule_type == AUTOMATION_SCHEDULE_WEEKLY:
        weekdays = _normalize_weekdays(source.get("weekdays"))
        for offset in range(8):
            candidate_day = current + timedelta(days=offset)
            if candidate_day.weekday() not in weekdays:
                continue
            candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.timestamp() > base_ts:
                return int(candidate.timestamp())
        candidate_day = current + timedelta(days=7)
        while candidate_day.weekday() not in weekdays:
            candidate_day += timedelta(days=1)
        candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return int(candidate.timestamp())

    if schedule_type == AUTOMATION_SCHEDULE_MONTHLY:
        try:
            day_of_month = max(1, min(int(source.get("day_of_month") or 1), 31))
        except Exception:
            day_of_month = 1
        candidate = _candidate_for_month(current.year, current.month, day_of_month, hour, minute)
        if candidate.timestamp() > base_ts:
            return int(candidate.timestamp())
        year = current.year
        month = current.month + 1
        if month > 12:
            year += 1
            month = 1
        return int(_candidate_for_month(year, month, day_of_month, hour, minute).timestamp())

    return int(today_candidate.timestamp()) if today_candidate.timestamp() > base_ts else int((today_candidate + timedelta(days=1)).timestamp())


def describe_schedule(task):
    source = dict(task or {})
    schedule_type = str(source.get("schedule_type") or AUTOMATION_SCHEDULE_DAILY).strip().lower()
    if schedule_type == AUTOMATION_SCHEDULE_ONCE:
        try:
            one_time_at = int(source.get("one_time_at") or 0)
        except Exception:
            one_time_at = 0
        if not one_time_at:
            return "单次"
        return datetime.fromtimestamp(one_time_at).strftime("单次 · %Y-%m-%d %H:%M")
    if schedule_type == AUTOMATION_SCHEDULE_INTERVAL:
        try:
            interval_minutes = max(1, int(source.get("interval_minutes") or 60))
        except Exception:
            interval_minutes = 60
        return f"每隔 {interval_minutes} 分钟"
    time_text, _hour, _minute = _parse_time_of_day(source.get("time_of_day"))
    if schedule_type == AUTOMATION_SCHEDULE_WEEKLY:
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        labels = [weekday_map[index] for index in _normalize_weekdays(source.get("weekdays"))]
        return f"每周 {'/'.join(labels)} {time_text}"
    if schedule_type == AUTOMATION_SCHEDULE_MONTHLY:
        try:
            day_of_month = max(1, min(int(source.get("day_of_month") or 1), 31))
        except Exception:
            day_of_month = 1
        return f"每月 {day_of_month} 日 {time_text}"
    return f"每天 {time_text}"


def normalize_automation_task(task, valid_template_ids=None, now_ts=None):
    source = dict(task or {})
    valid_template_ids = set(valid_template_ids or [])
    now_ts = int(now_ts or time.time())
    name = str(source.get("name") or "").strip()
    template_id = str(source.get("template_id") or "").strip()
    if not name or not template_id:
        return None
    if valid_template_ids and template_id not in valid_template_ids:
        return None
    schedule_type = str(source.get("schedule_type") or AUTOMATION_SCHEDULE_DAILY).strip().lower()
    if schedule_type not in AUTOMATION_SCHEDULE_TYPES:
        schedule_type = AUTOMATION_SCHEDULE_DAILY
    created_at = int(source.get("created_at") or now_ts)
    updated_at = int(source.get("updated_at") or created_at or now_ts)
    time_text, _hour, _minute = _parse_time_of_day(source.get("time_of_day"))
    try:
        one_time_at = int(source.get("one_time_at") or 0)
    except Exception:
        one_time_at = 0
    try:
        interval_minutes = max(1, int(source.get("interval_minutes") or 60))
    except Exception:
        interval_minutes = 60
    try:
        interval_anchor_at = int(source.get("interval_anchor_at") or created_at)
    except Exception:
        interval_anchor_at = created_at
    try:
        day_of_month = max(1, min(int(source.get("day_of_month") or 1), 31))
    except Exception:
        day_of_month = 1
    normalized = {
        "id": str(source.get("id") or f"auto-{uuid.uuid4().hex[:8]}").strip(),
        "name": name,
        "template_id": template_id,
        "prompt": str(source.get("prompt") or "").strip(),
        "enabled": bool(source.get("enabled", True)),
        "schedule_type": schedule_type,
        "time_of_day": time_text,
        "weekdays": _normalize_weekdays(source.get("weekdays")),
        "day_of_month": day_of_month,
        "interval_minutes": interval_minutes,
        "interval_anchor_at": interval_anchor_at,
        "one_time_at": one_time_at,
        "created_at": created_at,
        "updated_at": updated_at,
        "last_run_at": int(source.get("last_run_at") or 0),
        "last_missed_at": int(source.get("last_missed_at") or 0),
        "last_history_id": str(source.get("last_history_id") or "").strip(),
        "description": str(source.get("description") or "").strip(),
    }
    try:
        next_run_at = int(source.get("next_run_at") or 0)
    except Exception:
        next_run_at = 0
    normalized["next_run_at"] = next_run_at or compute_next_run_at(normalized, now_ts=now_ts)
    normalized["schedule_summary"] = describe_schedule(normalized)
    return normalized


def normalize_automation_tasks(value, valid_template_ids=None, now_ts=None):
    tasks = value if isinstance(value, list) else []
    normalized = []
    used_ids = set()
    for task in tasks:
        entry = normalize_automation_task(task, valid_template_ids=valid_template_ids, now_ts=now_ts)
        if not entry:
            continue
        task_id = entry["id"]
        base_id = task_id
        suffix = 2
        while task_id in used_ids:
            task_id = f"{base_id}-{suffix}"
            suffix += 1
        entry["id"] = task_id
        used_ids.add(task_id)
        normalized.append(entry)
    return normalized


def normalize_automation_history_record(record):
    source = dict(record or {})
    status = str(source.get("status") or AUTOMATION_HISTORY_STATUS_COMPLETED).strip().lower()
    if status not in AUTOMATION_HISTORY_STATUSES:
        status = AUTOMATION_HISTORY_STATUS_COMPLETED
    created_at = int(source.get("created_at") or source.get("started_at") or time.time())
    return {
        "id": str(source.get("id") or f"run-{uuid.uuid4().hex[:10]}").strip(),
        "task_id": str(source.get("task_id") or "").strip(),
        "task_name": str(source.get("task_name") or "").strip(),
        "template_id": str(source.get("template_id") or "").strip(),
        "template_name": str(source.get("template_name") or "").strip(),
        "session_id": str(source.get("session_id") or "").strip(),
        "trigger_source": str(source.get("trigger_source") or "manual").strip(),
        "status": status,
        "scheduled_at": int(source.get("scheduled_at") or 0),
        "started_at": int(source.get("started_at") or 0),
        "finished_at": int(source.get("finished_at") or 0),
        "summary": str(source.get("summary") or "").strip(),
        "error": str(source.get("error") or "").strip(),
        "created_at": created_at,
    }


def normalize_automation_history(value):
    records = value if isinstance(value, list) else []
    normalized = []
    used_ids = set()
    for record in records:
        entry = normalize_automation_history_record(record)
        history_id = entry["id"]
        base_id = history_id
        suffix = 2
        while history_id in used_ids:
            history_id = f"{base_id}-{suffix}"
            suffix += 1
        entry["id"] = history_id
        used_ids.add(history_id)
        normalized.append(entry)
    normalized.sort(key=lambda item: (item.get("started_at") or item.get("scheduled_at") or item.get("created_at") or 0), reverse=True)
    return normalized[:AUTOMATION_HISTORY_LIMIT]


def make_automation_history_record(task, template=None, status=AUTOMATION_HISTORY_STATUS_RUNNING, trigger_source="manual", session_id="", scheduled_at=0, summary="", error=""):
    template = dict(template or {})
    task = dict(task or {})
    now_ts = int(time.time())
    return normalize_automation_history_record(
        {
            "task_id": task.get("id") or "",
            "task_name": task.get("name") or "",
            "template_id": task.get("template_id") or "",
            "template_name": template.get("name") or "",
            "session_id": session_id,
            "trigger_source": trigger_source,
            "status": status,
            "scheduled_at": int(scheduled_at or 0),
            "started_at": now_ts if status == AUTOMATION_HISTORY_STATUS_RUNNING else 0,
            "finished_at": now_ts if status in {AUTOMATION_HISTORY_STATUS_COMPLETED, AUTOMATION_HISTORY_STATUS_ERROR, AUTOMATION_HISTORY_STATUS_INTERRUPTED, AUTOMATION_HISTORY_STATUS_MISSED} else 0,
            "summary": summary,
            "error": error,
            "created_at": now_ts,
        }
    )


def advance_task_to_next_run(task, now_ts=None, after_ts=None):
    normalized = _json_copy(task, {})
    if not normalized:
        return normalized
    now_ts = int(now_ts or time.time())
    normalized["updated_at"] = now_ts
    reference_ts = int(after_ts if after_ts is not None else now_ts)
    normalized["next_run_at"] = compute_next_run_at(normalized, now_ts=now_ts, after_ts=reference_ts)
    normalized["schedule_summary"] = describe_schedule(normalized)
    return normalized


def build_automation_execution_prompt(task, template):
    task = dict(task or {})
    template = dict(template or {})
    lines = [
        "# 自动化任务",
        f"任务名称: {task.get('name') or template.get('name') or '未命名自动化'}",
    ]
    description = str(template.get("description") or "").strip()
    if description:
        lines.append(f"模板目标: {description}")
    custom_prompt = str(task.get("prompt") or "").strip()
    if custom_prompt:
        lines.extend(["任务要求:", custom_prompt])
    lines.append("请按绑定的 SOP 状态机逐步执行当前步骤，直到流程完成、出现阻塞，或进入等待确认。")
    return "\n".join(lines)
