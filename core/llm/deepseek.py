from urllib.parse import urlparse


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
LEGACY_DEEPSEEK_MODEL = "deepseek-reasoner"
DEFAULT_DEEPSEEK_THINKING_ENABLED = True
DEFAULT_DEEPSEEK_REASONING_EFFORT = "high"
SUPPORTED_DEEPSEEK_REASONING_EFFORTS = ("high", "max")
SUPPORTED_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
DEEPSEEK_V4_CONTEXT_WINDOW_TOKENS = 1_000_000
DEEPSEEK_V4_MODEL_PREFIXES = ("deepseek-v4-pro", "deepseek-v4-flash")
DEEPSEEK_RESPONSES_REPLAY_META_KEY = "deepseek_responses_replay_items"
DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY = "_deepseek_responses_replay_items"


def should_migrate_legacy_model(model_name):
    text = str(model_name or "").strip()
    return not text or text == LEGACY_DEEPSEEK_MODEL


def normalize_deepseek_reasoning_effort(value):
    text = str(value or "").strip().lower()
    if text in SUPPORTED_DEEPSEEK_REASONING_EFFORTS:
        return text
    return DEFAULT_DEEPSEEK_REASONING_EFFORT


def normalize_reasoning_effort(value, allowed=None):
    text = str(value or "").strip().lower()
    supported = tuple(SUPPORTED_REASONING_EFFORTS if allowed is None else allowed)
    return text if text in supported else ""


def normalize_reasoning_efforts(values):
    normalized = []
    for value in values or []:
        effort = normalize_reasoning_effort(value)
        if effort and effort not in normalized:
            normalized.append(effort)
    return normalized


def is_deepseek_request(model_name, base_url=None):
    name = str(model_name or "").strip().lower()
    url = str(base_url or "").strip().lower()
    return ("deepseek" in name) or ("deepseek.com" in url)


def is_official_deepseek_api(base_url):
    text = str(base_url or "").strip()
    if not text:
        return False
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return str(parsed.hostname or "").strip().lower() == "api.deepseek.com"


def is_deepseek_v4_model(model_name):
    name = str(model_name or "").strip().lower()
    return any(name == prefix or name.startswith(prefix + "-") for prefix in DEEPSEEK_V4_MODEL_PREFIXES)


def build_deepseek_request_options(
    model_name,
    base_url=None,
    thinking_enabled=DEFAULT_DEEPSEEK_THINKING_ENABLED,
    reasoning_effort=DEFAULT_DEEPSEEK_REASONING_EFFORT,
):
    if not is_deepseek_request(model_name, base_url):
        return {}
    options = {
        "extra_body": {
            "thinking": {
                "type": "enabled" if bool(thinking_enabled) else "disabled"
            }
        },
    }
    if str(reasoning_effort or "").strip():
        options["reasoning_effort"] = normalize_deepseek_reasoning_effort(reasoning_effort)
    return options
