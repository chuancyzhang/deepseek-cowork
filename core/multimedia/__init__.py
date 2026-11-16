"""Opt-in multimedia plugin. Imported only while a host or run owns a lease."""

import copy
from urllib.parse import urlsplit

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

# Exact provider declarations, not substring guesses about user model names.
KNOWN_MODELS = {
    # https://agnes-ai.com/zh-Hans/docs/agnes-image-20-flash
    "agnes-image-2.0-flash": {"capabilities": ["image_generate"], "chat_supported": False, "purpose_label": "图片生成"},
    # https://agnes-ai.com/zh-Hans/docs/agnes-video-25-flash
    "agnes-video-2.5-flash": {"capabilities": ["video_generate"], "chat_supported": False, "purpose_label": "视频生成"},
}
EXAMPLES = {
    "image_generate": ("生成图片", "生成一张极简咖啡店海报，暖色调，突出手冲咖啡。"),
    "video_generate": ("生成视频", "生成一段介绍咖啡店的 5 秒短片，暖色调，横屏。"),
    "audio_generate": ("文字配音", "把这段文字转为自然的中文配音：欢迎来到我们的咖啡店。"),
    "image_understand": ("理解图片", "请分析我附上的图片，概括主要内容与细节。"),
    "video_understand": ("理解视频", "请分析这个可公开访问的视频链接，概括内容与关键时间点："),
    "audio_understand": ("音频转写", "请把我附上的音频转写为文字。"),
}


def model_usage(profile, bindings):
    agnes = urlsplit(profile.get("base_url", "")).hostname == "apihub.agnes-ai.com" or any(
        item.get("adapter") == "agnes" and item.get("channel_id") == profile.get("channel_id") for item in bindings.values())
    return copy.deepcopy(KNOWN_MODELS.get(profile.get("model_name"), {})) if agnes else {}


def binding_error(kind, binding, channel):
    if not channel or not channel.get("api_key"):
        return "服务不可用或尚未填写 API Key。"
    known = model_usage({**channel, "model_name": binding.get("model")}, {kind: binding})
    if known and kind not in known["capabilities"]:
        return "此模型用于" + known["purpose_label"] + "，不能绑定为" + CAPABILITIES[kind] + "。"
    if known and binding.get("adapter") != "agnes":
        return "此 Agnes 模型需要 Agnes 接入预设，请修改后保存。"
    try:
        validate_binding(kind, binding)
    except ValueError as error:
        return str(error)
    return ""


def quick_actions(bindings, channels):
    by_id = {item["channel_id"]: item for item in channels}
    return [{"id": kind, "plugin": "multimedia", "label": label, "prompt": prompt,
             "configured": bool(bindings.get(kind)) and not binding_error(kind, bindings[kind], by_id.get(bindings[kind].get("channel_id"))),
             "page": "settings", "tab": "multimedia"}
            for kind, (label, prompt) in EXAMPLES.items()]


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
    known = KNOWN_MODELS.get(result["model"]) if result["adapter"] == "agnes" else None
    if known and kind not in known["capabilities"]:
        raise ValueError("此模型只支持" + known["purpose_label"] + "，请选择符合该用途的模型。")
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
