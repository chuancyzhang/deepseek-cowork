"""Merge edited configuration objects without overwriting unrelated live changes."""
from copy import deepcopy


def settings_page_config_snapshot(config):
    """Only saved values represented by the settings page invalidate its editors.

    Navigation, conversation workspaces and scheduler history share config.json,
    but do not change the settings form. Keep that runtime state out of the page
    cache, and ignore bookkeeping timestamps within editable objects.
    """
    keys = (
        "model_channels", "selected_model_id", "default_workspace",
        "chat_history_dir", "chat_workspace_root", "god_mode", "data_security",
        "download_sources", "im_gateway", "experimental_long_context",
        "experimental_browser_automation",
    )

    def values(value):
        if isinstance(value, dict):
            return {key: values(item) for key, item in value.items()
                    if key not in {"created_at", "updated_at"}}
        if isinstance(value, list):
            return [values(item) for item in value]
        return deepcopy(value)

    return {key: values(config.get(key)) for key in keys}


def merge_settings_objects(baseline, draft, live, identity="id"):
    def comparable(item):
        if isinstance(item, dict):
            return {key: comparable(value) for key, value in item.items() if key not in {"created_at", "updated_at"}}
        if isinstance(item, list):
            return [comparable(value) for value in item]
        return item
    old = {item[identity]: item for item in baseline}
    new = {item[identity]: item for item in draft}
    current = {item[identity]: item for item in live}
    edited = {key for key in old.keys() | new.keys() if comparable(old.get(key)) != comparable(new.get(key))}
    for key in edited:
        if comparable(current.get(key)) != comparable(old.get(key)) and comparable(current.get(key)) != comparable(new.get(key)):
            raise ValueError("该配置已在其他位置更新，请保留当前输入并重新核对后保存。")
    result = []
    for item in live:
        key = item[identity]
        if key not in edited:
            result.append(deepcopy(item))
        elif key in new:
            result.append(deepcopy(new[key]))
    result.extend(deepcopy(item) for item in draft if item[identity] in edited and item[identity] not in current)
    return result
