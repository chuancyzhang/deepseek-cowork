"""Explicit gateway metadata; discovery never makes generation requests."""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone

from .deepseek import normalize_reasoning_efforts
from .model_catalog import build_new_model_defaults


ENDPOINTS = {
    "openai-response": ("openai", "responses"), "responses": ("openai", "responses"),
    "/v1/responses": ("openai", "responses"),
    "openai": ("openai", "chat_completions"), "chat_completions": ("openai", "chat_completions"),
    "/v1/chat/completions": ("openai", "chat_completions"),
    "anthropic": ("anthropic", "messages"), "messages": ("anthropic", "messages"),
    "/v1/messages": ("anthropic", "messages"),
}
SOURCE_FIELDS = ("platform", "site_url", "account_id", "key_id", "key_name", "group_id", "group_name", "service_id")


def normalize_gateway_source(value):
    """Only public connection identity is allowed into config.json."""
    if not isinstance(value, dict) or value.get("platform") not in {"sub2api", "new_api"}:
        return {}
    result = {key: str(value.get(key, "")) for key in SOURCE_FIELDS}
    if value.get("group_platform"):
        result["group_platform"] = str(value["group_platform"])
    return result


def normalize_discovery(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("source", "fetched_at", "protocol_status", "availability", "unsupported_reason", "metadata_warning"):
        if isinstance(value.get(key), str):
            result[key] = value[key][:1000]
    for key in ("endpoints", "manual_fields"):
        if isinstance(value.get(key), list):
            result[key] = [str(item)[:100] for item in value[key]][:100]
    caps = value.get("capabilities")
    if isinstance(caps, dict):
        clean = {}
        for key in ("supports_vision", "supports_image_generation", "supports_tools"):
            if type(caps.get(key)) is bool:
                clean[key] = caps[key]
        if isinstance(caps.get("reasoning_efforts"), list):
            clean["reasoning_efforts"] = normalize_reasoning_efforts(caps["reasoning_efforts"])
        for key in ("context_window_tokens", "max_output_tokens"):
            if type(caps.get(key)) is int and 0 < caps[key] <= 100000000:
                clean[key] = caps[key]
        result["capabilities"] = clean
    limits = value.get("group_restrictions")
    if isinstance(limits, dict):
        result["group_restrictions"] = {key: limits[key] for key in ("max_reasoning_effort", "allow_image_generation")
                                        if key in limits and isinstance(limits[key], (str, bool))}
    # Preserve inspectable declarations, never upstream prompts or credentials.
    declaration = value.get("declaration")
    if isinstance(declaration, dict):
        clean = {}
        for key in ("input_modalities", "supported_tools"):
            if isinstance(declaration.get(key), list):
                clean[key] = [item[:100] for item in declaration[key][:100] if isinstance(item, str)]
        if type(declaration.get("context_window")) is int:
            clean["context_window"] = declaration["context_window"]
        if isinstance(declaration.get("supported_reasoning_levels"), list):
            clean["supported_reasoning_levels"] = [
                {"effort": str(item.get("effort", ""))[:100]} if isinstance(item, dict) else str(item)[:100]
                for item in declaration["supported_reasoning_levels"][:100]]
        result["declaration"] = clean
    return result


def catalog_record(raw, *, source="models", default_provider="openai", platform=""):
    """Translate declared endpoint/capability fields, never a model-name heuristic."""
    raw = raw if isinstance(raw, dict) else {}
    model_id = str(raw.get("id") or raw.get("slug") or raw.get("name") or "").strip()
    endpoints = raw.get("supported_endpoint_types", raw.get("endpoints", []))
    if isinstance(endpoints, dict):
        endpoints = list(endpoints)
    if not isinstance(endpoints, list):
        endpoints = []
    endpoints = [str(item) for item in endpoints]
    choices = {ENDPOINTS[item] for item in endpoints if item in ENDPOINTS}
    selected = next((choice for choice in (("openai", "responses"), ("openai", "chat_completions"),
                                          ("anthropic", "messages")) if choice in choices), None)
    unsupported = bool(endpoints and not selected)
    # A native Gemini-only group is not an OpenAI-compatible conversation service.
    if not endpoints and platform == "gemini":
        unsupported = True
    provider, protocol = selected or (default_provider, "messages" if default_provider == "anthropic" else "chat_completions")
    caps = {}
    declared = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    for key in ("supports_vision", "supports_tools"):
        value = declared.get(key, raw.get(key))
        if type(value) is bool:
            caps[key] = value
    # Image endpoints and group permission do NOT imply the Responses image tool.
    tools = raw.get("supported_tools", declared.get("supported_tools", []))
    image_tool = declared.get("responses_image_generation", raw.get("supports_responses_image_generation"))
    if isinstance(tools, list) and "image_generation" in tools:
        image_tool = True
    if type(image_tool) is bool:
        caps["supports_image_generation"] = image_tool and protocol == "responses"
    efforts = declared.get("reasoning_efforts", raw.get("supported_reasoning_efforts", raw.get("supported_reasoning_levels", raw.get("reasoning_efforts"))))
    if isinstance(efforts, list):
        values = [item.get("reasoning_effort", item.get("effort", item.get("value"))) if isinstance(item, dict) else item for item in efforts]
        caps["reasoning_efforts"] = normalize_reasoning_efforts(values)
    modalities = raw.get("input_modalities")
    if isinstance(modalities, list):
        caps["supports_vision"] = "image" in modalities
    for key in ("context_window_tokens", "max_output_tokens"):
        value = declared.get(key, raw.get(key, raw.get("context_window") if key == "context_window_tokens" else None))
        if type(value) is int and 0 < value <= 100000000:
            caps[key] = value
    discovery = {"source": source, "fetched_at": datetime.now(timezone.utc).isoformat(),
                 "endpoints": endpoints, "protocol_status": "declared" if selected else "unknown",
                 "availability": "listed", "capabilities": caps,
                 "unsupported_reason": "当前应用暂不支持此调用协议" if unsupported else "",
                 "declaration": {key: raw[key] for key in ("input_modalities", "supported_reasoning_levels", "context_window", "supported_tools") if key in raw}}
    return {"id": model_id, "owned_by": str(raw.get("owned_by") or raw.get("display_name") or ""),
            "provider_type": provider, "api_protocol": protocol, "discovery": discovery}


def new_catalog_model(record, channel_id, base_url):
    provider = record.get("provider_type", "openai")
    name = record["id"]
    model = {"id": channel_id + "-" + hashlib.sha256(name.encode()).hexdigest()[:12],
             "model_name": name, "display_name": name}
    model.update(build_new_model_defaults(provider, base_url, name))
    metadata = normalize_discovery(record.get("discovery"))
    if metadata:
        model["discovery"] = metadata
        if provider == "openai" and metadata.get("protocol_status") == "declared":
            model["api_protocol"] = record.get("api_protocol", "chat_completions")
        caps = metadata.get("capabilities", {})
        model.update({key: copy.deepcopy(value) for key, value in caps.items() if key != "supports_tools"})
        if provider == "openai" and "reasoning_efforts" in caps:
            efforts = caps["reasoning_efforts"]
            effort = "medium" if "medium" in efforts else next(iter(efforts), "")
            model.update(reasoning_effort=effort, deepseek_reasoning_effort=effort,
                         deepseek_thinking_enabled=bool(efforts))
    return model


def reconcile_catalog(models, records):
    """Refresh evidence only; existing runtime choices/IDs always survive."""
    visible = {record["id"]: record for record in records}
    for model in models:
        record = visible.get(model.get("model_name"))
        previous = model.get("discovery", {})
        if record is not None:
            evidence = record.get("discovery") or catalog_record(record)["discovery"]
        else:
            evidence = copy.deepcopy(previous) or catalog_record({"id": model.get("model_name")})["discovery"]
            evidence["availability"] = "not_returned"
        model["discovery"] = normalize_discovery(evidence)
        legacy_fields = [key for key in ("api_protocol", "supports_vision", "supports_image_generation", "reasoning_efforts",
                                         "context_window_tokens", "max_output_tokens") if key in model]
        model["discovery"]["manual_fields"] = previous.get("manual_fields", legacy_fields if not previous else [])


def capability_summary(model):
    evidence = model.get("discovery", {})
    labels = []
    if model.get("supports_vision"):
        labels.append("图片理解")
    if model.get("supports_image_generation"):
        labels.append("图片生成")
    if model.get("reasoning_efforts"):
        labels.append("推理 " + str(model.get("reasoning_effort") or "/".join(model["reasoning_efforts"])))
    if evidence:
        if not evidence.get("capabilities"):
            labels.append("能力信息未提供")
        if evidence.get("protocol_status") == "unknown" and "api_protocol" not in evidence.get("manual_fields", []):
            labels.append("调用方式未确认")
        if evidence.get("availability") == "not_returned":
            labels.append("本次未返回")
    return " · ".join(labels) or "对话模型"
