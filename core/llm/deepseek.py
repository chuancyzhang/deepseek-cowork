DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
LEGACY_DEEPSEEK_MODEL = "deepseek-reasoner"
DEFAULT_DEEPSEEK_THINKING_ENABLED = True
DEFAULT_DEEPSEEK_REASONING_EFFORT = "high"
SUPPORTED_DEEPSEEK_REASONING_EFFORTS = ("high", "max")


def should_migrate_legacy_model(model_name):
    text = str(model_name or "").strip()
    return not text or text == LEGACY_DEEPSEEK_MODEL


def normalize_deepseek_reasoning_effort(value):
    text = str(value or "").strip().lower()
    if text in SUPPORTED_DEEPSEEK_REASONING_EFFORTS:
        return text
    return DEFAULT_DEEPSEEK_REASONING_EFFORT


def is_deepseek_request(model_name, base_url=None):
    name = str(model_name or "").strip().lower()
    url = str(base_url or "").strip().lower()
    return ("deepseek" in name) or ("deepseek.com" in url)


def build_deepseek_request_options(
    model_name,
    base_url=None,
    thinking_enabled=DEFAULT_DEEPSEEK_THINKING_ENABLED,
    reasoning_effort=DEFAULT_DEEPSEEK_REASONING_EFFORT,
):
    if not is_deepseek_request(model_name, base_url):
        return {}
    return {
        "reasoning_effort": normalize_deepseek_reasoning_effort(reasoning_effort),
        "extra_body": {
            "thinking": {
                "type": "enabled" if bool(thinking_enabled) else "disabled"
            }
        },
    }
