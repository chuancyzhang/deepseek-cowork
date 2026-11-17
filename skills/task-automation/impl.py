"""Model adapters: all mutations use the same host service as the management UI."""
from core.task_automation import TaskConflict


def _call(method, context, *args, **kwargs):
    context = context or {}
    service = context.get("task_automation_service")
    if service is None:
        return {"status": "unavailable", "executed": False, "error": "当前运行未连接任务自动化服务，请在桌面应用中使用。"}
    try:
        if method in {"save", "set_schedule", "run"}:
            kwargs.update(manager=context.get("skill_manager"), run_context=context.get("run_context"),
                          operation_id=context.get("logical_execution_id") or context.get("tool_call_id") or "")
        result = getattr(service, method)(*args, **kwargs)
        if method in {"list", "history"}:
            return {"status": "ok", "items": result, "count": len(result)}
        if method in {"save", "set_schedule"}:
            return {"status": "saved", "item": result}
        return {"status": "ok", **result}
    except (ValueError, OSError, RuntimeError) as exc:
        return {"status": "conflict" if isinstance(exc, TaskConflict) else "error", "error": str(exc)}


def list_automations(query="", schedule_status="all", _context=None):
    return _call("list", _context, query, schedule_status)


def get_automation(task_id, _context=None):
    return _call("get", _context, task_id)


def save_automation(task, task_id="", expected_revision=None, _context=None):
    return _call("save", _context, task, task_id, expected_revision)


def set_automation_schedule(task_id, expected_revision, action, schedule=None, _context=None):
    return _call("set_schedule", _context, task_id, expected_revision, action, schedule)


def run_automation(task_id, source="task", _context=None):
    return _call("run", _context, task_id, source)


def list_automation_runs(task_id="", limit=20, status_filter="", _context=None):
    return _call("history", _context, task_id, limit, status_filter)


def delete_automation(task_id, expected_revision, _context=None):
    return _call("delete", _context, task_id, expected_revision)


SCHEDULE_SCHEMA = {"type": ["object", "null"], "additionalProperties": False, "properties": {
    "enabled": {"type": "boolean", "description": "用户明确要求定时执行时设为 true；仅保存方案时 false。"},
    "timezone": {"type": "string", "description": "IANA 时区，例如 Asia/Shanghai；新计划省略时由服务确定本机时区。"},
    "schedule_type": {"type": "string", "enum": ["once", "daily", "weekly", "monthly", "interval", "cron"]},
    "time_of_day": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$", "description": "daily/weekly/monthly 必填，当地 HH:mm。"},
    "weekdays": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 0, "maximum": 6}, "description": "weekly 必填，0 周一，6 周日。"},
    "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31, "description": "monthly 必填；短月份使用月末。"},
    "interval_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
    "run_at": {"type": "string", "description": "单次绝对时间，ISO 8601，必须带 UTC 偏移；与 delay_seconds 互斥。"},
    "delay_seconds": {"type": "integer", "minimum": 1, "description": "单次相对延时；由后端从保存时刻计算，不由模型计算时间戳。"},
    "cron_expression": {"type": "string", "description": "cron 类型必填，五字段 Cron。"},
    "prompt_mode": {"type": "string", "enum": ["inherit", "custom"]},
    "custom_prompt": {"type": "string", "description": "custom 时必填，必须能在独立会话执行。"},
    "delivery": {"type": ["object", "null"], "additionalProperties": False, "properties": {
        "enabled": {"type": "boolean"}, "binding_id": {"type": "string", "description": "现有已验证目标绑定 ID；不得猜测。"}}},
}}
TASK_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "name": {"type": "string"}, "description": {"type": "string"}, "prompt": {"type": "string"},
    "skill_names": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    "execution_mode": {"type": "string", "enum": ["chat", "workspace"]},
    "workspace_dir": {"type": "string", "description": "workspace 模式必须是已经存在的目录；chat 模式不携带项目。"},
    "schedule": SCHEDULE_SCHEMA,
}}
ID = {"type": "string", "description": "查询结果中的准确任务 ID，不使用名称代替。"}
REVISION = {"type": "integer", "minimum": 1, "description": "最近查询返回的 revision；冲突后重新读取并合并。"}


def _tool(name, description, properties, required=(), read_only=False):
    return {"name": name, "handler": globals()[name], "description": description,
            "parameters": {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)},
            "read_only": read_only, "destructive": not read_only, "allowed_modes": ["execution"],
            "search_hint": "定时 周期 每天 每周 提醒 常用任务 自动化 计划 运行记录 automation schedule " + name}


TOOL_EXPORTS = [
    _tool("list_automations", "查询可复用任务和定时计划，返回准确 ID、版本、配置问题及下次运行时间。",
          {"query": {"type": "string"}, "schedule_status": {"type": "string", "enum": ["all", "enabled", "paused", "none"]}}, read_only=True),
    _tool("get_automation", "读取任务的完整配置、当前版本和最近运行记录。", {"task_id": ID}, ["task_id"], True),
    _tool("save_automation", "创建或更新任务，可同时保存计划。创建需要 name 和 prompt/skill_names 至少一个；更新必须 task_id 与 expected_revision；不按同名覆盖。",
          {"task": TASK_SCHEMA, "task_id": ID, "expected_revision": REVISION}, ["task"]),
    _tool("set_automation_schedule", "设置、启用、暂停或移除任务计划；暂停不停止已运行会话，移除计划保留任务与历史。",
          {"task_id": ID, "expected_revision": REVISION, "action": {"type": "string", "enum": ["set", "enable", "pause", "remove"]}, "schedule": SCHEDULE_SCHEMA},
          ["task_id", "expected_revision", "action"]),
    _tool("run_automation", "立即运行并返回真实会话和运行回执。source=task 使用任务正文，不发送计划通知；schedule 使用计划正文和交付配置，不改变原计划。仅技能组合返回 draft_ready 等待输入。",
          {"task_id": ID, "source": {"type": "string", "enum": ["task", "schedule"]}}, ["task_id"]),
    _tool("list_automation_runs", "查询执行结果、等待输入和发送状态；任务删除后仍可按原 ID 查询历史。",
          {"task_id": ID, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
           "status_filter": {"type": "string", "enum": ["", "running", "completed", "error", "missed", "skipped", "interrupted"]}}, read_only=True),
    _tool("delete_automation", "删除任务及其计划；已经启动的会话和运行记录保留，不自动停止正在执行的任务。",
          {"task_id": ID, "expected_revision": REVISION}, ["task_id", "expected_revision"]),
]
