import copy
import os
import time

from core.favorites_manager import (
    FAVORITE_EXECUTION_CHAT,
    FAVORITE_EXECUTION_WORKSPACE,
    FAVORITE_PROMPT_INHERIT,
    favorite_effective_prompt,
    normalize_favorite,
)


# Updating any trigger definition invalidates the previously calculated next run.
_SCHEDULE_TRIGGER_FIELDS = {
    "schedule_type",
    "cron_expression",
    "time_of_day",
    "weekdays",
    "day_of_month",
    "interval_minutes",
    "interval_anchor_at",
    "one_time_at",
}


def _config_manager(_context):
    manager = (_context or {}).get("config_manager") if isinstance(_context, dict) else None
    if manager is None:
        raise ValueError("当前运行环境没有提供常用库配置服务。")
    return manager


def _approval_response(message, *, title, details="", _context=None):
    from skills.interaction.impl import request_user_approval

    return request_user_approval(message, title=title, details=details, _context=_context)


def _is_approved(payload):
    response = payload.get("interaction_response") if isinstance(payload, dict) else None
    return bool((response or {}).get("approved"))


def _find_favorite(favorites, identifier):
    wanted = str(identifier or "").strip()
    if not wanted:
        return None, -1
    for index, item in enumerate(favorites):
        if str(item.get("id") or "").strip() == wanted:
            return item, index
    for index, item in enumerate(favorites):
        if str(item.get("name") or "").strip() == wanted:
            return item, index
    return None, -1


def _available_skill_names(_context):
    manager = (_context or {}).get("skill_manager") if isinstance(_context, dict) else None
    if manager is None or not hasattr(manager, "get_all_skills"):
        return None
    return {
        str(item.get("name") or "").strip()
        for item in manager.get_all_skills()
        if item.get("enabled", True) and item.get("available", True) and str(item.get("name") or "").strip()
    }


def _validate_runtime_references(favorite, _context):
    if favorite.get("execution_mode") == FAVORITE_EXECUTION_WORKSPACE:
        workspace_dir = str(favorite.get("workspace_dir") or "").strip()
        if not os.path.isdir(workspace_dir):
            raise ValueError(f"工作区不存在：{workspace_dir}")
    available = _available_skill_names(_context)
    if available is not None:
        missing = [name for name in favorite.get("skill_names") or [] if name not in available]
        if missing:
            raise ValueError("以下能力不可用：" + "、".join(missing))


def _summarize(item):
    schedule = copy.deepcopy(item.get("schedule")) if item.get("schedule") else None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description") or "",
        "prompt": item.get("prompt") or "",
        "skill_names": list(item.get("skill_names") or []),
        "execution_mode": item.get("execution_mode") or FAVORITE_EXECUTION_CHAT,
        "workspace_dir": item.get("workspace_dir") or "",
        "schedule": schedule,
    }


def list_favorites(query="", scheduled_only=False, _context=None):
    items = _config_manager(_context).get_favorites()
    wanted = str(query or "").strip().casefold()
    if scheduled_only:
        items = [item for item in items if item.get("schedule")]
    if wanted:
        items = [
            item for item in items
            if wanted in " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                    str(item.get("prompt") or ""),
                    " ".join(item.get("skill_names") or []),
                ]
            ).casefold()
        ]
    return {
        "status": "ok",
        "count": len(items),
        "items": [_summarize(item) for item in items],
        "content": f"找到 {len(items)} 个常用项。",
    }


def upsert_favorite(favorite, match="", _context=None):
    if not isinstance(favorite, dict):
        return {"status": "error", "error": "favorite 必须是对象。"}
    manager = _config_manager(_context)
    favorites = manager.get_favorites()
    existing = None
    existing_index = -1
    for identifier in (favorite.get("id"), match, favorite.get("name")):
        existing, existing_index = _find_favorite(favorites, identifier)
        if existing:
            break
    merged = copy.deepcopy(existing or {})
    incoming = copy.deepcopy(favorite)
    if existing and isinstance(existing.get("schedule"), dict) and isinstance(incoming.get("schedule"), dict):
        schedule = copy.deepcopy(existing["schedule"])
        if _SCHEDULE_TRIGGER_FIELDS.intersection(incoming["schedule"]):
            schedule.pop("next_run_at", None)
        schedule.update(incoming["schedule"])
        incoming["schedule"] = schedule
    merged.update(incoming)
    if existing:
        merged["id"] = existing.get("id")
        merged["created_at"] = existing.get("created_at")
    else:
        merged.setdefault("execution_mode", FAVORITE_EXECUTION_CHAT)
        if isinstance(merged.get("schedule"), dict):
            merged["schedule"].setdefault("enabled", False)
    if str(merged.get("execution_mode") or FAVORITE_EXECUTION_CHAT) == FAVORITE_EXECUTION_CHAT:
        merged["workspace_dir"] = ""
    merged["updated_at"] = int(time.time())
    try:
        normalized = normalize_favorite(merged)
        _validate_runtime_references(normalized, _context)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    if existing_index >= 0:
        favorites[existing_index] = normalized
        action = "updated"
    else:
        favorites.insert(0, normalized)
        action = "created"
    manager.set_favorites(favorites)
    saved = manager.get_favorite(normalized.get("id"))
    return {
        "status": "ok",
        "action": action,
        "item": _summarize(saved),
        "content": f"常用项“{saved.get('name')}”已{'更新' if action == 'updated' else '创建'}。",
    }


def configure_favorite_schedule(favorite_id_or_name, schedule=None, remove=False, _context=None):
    manager = _config_manager(_context)
    favorites = manager.get_favorites()
    favorite, index = _find_favorite(favorites, favorite_id_or_name)
    if index < 0 or favorite is None:
        return {"status": "error", "error": f"未找到常用项：{favorite_id_or_name}"}
    updated = copy.deepcopy(favorite)
    if remove:
        updated["schedule"] = None
    else:
        if not isinstance(schedule, dict):
            return {"status": "error", "error": "schedule 必须是对象；移除计划请使用 remove=true。"}
        merged_schedule = copy.deepcopy(updated.get("schedule") or {})
        if _SCHEDULE_TRIGGER_FIELDS.intersection(schedule):
            merged_schedule.pop("next_run_at", None)
        merged_schedule.update(copy.deepcopy(schedule))
        merged_schedule.setdefault("enabled", False)
        merged_schedule.setdefault("prompt_mode", FAVORITE_PROMPT_INHERIT)
        updated["schedule"] = merged_schedule
    updated["updated_at"] = int(time.time())
    try:
        normalized = normalize_favorite(updated)
        _validate_runtime_references(normalized, _context)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    favorites[index] = normalized
    manager.set_favorites(favorites)
    saved = manager.get_favorite(normalized.get("id"))
    return {
        "status": "ok",
        "action": "schedule_removed" if remove else "schedule_updated",
        "item": _summarize(saved),
        "content": f"常用项“{saved.get('name')}”的定时计划已{'移除' if remove else '更新'}。",
    }


def delete_favorite(favorite_id_or_name, _context=None):
    manager = _config_manager(_context)
    favorites = manager.get_favorites()
    favorite, index = _find_favorite(favorites, favorite_id_or_name)
    if index < 0 or favorite is None:
        return {"status": "error", "error": f"未找到常用项：{favorite_id_or_name}"}
    approval = _approval_response(
        f"删除常用项“{favorite.get('name')}”？",
        title="删除常用项",
        details="附加计划和运行记录会一并删除；已经创建的聊天会保留。",
        _context=_context,
    )
    if not _is_approved(approval):
        return {"status": "cancelled", "approval": approval, "item": _summarize(favorite), "content": "已取消删除。"}
    del favorites[index]
    manager.set_favorites(favorites)
    manager.set_favorite_run_history([
        item for item in manager.get_favorite_run_history()
        if item.get("favorite_id") != favorite.get("id")
    ])
    return {"status": "ok", "approval": approval, "item": _summarize(favorite), "content": f"常用项“{favorite.get('name')}”已删除。"}


def launch_favorite(favorite_id_or_name, use_schedule_prompt=False, _context=None):
    manager = _config_manager(_context)
    favorite = manager.get_favorite(favorite_id_or_name)
    if favorite is None:
        return {"status": "error", "error": f"未找到常用项：{favorite_id_or_name}"}
    try:
        _validate_runtime_references(favorite, _context)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    if use_schedule_prompt and not favorite.get("schedule"):
        return {"status": "error", "error": "这个常用项没有定时计划。"}
    if use_schedule_prompt and not favorite_effective_prompt(favorite):
        return {"status": "error", "error": "定时计划没有可执行的提示词。"}
    approval = _approval_response(
        f"开始常用项“{favorite.get('name')}”？",
        title="开始常用任务",
        details="会创建一个新的主智能体任务。" + ("计划提示词将立即提交。" if use_schedule_prompt else "常用提示词会自动提交；仅能力组合会等待你输入。"),
        _context=_context,
    )
    if not _is_approved(approval):
        return {"status": "cancelled", "approval": approval, "item": _summarize(favorite), "content": "已取消启动。"}
    action = {
        "type": "run_favorite_schedule" if use_schedule_prompt else "launch_favorite",
        "favorite_id": favorite.get("id"),
    }
    return {
        "status": "queued",
        "approval": approval,
        "item": _summarize(favorite),
        "client_action": action,
        "content": f"已请求启动常用项“{favorite.get('name')}”。",
    }


def list_favorite_run_history(favorite_id_or_name="", limit=20, status_filter="", _context=None):
    manager = _config_manager(_context)
    history = manager.get_favorite_run_history()
    if favorite_id_or_name:
        favorite = manager.get_favorite(favorite_id_or_name)
        if favorite is None:
            return {"status": "error", "error": f"未找到常用项：{favorite_id_or_name}"}
        history = [item for item in history if item.get("favorite_id") == favorite.get("id")]
    wanted = str(status_filter or "").strip().lower()
    if wanted:
        history = [item for item in history if str(item.get("status") or "").lower() == wanted]
    try:
        limit_value = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        return {"status": "error", "error": "limit 必须是 1 到 100 的整数。"}
    selected = copy.deepcopy(history[:limit_value])
    return {"status": "ok", "count": len(selected), "items": selected, "content": f"找到 {len(selected)} 条运行记录。"}


TOOL_EXPORTS = [
    {
        "name": "list_favorites",
        "handler": list_favorites,
        "description": "List saved favorite prompts and capability combinations.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "scheduled_only": {"type": "boolean"}}, "required": []},
        "read_only": True,
        "allowed_modes": ["execution"],
        "search_hint": "常用 收藏 提示词 能力组合 工作模式 favorite prompt library skills",
    },
    {
        "name": "upsert_favorite",
        "handler": upsert_favorite,
        "description": "Create or update a reusable favorite prompt, capability bundle, and execution location.",
        "parameters": {"type": "object", "properties": {"favorite": {"type": "object"}, "match": {"type": "string"}}, "required": ["favorite"]},
        "allowed_modes": ["execution"],
        "search_hint": "保存为常用 创建更新提示词 能力组合 聊天 工作区 favorite workflow",
    },
    {
        "name": "configure_favorite_schedule",
        "handler": configure_favorite_schedule,
        "description": "Add, update, pause, enable, or remove the single optional schedule attached to a favorite.",
        "parameters": {"type": "object", "properties": {"favorite_id_or_name": {"type": "string"}, "schedule": {"type": "object"}, "remove": {"type": "boolean"}}, "required": ["favorite_id_or_name"]},
        "allowed_modes": ["execution"],
        "search_hint": "常用 定时 计划 cron 每天 每周 暂停 启用 schedule favorite",
    },
    {
        "name": "delete_favorite",
        "handler": delete_favorite,
        "description": "Delete a favorite, its optional schedule, and its run history after approval.",
        "parameters": {"type": "object", "properties": {"favorite_id_or_name": {"type": "string"}}, "required": ["favorite_id_or_name"]},
        "allowed_modes": ["execution"],
        "search_hint": "删除常用 移除收藏 delete favorite",
    },
    {
        "name": "launch_favorite",
        "handler": launch_favorite,
        "description": "Launch a favorite in a new main-agent task after approval, optionally using its schedule prompt.",
        "parameters": {"type": "object", "properties": {"favorite_id_or_name": {"type": "string"}, "use_schedule_prompt": {"type": "boolean"}}, "required": ["favorite_id_or_name"]},
        "allowed_modes": ["execution"],
        "search_hint": "运行常用 开始任务 主智能体 提交提示词 加载能力 launch favorite",
    },
    {
        "name": "list_favorite_run_history",
        "handler": list_favorite_run_history,
        "description": "List recent schedule run records for favorites.",
        "parameters": {"type": "object", "properties": {"favorite_id_or_name": {"type": "string"}, "limit": {"type": "integer"}, "status_filter": {"type": "string"}}, "required": []},
        "read_only": True,
        "allowed_modes": ["execution"],
        "search_hint": "常用运行历史 定时结果 错误 missed favorite run history",
    },
]
