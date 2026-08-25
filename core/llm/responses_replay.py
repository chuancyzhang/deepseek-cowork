RESPONSES_REPLAY_META_KEY = "responses_replay_items"
RESPONSES_REPLAY_INPUT_KEY = "_responses_replay_items"

PROVIDER_REPLAY_NAMESPACE_META_KEY = "provider_replay_namespace_v1"


def build_provider_replay_namespace(
    *,
    provider_family: str,
    base_url: str,
    model: str,
    protocol: str,
) -> dict[str, str | int]:
    """Return stable provenance for newly persisted provider-native replay data."""
    return {
        "version": 1,
        "provider_family": str(provider_family or "").strip().lower(),
        "base_url": str(base_url or "").strip().lower().rstrip("/"),
        "model": str(model or "").strip(),
        "protocol": str(protocol or "").strip().lower(),
    }


def provider_replay_namespaces_compatible(source: object, target: object) -> bool:
    """Native replay is reusable only inside the exact provider/model/protocol namespace."""
    if not isinstance(source, dict) or not isinstance(target, dict):
        return False
    required = ("version", "provider_family", "base_url", "model", "protocol")
    return all(source.get(key) == target.get(key) for key in required)
