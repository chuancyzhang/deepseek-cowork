"""Model discovery, recommendations, and defaults for newly added models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from anthropic import Anthropic
from openai import OpenAI

from .providers import API_PROTOCOL_CHAT_COMPLETIONS, API_PROTOCOL_RESPONSES


DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_RECOMMENDATION_KEY = "deepseek_official"
DEEPSEEK_VISION_MODEL = "deepseek-v4-flash-vision-exp"
DEEPSEEK_NEW_MODEL_REASONING_EFFORTS = ("high", "max")
DEEPSEEK_NEW_MODEL_DEFAULT_REASONING_EFFORT = "max"

# Product-owned configuration. Recommendation decisions elsewhere must read
# this registry instead of hard-coding a model name.
MODEL_RECOMMENDATIONS = {
    DEEPSEEK_RECOMMENDATION_KEY: {
        "model_name": "deepseek-v4-flash",
        "label": "推荐",
    },
}


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    owned_by: str = ""

    def as_dict(self):
        return {"id": self.id, "owned_by": self.owned_by}


class ModelCatalogError(RuntimeError):
    """A user-displayable model discovery failure with secrets removed."""


def _normalized_provider_type(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"openai", "deepseek"}:
        return "openai"
    if normalized == "anthropic":
        return "anthropic"
    return ""


def is_deepseek_official_base_url(base_url):
    """Return whether a URL targets the official DeepSeek API endpoint."""

    try:
        parsed = urlparse(str(base_url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "api.deepseek.com":
        return False
    if (
        parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    return (parsed.path or "").rstrip("/").lower() in {"", "/v1"}


def get_recommended_model(provider_type="openai", base_url=DEEPSEEK_OFFICIAL_BASE_URL):
    if _normalized_provider_type(provider_type) != "openai":
        return ""
    if not is_deepseek_official_base_url(base_url):
        return ""
    return str(MODEL_RECOMMENDATIONS[DEEPSEEK_RECOMMENDATION_KEY]["model_name"] or "").strip()


def get_recommendation_label(provider_type="openai", base_url=DEEPSEEK_OFFICIAL_BASE_URL):
    if not get_recommended_model(provider_type, base_url):
        return ""
    return str(MODEL_RECOMMENDATIONS[DEEPSEEK_RECOMMENDATION_KEY].get("label") or "推荐")


def is_recommended_model(model_name, provider_type="openai", base_url=DEEPSEEK_OFFICIAL_BASE_URL):
    recommended = get_recommended_model(provider_type, base_url)
    return bool(recommended and str(model_name or "").strip() == recommended)


def build_new_model_defaults(provider_type, base_url, model_name):
    """Return explicit defaults for a model created after service configuration."""

    provider = _normalized_provider_type(provider_type)
    if provider != "openai":
        return {"supports_vision": False, "supports_image_generation": False}
    if not is_deepseek_official_base_url(base_url):
        return {
            "supports_vision": False,
            "supports_image_generation": False,
            "api_protocol": API_PROTOCOL_CHAT_COMPLETIONS,
            "deepseek_thinking_enabled": False,
            "deepseek_reasoning_effort": "",
            "reasoning_efforts": [],
            "reasoning_effort": "",
        }
    normalized_name = str(model_name or "").strip().lower()
    return {
        "supports_vision": normalized_name == DEEPSEEK_VISION_MODEL,
        "supports_image_generation": False,
        "api_protocol": API_PROTOCOL_RESPONSES,
        "deepseek_thinking_enabled": True,
        "deepseek_reasoning_effort": DEEPSEEK_NEW_MODEL_DEFAULT_REASONING_EFFORT,
        "reasoning_efforts": list(DEEPSEEK_NEW_MODEL_REASONING_EFFORTS),
        "reasoning_effort": DEEPSEEK_NEW_MODEL_DEFAULT_REASONING_EFFORT,
    }


def _safe_error_message(error, api_key=""):
    message = str(error or "模型接口请求失败").strip() or "模型接口请求失败"
    secret = str(api_key or "")
    if secret:
        message = message.replace(secret, "***")
    message = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1***", message)
    message = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+", r"\1***", message)
    return message


def _page_items(page):
    data = getattr(page, "data", None)
    if isinstance(data, list):
        return data
    if isinstance(page, list):
        return page
    try:
        return list(page or [])
    except TypeError:
        return []


def _iter_pages(first_page):
    page = first_page
    seen_pages = 0
    while page is not None:
        yield page
        seen_pages += 1
        if seen_pages >= 100:
            raise ModelCatalogError("模型列表分页超过安全上限，请检查接口响应。")
        has_next = getattr(page, "has_next_page", None)
        if not callable(has_next) or not has_next():
            break
        get_next = getattr(page, "get_next_page", None)
        if not callable(get_next):
            raise ModelCatalogError("模型接口声明了下一页，但没有提供分页读取能力。")
        page = get_next()


def list_available_models(provider_type, base_url, api_key, timeout=20):
    """List models visible to the supplied service credentials.

    The function deliberately does not infer chat, vision, tool, or reasoning
    capabilities from a model name.
    """

    provider = _normalized_provider_type(provider_type)
    endpoint = str(base_url or "").strip()
    secret = str(api_key or "").strip()
    if not endpoint:
        raise ModelCatalogError("请填写服务地址。")
    if not secret:
        raise ModelCatalogError("请填写访问密钥。")
    if not provider:
        raise ModelCatalogError(f"不支持的服务类型：{provider_type}")
    try:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelCatalogError("服务地址必须是有效的 HTTP 或 HTTPS URL。")
        request_timeout = httpx.Timeout(float(timeout), connect=min(float(timeout), 10.0))
        if provider == "anthropic":
            client = Anthropic(api_key=secret, base_url=endpoint, timeout=request_timeout)
            first_page = client.models.list(limit=100)
        else:
            client = OpenAI(api_key=secret, base_url=endpoint, timeout=request_timeout)
            first_page = client.models.list()
        records = {}
        for page in _iter_pages(first_page):
            for item in _page_items(page):
                model_id = str(getattr(item, "id", "") or (item.get("id") if isinstance(item, dict) else "")).strip()
                if not model_id:
                    continue
                owned_by = str(
                    getattr(item, "owned_by", "")
                    or getattr(item, "display_name", "")
                    or (item.get("owned_by") if isinstance(item, dict) else "")
                    or ""
                ).strip()
                records.setdefault(model_id, ModelCatalogEntry(model_id, owned_by))
    except ModelCatalogError:
        raise
    except Exception as exc:
        raise ModelCatalogError(_safe_error_message(exc, secret)) from exc
    if not records:
        raise ModelCatalogError("接口返回成功，但没有提供任何模型。")
    return [records[key].as_dict() for key in sorted(records, key=str.casefold)]
