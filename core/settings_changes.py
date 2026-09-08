"""Merge edited configuration objects without overwriting unrelated live changes."""
from copy import deepcopy


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
