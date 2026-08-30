import calendar
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta

from .clarify_mode import normalize_selected_skill_names
from .favorite_delivery import normalize_favorite_delivery

try:
    from croniter import croniter
except Exception:
    croniter = None


FAVORITE_EXECUTION_CHAT = "chat"
FAVORITE_EXECUTION_WORKSPACE = "workspace"
FAVORITE_EXECUTION_MODES = {FAVORITE_EXECUTION_CHAT, FAVORITE_EXECUTION_WORKSPACE}

FAVORITE_PROMPT_INHERIT = "inherit"
FAVORITE_PROMPT_CUSTOM = "custom"
FAVORITE_PROMPT_MODES = {FAVORITE_PROMPT_INHERIT, FAVORITE_PROMPT_CUSTOM}

FAVORITE_SCHEDULE_ONCE = "once"
FAVORITE_SCHEDULE_CRON = "cron"
FAVORITE_SCHEDULE_DAILY = "daily"
FAVORITE_SCHEDULE_WEEKLY = "weekly"
FAVORITE_SCHEDULE_MONTHLY = "monthly"
FAVORITE_SCHEDULE_INTERVAL = "interval"
FAVORITE_SCHEDULE_TYPES = {
    FAVORITE_SCHEDULE_ONCE,
    FAVORITE_SCHEDULE_CRON,
    FAVORITE_SCHEDULE_DAILY,
    FAVORITE_SCHEDULE_WEEKLY,
    FAVORITE_SCHEDULE_MONTHLY,
    FAVORITE_SCHEDULE_INTERVAL,
}

FAVORITE_RUN_STATUS_RUNNING = "running"
FAVORITE_RUN_STATUS_COMPLETED = "completed"
FAVORITE_RUN_STATUS_ERROR = "error"
FAVORITE_RUN_STATUS_INTERRUPTED = "interrupted"
FAVORITE_RUN_STATUS_MISSED = "missed"
FAVORITE_RUN_STATUSES = {
    FAVORITE_RUN_STATUS_RUNNING,
    FAVORITE_RUN_STATUS_COMPLETED,
    FAVORITE_RUN_STATUS_ERROR,
    FAVORITE_RUN_STATUS_INTERRUPTED,
    FAVORITE_RUN_STATUS_MISSED,
}

DEFAULT_FAVORITES_TIMER_INTERVAL_MS = 30000
FAVORITE_RUN_GRACE_SECONDS = 90
FAVORITE_RUN_HISTORY_LIMIT = 400


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _parse_time_of_day(value, fallback="09:00"):
    text = str(value or fallback).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError("执行时间必须使用 HH:mm 格式。")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("执行时间超出有效范围。")
    return f"{hour:02d}:{minute:02d}", hour, minute


def _normalize_weekdays(values):
    normalized = []
    for item in values if isinstance(values, list) else []:
        try:
            weekday = int(item)
        except Exception as exc:
            raise ValueError("星期必须是 0 到 6 的整数。") from exc
        if weekday < 0 or weekday > 6:
            raise ValueError("星期必须是 0 到 6 的整数。")
        if weekday not in normalized:
            normalized.append(weekday)
    return sorted(normalized) or [0]


def _last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]


def _candidate_for_month(year, month, day, hour, minute):
    day = max(1, min(int(day or 1), _last_day_of_month(year, month)))
    return datetime(year, month, day, hour, minute)


def _parse_cron_part(part, min_value, max_value):
    text = str(part or "").strip().lower()
    if not text:
        raise ValueError("Cron 字段不能为空。")
    values = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ValueError("Cron 项不能为空。")
        step = 1
        if "/" in chunk:
            chunk, step_text = chunk.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("Cron 步长必须大于 0。")
        if chunk == "*":
            start, end = min_value, max_value
        elif "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(chunk)
        if start < min_value or end > max_value or start > end:
            raise ValueError("Cron 数值超出范围。")
        values.update(range(start, end + 1, step))
    return values


def validate_cron_expression(value):
    text = str(value or "").strip()
    parts = text.split()
    if len(parts) != 5:
        return False
    try:
        _parse_cron_part(parts[0], 0, 59)
        _parse_cron_part(parts[1], 0, 23)
        _parse_cron_part(parts[2], 1, 31)
        _parse_cron_part(parts[3], 1, 12)
        _parse_cron_part(parts[4], 0, 7)
    except Exception:
        return False
    return True


def normalize_cron_expression(value):
    text = " ".join(str(value or "").strip().split())
    if not validate_cron_expression(text):
        raise ValueError("Cron 表达式无效，应包含 5 个字段。")
    return text


def cron_expression_from_schedule(schedule):
    source = dict(schedule or {})
    schedule_type = str(source.get("schedule_type") or FAVORITE_SCHEDULE_DAILY).strip().lower()
    _time_text, hour, minute = _parse_time_of_day(source.get("time_of_day"))
    if schedule_type == FAVORITE_SCHEDULE_WEEKLY:
        cron_weekdays = [str((weekday + 1) % 7) for weekday in _normalize_weekdays(source.get("weekdays"))]
        return f"{minute} {hour} * * {','.join(cron_weekdays)}"
    if schedule_type == FAVORITE_SCHEDULE_MONTHLY:
        day = int(source.get("day_of_month") or 1)
        if day < 1 or day > 31:
            raise ValueError("每月执行日期必须在 1 到 31 之间。")
        return f"{minute} {hour} {day} * *"
    if schedule_type == FAVORITE_SCHEDULE_INTERVAL:
        interval = int(source.get("interval_minutes") or 60)
        if interval < 1 or interval > 24 * 60:
            raise ValueError("执行间隔必须在 1 到 1440 分钟之间。")
        if interval < 60:
            return f"*/{interval} * * * *"
        if interval % 60 == 0 and interval // 60 <= 23:
            return f"0 */{interval // 60} * * *"
        return "*/59 * * * *"
    return f"{minute} {hour} * * *"


def _cron_matches(candidate, expression):
    parts = expression.split()
    minutes = _parse_cron_part(parts[0], 0, 59)
    hours = _parse_cron_part(parts[1], 0, 23)
    days = _parse_cron_part(parts[2], 1, 31)
    months = _parse_cron_part(parts[3], 1, 12)
    weekdays = _parse_cron_part(parts[4], 0, 7)
    cron_weekday = (candidate.weekday() + 1) % 7
    weekday_match = cron_weekday in weekdays or (cron_weekday == 0 and 7 in weekdays)
    return (
        candidate.minute in minutes
        and candidate.hour in hours
        and candidate.day in days
        and candidate.month in months
        and weekday_match
    )


def compute_next_cron_run_at(expression, now_ts=None, after_ts=None):
    expression = normalize_cron_expression(expression)
    now_ts = int(now_ts or time.time())
    base_ts = int(after_ts if after_ts is not None else now_ts)
    base_dt = datetime.fromtimestamp(base_ts)
    if croniter:
        return int(croniter(expression, base_dt).get_next(datetime).timestamp())
    candidate = base_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _index in range(366 * 24 * 60 * 5):
        if _cron_matches(candidate, expression):
            return int(candidate.timestamp())
        candidate += timedelta(minutes=1)
    raise ValueError("无法在五年范围内计算下一次 Cron 运行时间。")


def compute_next_run_at(schedule, now_ts=None, after_ts=None):
    source = dict(schedule or {})
    now_ts = int(now_ts or time.time())
    base_ts = int(after_ts if after_ts is not None else now_ts)
    schedule_type = str(source.get("schedule_type") or FAVORITE_SCHEDULE_DAILY).strip().lower()
    if schedule_type not in FAVORITE_SCHEDULE_TYPES:
        raise ValueError(f"未知的定时类型：{schedule_type}")
    if schedule_type == FAVORITE_SCHEDULE_ONCE:
        one_time_at = int(source.get("one_time_at") or 0)
        if one_time_at <= 0:
            raise ValueError("单次计划必须设置执行时间。")
        return one_time_at
    if schedule_type == FAVORITE_SCHEDULE_CRON:
        return compute_next_cron_run_at(source.get("cron_expression"), now_ts=now_ts, after_ts=base_ts)
    if schedule_type == FAVORITE_SCHEDULE_INTERVAL:
        interval_minutes = int(source.get("interval_minutes") or 60)
        if interval_minutes < 1 or interval_minutes > 24 * 60:
            raise ValueError("执行间隔必须在 1 到 1440 分钟之间。")
        anchor_at = int(source.get("interval_anchor_at") or source.get("created_at") or now_ts)
        interval_seconds = interval_minutes * 60
        if base_ts < anchor_at:
            return anchor_at
        return anchor_at + ((max(0, base_ts - anchor_at) // interval_seconds) + 1) * interval_seconds
    _time_text, hour, minute = _parse_time_of_day(source.get("time_of_day"))
    current = datetime.fromtimestamp(base_ts)
    today = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule_type == FAVORITE_SCHEDULE_DAILY:
        return int(today.timestamp()) if today.timestamp() > base_ts else int((today + timedelta(days=1)).timestamp())
    if schedule_type == FAVORITE_SCHEDULE_WEEKLY:
        weekdays = _normalize_weekdays(source.get("weekdays"))
        for offset in range(8):
            candidate_day = current + timedelta(days=offset)
            if candidate_day.weekday() in weekdays:
                candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate.timestamp() > base_ts:
                    return int(candidate.timestamp())
    if schedule_type == FAVORITE_SCHEDULE_MONTHLY:
        day = int(source.get("day_of_month") or 1)
        if day < 1 or day > 31:
            raise ValueError("每月执行日期必须在 1 到 31 之间。")
        candidate = _candidate_for_month(current.year, current.month, day, hour, minute)
        if candidate.timestamp() > base_ts:
            return int(candidate.timestamp())
        year, month = current.year, current.month + 1
        if month > 12:
            year, month = year + 1, 1
        return int(_candidate_for_month(year, month, day, hour, minute).timestamp())
    raise ValueError(f"无法计算定时类型：{schedule_type}")


def describe_schedule(schedule):
    source = dict(schedule or {})
    schedule_type = str(source.get("schedule_type") or FAVORITE_SCHEDULE_DAILY).strip().lower()
    if schedule_type == FAVORITE_SCHEDULE_ONCE:
        one_time_at = int(source.get("one_time_at") or 0)
        return datetime.fromtimestamp(one_time_at).strftime("单次 · %Y-%m-%d %H:%M") if one_time_at else "单次"
    if schedule_type == FAVORITE_SCHEDULE_INTERVAL:
        return f"每隔 {int(source.get('interval_minutes') or 60)} 分钟"
    if schedule_type == FAVORITE_SCHEDULE_CRON:
        return f"Cron · {normalize_cron_expression(source.get('cron_expression'))}"
    time_text, _hour, _minute = _parse_time_of_day(source.get("time_of_day"))
    if schedule_type == FAVORITE_SCHEDULE_WEEKLY:
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        labels = [weekday_map[index] for index in _normalize_weekdays(source.get("weekdays"))]
        return f"每周 {'/'.join(labels)} {time_text}"
    if schedule_type == FAVORITE_SCHEDULE_MONTHLY:
        return f"每月 {int(source.get('day_of_month') or 1)} 日 {time_text}"
    return f"每天 {time_text}"


def favorite_effective_prompt(favorite):
    source = dict(favorite or {})
    schedule = dict(source.get("schedule") or {})
    if str(schedule.get("prompt_mode") or FAVORITE_PROMPT_INHERIT) == FAVORITE_PROMPT_CUSTOM:
        return str(schedule.get("custom_prompt") or "").strip()
    return str(source.get("prompt") or "").strip()


def normalize_favorite_schedule(schedule, favorite_prompt="", now_ts=None):
    if schedule is None:
        return None
    source = dict(schedule or {})
    now_ts = int(now_ts or time.time())
    schedule_type = str(source.get("schedule_type") or FAVORITE_SCHEDULE_DAILY).strip().lower()
    if schedule_type not in FAVORITE_SCHEDULE_TYPES:
        raise ValueError(f"未知的定时类型：{schedule_type}")
    prompt_mode = str(source.get("prompt_mode") or FAVORITE_PROMPT_INHERIT).strip().lower()
    if prompt_mode not in FAVORITE_PROMPT_MODES:
        raise ValueError(f"未知的定时提示词模式：{prompt_mode}")
    custom_prompt = str(source.get("custom_prompt") or "").strip()
    if prompt_mode == FAVORITE_PROMPT_INHERIT and not str(favorite_prompt or "").strip():
        raise ValueError("当前常用项没有提示词，定时计划必须使用专用提示词。")
    if prompt_mode == FAVORITE_PROMPT_CUSTOM and not custom_prompt:
        raise ValueError("定时计划的专用提示词不能为空。")
    created_at = int(source.get("created_at") or now_ts)
    time_text = _parse_time_of_day(source.get("time_of_day"))[0]
    weekdays = _normalize_weekdays(source.get("weekdays"))
    day_of_month = int(source.get("day_of_month") or 1)
    if day_of_month < 1 or day_of_month > 31:
        raise ValueError("每月执行日期必须在 1 到 31 之间。")
    interval_minutes = int(source.get("interval_minutes") or 60)
    if interval_minutes < 1 or interval_minutes > 24 * 60:
        raise ValueError("执行间隔必须在 1 到 1440 分钟之间。")
    one_time_at = int(source.get("one_time_at") or 0)
    if schedule_type == FAVORITE_SCHEDULE_ONCE and one_time_at <= 0:
        raise ValueError("单次计划必须设置执行时间。")
    cron_expression = str(source.get("cron_expression") or "").strip()
    if schedule_type == FAVORITE_SCHEDULE_CRON:
        cron_expression = normalize_cron_expression(cron_expression)
    else:
        cron_expression = cron_expression_from_schedule(source)
    normalized = {
        "enabled": bool(source.get("enabled", False)),
        "prompt_mode": prompt_mode,
        "custom_prompt": custom_prompt,
        "schedule_type": schedule_type,
        "cron_expression": cron_expression,
        "time_of_day": time_text,
        "weekdays": weekdays,
        "day_of_month": day_of_month,
        "interval_minutes": interval_minutes,
        "interval_anchor_at": int(source.get("interval_anchor_at") or created_at),
        "one_time_at": one_time_at,
        "created_at": created_at,
        "updated_at": int(source.get("updated_at") or now_ts),
        "last_run_at": int(source.get("last_run_at") or 0),
        "last_missed_at": int(source.get("last_missed_at") or 0),
        "last_history_id": str(source.get("last_history_id") or "").strip(),
        "delivery": normalize_favorite_delivery(source.get("delivery")),
    }
    normalized["next_run_at"] = int(source.get("next_run_at") or 0) or compute_next_run_at(normalized, now_ts=now_ts)
    normalized["schedule_summary"] = describe_schedule(normalized)
    return normalized


def normalize_favorite(favorite, now_ts=None):
    source = dict(favorite or {})
    now_ts = int(now_ts or time.time())
    name = str(source.get("name") or "").strip()
    prompt = str(source.get("prompt") or "").strip()
    skill_names = normalize_selected_skill_names(source.get("skill_names") or source.get("selected_skill_names"))
    if not name:
        raise ValueError("常用名称不能为空。")
    if not prompt and not skill_names:
        raise ValueError("常用项必须包含提示词或至少一项能力。")
    execution_mode = str(source.get("execution_mode") or FAVORITE_EXECUTION_CHAT).strip().lower()
    if execution_mode not in FAVORITE_EXECUTION_MODES:
        raise ValueError(f"未知的执行位置：{execution_mode}")
    raw_workspace_dir = str(source.get("workspace_dir") or "").strip()
    workspace_dir = os.path.normpath(raw_workspace_dir) if execution_mode == FAVORITE_EXECUTION_WORKSPACE and raw_workspace_dir else ""
    if execution_mode == FAVORITE_EXECUTION_WORKSPACE and not workspace_dir:
        raise ValueError("工作区模式必须选择工作区。")
    created_at = int(source.get("created_at") or now_ts)
    schedule = normalize_favorite_schedule(source.get("schedule"), favorite_prompt=prompt, now_ts=now_ts)
    return {
        "id": str(source.get("id") or f"fav-{uuid.uuid4().hex[:10]}").strip(),
        "name": name,
        "description": str(source.get("description") or "").strip(),
        "prompt": prompt,
        "skill_names": skill_names,
        "execution_mode": execution_mode,
        "workspace_dir": workspace_dir,
        "schedule": schedule,
        "created_at": created_at,
        "updated_at": int(source.get("updated_at") or created_at or now_ts),
    }


def normalize_favorites(value, now_ts=None):
    favorites = value if isinstance(value, list) else []
    normalized = []
    used_ids = set()
    for favorite in favorites:
        entry = normalize_favorite(favorite, now_ts=now_ts)
        identifier = entry["id"]
        if identifier in used_ids:
            raise ValueError(f"常用项 ID 重复：{identifier}")
        used_ids.add(identifier)
        normalized.append(entry)
    return normalized


def normalize_favorite_run_record(record):
    source = dict(record or {})
    status = str(source.get("status") or FAVORITE_RUN_STATUS_COMPLETED).strip().lower()
    if status not in FAVORITE_RUN_STATUSES:
        raise ValueError(f"未知的常用运行状态：{status}")
    created_at = int(source.get("created_at") or source.get("started_at") or time.time())
    return {
        "id": str(source.get("id") or f"run-{uuid.uuid4().hex[:10]}").strip(),
        "favorite_id": str(source.get("favorite_id") or source.get("task_id") or "").strip(),
        "favorite_name": str(source.get("favorite_name") or source.get("task_name") or "").strip(),
        "session_id": str(source.get("session_id") or "").strip(),
        "trigger_source": str(source.get("trigger_source") or "scheduler").strip(),
        "status": status,
        "scheduled_at": int(source.get("scheduled_at") or 0),
        "started_at": int(source.get("started_at") or 0),
        "finished_at": int(source.get("finished_at") or 0),
        "summary": str(source.get("summary") or "").strip(),
        "error": str(source.get("error") or "").strip(),
        "delivery_id": str(source.get("delivery_id") or "").strip(),
        "delivery_status": str(source.get("delivery_status") or "").strip(),
        "delivery_error": str(source.get("delivery_error") or "").strip(),
        "created_at": created_at,
    }


def normalize_favorite_run_history(value):
    records = value if isinstance(value, list) else []
    normalized = [normalize_favorite_run_record(record) for record in records]
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("常用运行历史包含重复 ID。")
    normalized.sort(
        key=lambda item: item.get("started_at") or item.get("scheduled_at") or item.get("created_at") or 0,
        reverse=True,
    )
    return normalized[:FAVORITE_RUN_HISTORY_LIMIT]


def make_favorite_run_record(favorite, status=FAVORITE_RUN_STATUS_RUNNING, trigger_source="scheduler", session_id="", scheduled_at=0, summary="", error=""):
    favorite = dict(favorite or {})
    now_ts = int(time.time())
    terminal = {
        FAVORITE_RUN_STATUS_COMPLETED,
        FAVORITE_RUN_STATUS_ERROR,
        FAVORITE_RUN_STATUS_INTERRUPTED,
        FAVORITE_RUN_STATUS_MISSED,
    }
    return normalize_favorite_run_record(
        {
            "favorite_id": favorite.get("id") or "",
            "favorite_name": favorite.get("name") or "",
            "session_id": session_id,
            "trigger_source": trigger_source,
            "status": status,
            "scheduled_at": int(scheduled_at or 0),
            "started_at": now_ts if status == FAVORITE_RUN_STATUS_RUNNING else 0,
            "finished_at": now_ts if status in terminal else 0,
            "summary": summary,
            "error": error,
            "created_at": now_ts,
        }
    )


def advance_favorite_schedule(favorite, now_ts=None, after_ts=None):
    normalized = _json_copy(favorite, {})
    schedule = dict(normalized.get("schedule") or {})
    if not schedule:
        raise ValueError("常用项没有定时计划。")
    now_ts = int(now_ts or time.time())
    schedule["updated_at"] = now_ts
    schedule["next_run_at"] = compute_next_run_at(
        schedule,
        now_ts=now_ts,
        after_ts=int(after_ts if after_ts is not None else now_ts),
    )
    schedule["schedule_summary"] = describe_schedule(schedule)
    normalized["schedule"] = schedule
    normalized["updated_at"] = now_ts
    return normalized


def migrate_automation_task(task, workspace_dir="", now_ts=None):
    source = dict(task or {})
    prompt = str(source.get("prompt") or "").strip()
    legacy_needs_edit = not bool(prompt or normalize_selected_skill_names(source.get("skill_names") or []))
    if legacy_needs_edit:
        prompt = (
            "这个常用项由旧版自动化迁移而来，原配置没有可直接运行的提示词。"
            "请先编辑并补充明确任务目标。"
        )
    schedule_type = str(source.get("schedule_type") or FAVORITE_SCHEDULE_DAILY).strip().lower()
    if schedule_type not in FAVORITE_SCHEDULE_TYPES:
        schedule_type = FAVORITE_SCHEDULE_DAILY
    schedule_source = {
        key: source.get(key)
        for key in (
            "enabled",
            "cron_expression",
            "time_of_day",
            "weekdays",
            "day_of_month",
            "interval_minutes",
            "interval_anchor_at",
            "one_time_at",
            "created_at",
            "updated_at",
            "next_run_at",
            "last_run_at",
            "last_missed_at",
            "last_history_id",
        )
    }
    schedule_source["schedule_type"] = schedule_type
    if legacy_needs_edit:
        schedule_source["enabled"] = False
    schedule_source["prompt_mode"] = FAVORITE_PROMPT_INHERIT
    execution_mode = FAVORITE_EXECUTION_WORKSPACE if str(workspace_dir or "").strip() else FAVORITE_EXECUTION_CHAT
    description = str(source.get("description") or "").strip()
    if legacy_needs_edit:
        migration_note = "旧版配置缺少可执行内容，已暂停定时计划。"
        description = f"{description}\n{migration_note}".strip()
    return normalize_favorite(
        {
            "id": source.get("id"),
            "name": source.get("name") or "旧自动化",
            "description": description,
            "prompt": prompt,
            "skill_names": source.get("skill_names") or [],
            "execution_mode": execution_mode,
            "workspace_dir": workspace_dir,
            "schedule": schedule_source,
            "created_at": source.get("created_at"),
            "updated_at": source.get("updated_at"),
        },
        now_ts=now_ts,
    )
