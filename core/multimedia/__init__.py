"""Opt-in multimedia plugin. Imported only while a host or run owns a lease."""

import copy
import json
from urllib.parse import urlsplit

CAPABILITIES = {
    "image_generate": "图片生成", "image_understand": "图片理解",
    "video_generate": "视频生成", "video_understand": "视频理解",
    "audio_generate": "语音生成", "audio_understand": "音频转写",
}
SKILL_CAPABILITIES = ("image_generate", "video_generate", "video_understand")
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
    "agnes-image-2.0-flash": {"capabilities": ["image_generate"], "chat_supported": False, "purpose_label": "图片生成", "adapter": "agnes"},
    # https://agnes-ai.com/zh-Hans/docs/agnes-image-25-flash
    "agnes-image-2.5-flash": {"capabilities": ["image_generate"], "chat_supported": False, "purpose_label": "图片生成", "adapter": "agnes"},
    # https://agnes-ai.com/zh-Hans/docs/agnes-video-25-flash
    "agnes-video-2.5-flash": {"capabilities": ["video_generate"], "chat_supported": False, "purpose_label": "视频生成", "adapter": "agnes"},
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
    policy = configuration(config)
    channels = {item["channel_id"]: item for item in config.get_model_channels()}
    profiles = {}
    for kind, binding in policy["bindings"].items():
        if kind not in SKILL_CAPABILITIES:
            continue
        channel = channels.get(binding.get("channel_id"))
        if channel and not binding_error(kind, binding, channel):
            profiles[kind] = {**binding, "base_url": channel["base_url"], "api_key": channel["api_key"],
                              "service_name": channel["display_name"]}
    return profiles


def configuration(config):
    values = (config.get("skill_configs", {}) or {}).get("multimedia", {})
    bindings = json.loads(values.get("bindings") or "{}")
    if not isinstance(bindings, dict):
        raise ValueError("多媒体模型配置格式无效，请在技能配置页修正。")
    return {"enabled": config.is_skill_enabled("multimedia", default_enabled=False), "bindings": bindings}
