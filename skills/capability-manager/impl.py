"""Read existing catalog state without enabling, connecting or discovering tools."""
from core.capability_search import score


def inspect_capabilities(query="", limit=12, _context=None):
    context = _context or {}
    manager = context.get("skill_manager")
    if manager is None:
        return {"ok": False, "error": "当前运行没有可读取的能力目录。"}
    entries = []
    for item in manager.get_all_skills():
        name = item["name"]
        rank = score(query, names=[name, item.get("display_name", "")],
                     phrases=item.get("use_cases") or [], description=item.get("description", "")) if query else 1
        if rank <= 0:
            continue
        record = manager.skill_records.get(name) or {}
        config = item.get("config_status") or {}
        dependency = record.get("dependency_status") or item.get("dependency_status") or {}
        enabled = bool(item.get("enabled", True)) and bool((record.get("mcp_server") or {}).get("enabled", True))
        in_scope = manager._is_skill_allowed_by_scope(name, context.get("run_context"))
        loaded = name in manager.loaded_skills_meta
        problems = []
        if not enabled:
            problems.append("disabled")
        if config.get("complete") is False:
            problems.append("needs_configuration")
        if record.get("load_error") or dependency.get("ok") is False and enabled:
            problems.append("load_or_dependency_error")
        if not in_scope:
            problems.append("out_of_scope")
        if not loaded or record.get("mcp_server") and not record.get("tool_refs"):
            problems.append("not_loaded")
        entries.append({"name": name, "display_name": item.get("display_name") or name,
                        "description": item.get("user_description") or item.get("description", ""),
                        "installed": True, "enabled": enabled, "in_scope": in_scope, "loaded": loaded,
                        "status": problems[0] if problems else "available", "issues": problems,
                        "missing_configuration": config.get("missing_required") or [],
                        "settings": "能力 → MCP 服务" if record.get("mcp_server") else "能力 → 技能 → 我的能力",
                        "score": rank})
    entries.sort(key=lambda entry: (-entry["score"], entry["name"]))
    return {"ok": True, "capabilities": entries[:max(1, min(50, int(limit)))],
            "message": "这是当前目录状态，未连接服务或加载工具；可执行能力使用 tool_search 发现。"}


TOOL_EXPORTS = [{"name": "inspect_capabilities", "handler": inspect_capabilities,
                 "description": "查看已安装能力的用途、启用、配置、依赖与当前会话状态；不连接服务或启用能力。",
                 "parameters_schema": {"type": "object", "properties": {
                     "query": {"type": "string"}, "limit": {"type": "integer"}}},
                 "read_only": True, "search_hint": "能力 状态 连接 配置 插件 capability connection status"}]
