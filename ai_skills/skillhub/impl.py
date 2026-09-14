"""Read-only SkillHub discovery through Cowork's existing catalogue client."""

import logging

from core.skillhub import SkillHubClient, identifier, preferred_version, read_origin


log = logging.getLogger("cowork.skillhub_skill")


def _context_manager(context):
    manager = context.get("skill_manager")
    if manager is None:
        raise RuntimeError("当前技能运行上下文不可用，无法确认本地安装状态。")
    return manager


def _installed_inventory(manager):
    inventory = []
    for skill in manager.get_all_skills():
        path = skill.get("path")
        origin = read_origin(path) if path else None
        if origin is not None:
            identifier(origin.get("slug"))
            identifier(origin.get("version"))
        inventory.append((skill, origin))
    return inventory


def _local_skill(skill, manager, context, origin=None):
    name = skill["name"]
    record = manager.skill_records.get(name)
    config = skill.get("config_status") or {}
    enabled = (
        manager._is_skill_enabled_for_path(name, skill["path"])
        if skill.get("path") else bool(skill.get("enabled"))
    )
    run_context = context.get("run_context") or {}
    discovery = None
    if not enabled:
        state, message = "disabled", "已安装但未开启，请在 AI 能力商城 → 我的能力中开启。"
    elif not manager._is_skill_allowed_by_scope(name, run_context):
        state, message = "out_of_scope", "已安装，但不在当前会话允许的能力范围内。"
    elif not record:
        state, message = "runtime_not_loaded", "已安装，但当前运行时尚未加载；请检查能力状态后继续。"
    elif record.get("available") is False:
        state, message = "unavailable", "已安装，但技能加载失败，请在能力管理中查看诊断。"
    elif not config.get("complete", True):
        state, message = "configuration_required", "已安装，请先在能力设置中补齐必需配置。"
    else:
        state, message = "available", "已安装并开启，通过 tool_search 获取本地说明和工具后使用。"
        discovery = {"query": name, "include_loaded": True}
    return {
        "name": name,
        "display_name": skill.get("display_name") or name,
        "version": (origin or {}).get("version") or "",
        "enabled": enabled,
        "state": state,
        "message": message,
        "missing_config_fields": list(config.get("missing_required") or []),
        "discovery": discovery,
    }


def _installation(slug, inventory, manager, context):
    installed = []
    same_name = []
    for skill, origin in inventory:
        if origin and origin.get("slug") == slug:
            installed.append(_local_skill(skill, manager, context, origin))
        elif skill["name"].casefold() == slug.casefold():
            same_name.append(_local_skill(skill, manager, context, origin))
    return {
        "installed": bool(installed),
        "installed_skills": installed,
        "same_name_local_skills": same_name,
        "installation_hint": (
            "优先使用已安装能力；不可用时按本地状态处理。" if installed
            else "未找到此 SkillHub 来源的安装。先核对同名本地能力；确需新技能时询问是否安装使用。"
        ),
    }


def _log_event(event, context, **fields):
    log.info(
        "skillhub_skill event=%s session_id=%s tool_call_id=%s fields=%s",
        event, context.get("session_id") or "", context.get("tool_call_id") or "", fields,
    )


def _error(exc, context, operation, phase):
    _log_event(operation + "_error", context, phase=phase, error_type=type(exc).__name__)
    return {
        "ok": False,
        "status": "error",
        "phase": phase,
        "message": str(exc),
        "recovery": "请修正参数或检查对应服务、能力状态后重试；本次未安装或修改任何技能。",
    }


def search_skillhub(query, page=1, _context=None):
    context = _context if isinstance(_context, dict) else {}
    phase = "validation"
    _log_event("search_submit", context)
    try:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("请提供技能名称或任务关键词。")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("页码必须是大于等于 1 的整数。")
        query = query.strip()
        manager = _context_manager(context)
        phase = "local_inventory"
        inventory = _installed_inventory(manager)
        phase = "remote_search"
        _log_event("search_start", context, page=page)
        data = SkillHubClient().search(keyword=query, page=page)
        phase = "local_status"
        skills = []
        for item in data["skills"]:
            slug = item["slug"]
            skills.append({
                "slug": slug,
                "name": item["name"],
                "summary": item.get("description_zh") or item.get("description") or "",
                "version": item.get("version") or "",
                "url": "https://skillhub.cn/skills/" + identifier(slug),
                **_installation(slug, inventory, manager, context),
            })
        _log_event("search_done", context, count=len(skills), total=data["total"])
        return {
            "ok": True,
            "status": "ok",
            "query": query,
            "page": page,
            "total": data["total"],
            "has_more": page * 20 < data["total"],
            "skills": skills,
            "message": "已读取 SkillHub 搜索结果并核对本地状态。" if skills else "本页没有匹配结果。",
        }
    except Exception as exc:
        return _error(exc, context, "search", phase)


def get_skillhub_skill(slug, _context=None):
    context = _context if isinstance(_context, dict) else {}
    phase = "validation"
    _log_event("detail_submit", context)
    try:
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("请提供搜索结果中的准确 SkillHub slug。")
        slug = slug.strip()
        encoded_slug = identifier(slug)
        manager = _context_manager(context)
        phase = "local_inventory"
        inventory = _installed_inventory(manager)
        phase = "remote_detail"
        _log_event("detail_start", context, slug=slug)
        data = SkillHubClient().detail(slug)
        skill = data["skill"]
        latest = data.get("latestVersion") or {}
        phase = "local_status"
        installation = _installation(slug, inventory, manager, context)
        _log_event("detail_done", context, slug=slug, installed=installation["installed"])
        return {
            "ok": True,
            "status": "ok",
            "slug": slug,
            "name": skill["displayName"],
            "summary": skill.get("summary_zh") or skill.get("summary") or "",
            "latest_version": preferred_version(data),
            "changelog": latest.get("changelog") or "",
            "owner": (data.get("owner") or {}).get("handle") or "",
            "url": "https://skillhub.cn/skills/" + encoded_slug,
            **installation,
            "install_path": "AI 能力商城 → SkillHub → 搜索此 slug → 查看详情并安装；再到我的能力开启和配置。",
        }
    except Exception as exc:
        return _error(exc, context, "detail", phase)
