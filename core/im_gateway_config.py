import copy


IM_PROVIDER_ORDER = ("feishu", "dingtalk", "wecom")


def normalize_im_gateway_config(value):
    """Return the canonical single-provider enterprise-message configuration."""
    source = value if isinstance(value, dict) else {}
    raw_providers = source.get("providers")
    raw_providers = raw_providers if isinstance(raw_providers, dict) else {}
    providers = {}
    for name in IM_PROVIDER_ORDER:
        item = raw_providers.get(name)
        providers[name] = copy.deepcopy(item) if isinstance(item, dict) else {}

    selected = ""
    enabled = source.get("enabled_providers")
    if isinstance(enabled, list):
        for item in enabled:
            name = str(item or "").strip().lower()
            if name in IM_PROVIDER_ORDER:
                selected = name
                break
    if not selected:
        for name in IM_PROVIDER_ORDER:
            if bool(providers[name].get("enabled")):
                selected = name
                break

    for name in IM_PROVIDER_ORDER:
        providers[name]["enabled"] = name == selected
    providers["feishu"]["long_connection"] = True
    return {
        "enabled_providers": [selected] if selected else [],
        "providers": providers,
    }


def update_selected_provider(value, provider_name, provider_values):
    normalized = normalize_im_gateway_config(value)
    selected = str(provider_name or "").strip().lower()
    if selected not in IM_PROVIDER_ORDER:
        raise ValueError("请选择飞书、钉钉或企业微信。")
    current = normalized["providers"].get(selected, {})
    current.update(copy.deepcopy(provider_values or {}))
    normalized["providers"][selected] = current
    normalized["enabled_providers"] = [selected]
    for name in IM_PROVIDER_ORDER:
        normalized["providers"][name]["enabled"] = name == selected
    normalized["providers"]["feishu"]["long_connection"] = True
    return normalized


def disable_im_gateway(value):
    normalized = normalize_im_gateway_config(value)
    normalized["enabled_providers"] = []
    for item in normalized["providers"].values():
        item["enabled"] = False
    return normalized
