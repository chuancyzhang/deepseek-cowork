import calendar
import json
import re
import time
import uuid
from datetime import datetime, timedelta

from .clarify_mode import normalize_selected_skill_names

try:
    from croniter import croniter
except Exception:
    croniter = None


AUTOMATION_SCHEDULE_ONCE = "once"
AUTOMATION_SCHEDULE_CRON = "cron"
AUTOMATION_SCHEDULE_DAILY = "daily"
AUTOMATION_SCHEDULE_WEEKLY = "weekly"
AUTOMATION_SCHEDULE_MONTHLY = "monthly"
AUTOMATION_SCHEDULE_INTERVAL = "interval"

AUTOMATION_SCHEDULE_TYPES = {
    AUTOMATION_SCHEDULE_ONCE,
    AUTOMATION_SCHEDULE_CRON,
    AUTOMATION_SCHEDULE_DAILY,
    AUTOMATION_SCHEDULE_WEEKLY,
    AUTOMATION_SCHEDULE_MONTHLY,
    AUTOMATION_SCHEDULE_INTERVAL,
}

AUTOMATION_HISTORY_STATUS_RUNNING = "running"
AUTOMATION_HISTORY_STATUS_COMPLETED = "completed"
AUTOMATION_HISTORY_STATUS_ERROR = "error"
AUTOMATION_HISTORY_STATUS_INTERRUPTED = "interrupted"
AUTOMATION_HISTORY_STATUS_MISSED = "missed"

AUTOMATION_HISTORY_STATUSES = {
    AUTOMATION_HISTORY_STATUS_RUNNING,
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


def _replace_cron_aliases(text, aliases):
    result = str(text or "")
    for alias, value in (aliases or {}).items():
        result = re.sub(rf"\b{re.escape(alias)}\b", str(value), result)
    return result


def _parse_cron_part(part, min_value, max_value, names=None):
    text = str(part or "").strip().lower()
    if names:
        text = _replace_cron_aliases(text, names)
    if not text:
        raise ValueError("empty cron field")
    values = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ValueError("empty cron item")
        step = 1
        if "/" in chunk:
            chunk, step_text = chunk.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be greater than 0")
        if chunk == "*":
            start, end = min_value, max_value
        elif "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(chunk)
        if start < min_value or end > max_value or start > end:
            raise ValueError("cron value is out of range")
        for value in range(start, end + 1, step):
            values.add(value)
    return values


def normalize_cron_expression(value, fallback="0 9 * * *"):
    text = str(value or "").strip()
    if not text:
        text = fallback
    parts = text.split()
    if len(parts) != 5:
        return fallback
    try:
        _parse_cron_part(parts[0], 0, 59)
        _parse_cron_part(parts[1], 0, 23)
        _parse_cron_part(parts[2], 1, 31)
        _parse_cron_part(parts[3], 1, 12)
        _parse_cron_part(parts[4], 0, 7)
    except Exception:
        return fallback
    return " ".join(parts)


def validate_cron_expression(value):
    text = str(value or "").strip()
    return bool(text and normalize_cron_expression(text, fallback="") == text)


def cron_expression_from_legacy_schedule(task):
    source = dict(task or {})
    schedule_type = str(source.get("schedule_type") or AUTOMATION_SCHEDULE_DAILY).strip().lower()
    _time_text, hour, minute = _parse_time_of_day(source.get("time_of_day"))
    if schedule_type == AUTOMATION_SCHEDULE_WEEKLY:
        weekdays = _normalize_weekdays(source.get("weekdays"))
        cron_weekdays = [str((weekday + 1) % 7) for weekday in weekdays]
        return f"{minute} {hour} * * {','.join(cron_weekdays)}"
    if schedule_type == AUTOMATION_SCHEDULE_MONTHLY:
        try:
            day_of_month = max(1, min(int(source.get("day_of_month") or 1), 31))
        except Exception:
            day_of_month = 1
        return f"{minute} {hour} {day_of_month} * *"
    if schedule_type == AUTOMATION_SCHEDULE_INTERVAL:
        try:
            interval_minutes = max(1, int(source.get("interval_minutes") or 60))
        except Exception:
            interval_minutes = 60
        if interval_minutes < 60:
            return f"*/{interval_minutes} * * * *"
        if interval_minutes % 60 == 0 and interval_minutes // 60 <= 23:
            return f"0 */{interval_minutes // 60} * * *"
        return "*/59 * * * *"
    return f"{minute} {hour} * * *"


def _cron_matches(candidate, expression):
    parts = expression.split()
    minute_values = _parse_cron_part(parts[0], 0, 59)
    hour_values = _parse_cron_part(parts[1], 0, 23)
    day_values = _parse_cron_part(parts[2], 1, 31)
    month_values = _parse_cron_part(parts[3], 1, 12)
    weekday_values = _parse_cron_part(parts[4], 0, 7)
    cron_weekday = (candidate.weekday() + 1) % 7
    weekday_match = cron_weekday in weekday_values or (cron_weekday == 0 and 7 in weekday_values)
    return (
        candidate.minute in minute_values
        and candidate.hour in hour_values
        and candidate.day in day_values
        and candidate.month in month_values
        and weekday_match
    )


def compute_next_cron_run_at(expression, now_ts=None, after_ts=None):
    now_ts = int(now_ts or time.time())
    base_ts = int(after_ts if after_ts is not None else now_ts)
    cron_expression = normalize_cron_expression(expression)
    base_dt = datetime.fromtimestamp(base_ts)
    if croniter:
        return int(croniter(cron_expression, base_dt).get_next(datetime).timestamp())
    candidate = base_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_minutes = 366 * 24 * 60 * 5
    for _index in range(max_minutes):
        if _cron_matches(candidate, cron_expression):
            return int(candidate.timestamp())
        candidate += timedelta(minutes=1)
    return int((base_dt + timedelta(days=1)).timestamp())


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

    if schedule_type == AUTOMATION_SCHEDULE_CRON:
        return compute_next_cron_run_at(source.get("cron_expression"), now_ts=now_ts, after_ts=base_ts)

    cron_expression = str(source.get("cron_expression") or "").strip()
    if cron_expression and schedule_type in {
        AUTOMATION_SCHEDULE_DAILY,
        AUTOMATION_SCHEDULE_WEEKLY,
        AUTOMATION_SCHEDULE_MONTHLY,
    }:
        return compute_next_cron_run_at(cron_expression, now_ts=now_ts, after_ts=base_ts)

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
    if schedule_type == AUTOMATION_SCHEDULE_CRON:
        expression = normalize_cron_expression(source.get("cron_expression"))
        return f"Cron · {expression}"
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


def normalize_automation_task(task, valid_agent_profile_ids=None, now_ts=None):
    source = dict(task or {})
    valid_agent_profile_ids = set(valid_agent_profile_ids or [])
    now_ts = int(now_ts or time.time())
    name = str(source.get("name") or "").strip()
    prompt = str(source.get("prompt") or "").strip()
    legacy_template_id = str(source.get("template_id") or "").strip()
    legacy_template_name = str(source.get("template_name") or "").strip()
    legacy_requires_prompt = bool(legacy_template_id and not prompt)
    if legacy_requires_prompt:
        name = name or legacy_template_name or "旧版自动化任务"
        prompt = (
            "这个任务来自已下线的旧版 SOP 自动化模板。"
            "请编辑任务，把原流程目标改写成可直接执行的提示词后再启用。"
        )
    if not name or not prompt:
        return None
    agent_profile_id = str(source.get("agent_profile_id") or source.get("default_agent_profile_id") or "").strip()
    if valid_agent_profile_ids and agent_profile_id not in valid_agent_profile_ids:
        agent_profile_id = ""
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
        "prompt": prompt,
        "skill_names": normalize_selected_skill_names(
            source.get("skill_names") or source.get("selected_skill_names")
        ),
        "agent_profile_id": agent_profile_id,
        "enabled": False if legacy_requires_prompt else bool(source.get("enabled", True)),
        "schedule_type": schedule_type,
        "cron_expression": normalize_cron_expression(
            source.get("cron_expression")
            or (cron_expression_from_legacy_schedule(source) if schedule_type != AUTOMATION_SCHEDULE_ONCE else "")
        ),
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
    migration_note = str(source.get("migration_note") or "").strip()
    if legacy_requires_prompt:
        migration_note = (
            f"旧版 SOP 模板已下线，原 template_id={legacy_template_id}。"
            "任务已停用，请补充提示词后再启用。"
        )
    if migration_note:
        normalized["migration_note"] = migration_note
    if legacy_requires_prompt:
        normalized["enabled"] = False
    try:
        next_run_at = int(source.get("next_run_at") or 0)
    except Exception:
        next_run_at = 0
    normalized["next_run_at"] = next_run_at or compute_next_run_at(normalized, now_ts=now_ts)
    normalized["schedule_summary"] = describe_schedule(normalized)
    return normalized


def normalize_automation_tasks(value, valid_agent_profile_ids=None, now_ts=None):
    tasks = value if isinstance(value, list) else []
    normalized = []
    used_ids = set()
    for task in tasks:
        entry = normalize_automation_task(task, valid_agent_profile_ids=valid_agent_profile_ids, now_ts=now_ts)
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
        "agent_profile_id": str(source.get("agent_profile_id") or "").strip(),
        "agent_profile_name": str(source.get("agent_profile_name") or "").strip(),
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


def make_automation_history_record(task, agent_profile=None, status=AUTOMATION_HISTORY_STATUS_RUNNING, trigger_source="manual", session_id="", scheduled_at=0, summary="", error=""):
    agent_profile = dict(agent_profile or {})
    task = dict(task or {})
    now_ts = int(time.time())
    return normalize_automation_history_record(
        {
            "task_id": task.get("id") or "",
            "task_name": task.get("name") or "",
            "agent_profile_id": task.get("agent_profile_id") or agent_profile.get("id") or "",
            "agent_profile_name": agent_profile.get("name") or "",
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


def build_automation_execution_prompt(task):
    task = dict(task or {})
    lines = [
        "# 自动化任务",
        f"任务名称: {task.get('name') or '未命名自动化'}",
    ]
    custom_prompt = str(task.get("prompt") or "").strip()
    if custom_prompt:
        lines.extend(["", "任务提示词:", custom_prompt])
    lines.append("")
    lines.append("请直接完成上述自动化任务；如果无法继续，请明确说明原因和需要用户处理的事项。")
    return "\n".join(lines)
