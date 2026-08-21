from .wecom_capability import (
    WECOM_AUTH_PROVIDER_ID,
    get_wecom_authorization_status,
)


_TRUSTED_AUTHORIZATION_PROVIDERS = {
    WECOM_AUTH_PROVIDER_ID: {
        "id": WECOM_AUTH_PROVIDER_ID,
        "display_name": "企业微信",
        "status": get_wecom_authorization_status,
    }
}


def normalize_authorization_declaration(value):
    if not isinstance(value, dict):
        return {}
    provider_id = str(value.get("provider") or "").strip()
    if not provider_id:
        return {}
    if provider_id not in _TRUSTED_AUTHORIZATION_PROVIDERS:
        raise ValueError(f"不受信任的能力授权 Provider：{provider_id}")
    return {
        "provider": provider_id,
        "required": bool(value.get("required")),
    }


def authorization_provider(provider_id):
    provider = _TRUSTED_AUTHORIZATION_PROVIDERS.get(str(provider_id or "").strip())
    if provider is None:
        raise ValueError(f"不受信任的能力授权 Provider：{provider_id}")
    return dict(provider)


def authorization_status_for_skill(skill, *, verify_remote=False, abort_check=None):
    declaration = normalize_authorization_declaration((skill or {}).get("authorization"))
    if not declaration:
        return None
    provider = authorization_provider(declaration["provider"])
    status = provider["status"](
        verify_remote=verify_remote,
        abort_check=abort_check,
    )
    status["required"] = declaration["required"]
    return status
