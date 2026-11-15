"""Opt-in multimedia plugin. Imported only while a host or run owns a lease."""

import copy

CAPABILITIES = {
    "image_generate": "图片生成", "image_understand": "图片理解",
    "video_generate": "视频生成", "video_understand": "视频理解",
    "audio_generate": "语音生成", "audio_understand": "音频转写",
}
ADAPTERS = {
    "openai": {"label": "OpenAI 兼容", "capabilities": list(CAPABILITIES), "base_url": ""},
    "agnes": {"label": "Agnes", "capabilities": ["image_generate", "video_generate"],
              "base_url": "https://apihub.agnes-ai.com/v1"},
    "ark": {"label": "火山方舟", "capabilities": ["video_generate", "image_understand", "video_understand"],
            "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
}


def normalize(value):
    value = value if isinstance(value, dict) else {}
    bindings = value.get("bindings") if isinstance(value.get("bindings"), dict) else {}
    return {"enabled": value.get("enabled") is True,
            "bindings": {key: copy.deepcopy(item) for key, item in bindings.items() if key in CAPABILITIES and isinstance(item, dict)}}


def validate_binding(kind, value):
    if kind not in CAPABILITIES or not isinstance(value, dict):
        raise ValueError("多媒体配置无效。")
    if not value:
        return {}
    result = {key: str(value.get(key) or "").strip() for key in ("channel_id", "model", "adapter")}
    if not result["channel_id"] or not result["model"]:
        raise ValueError("请选择服务并填写模型标识。")
    if kind not in ADAPTERS.get(result["adapter"], {}).get("capabilities", []):
        raise ValueError("此接入预设不支持所选能力。")
    return result


def snapshot(config):
    policy = normalize(config.get("experimental_multimedia", {}))
    channels = {item["channel_id"]: item for item in config.get_model_channels()}
    profiles = {}
    for kind, binding in policy["bindings"].items():
        channel = channels.get(binding.get("channel_id"))
        if channel:
            profiles[kind] = {**binding, "base_url": channel["base_url"], "api_key": channel["api_key"],
                              "service_name": channel["display_name"]}
    return profiles
