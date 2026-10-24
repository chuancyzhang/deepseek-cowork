"""Optional, provider-scoped agent startup plugins."""

from .base import AgentBootstrapPlugin
from .deepseek_flash_minimal import DeepSeekFlashMinimalBootstrap
from core.llm.model_catalog import get_default_bootstrap_plugin


BUILTIN_PLUGINS = (DeepSeekFlashMinimalBootstrap,)


def resolve(provider, model) -> AgentBootstrapPlugin | None:
    """Resolve an enabled plugin without changing the normal agent registry."""
    selected = str(provider.get("bootstrap_plugin", get_default_bootstrap_plugin(
        provider.get("provider_type") or provider.get("provider") or "openai",
        provider.get("base_url"), model,
    )) or "").strip()
    for plugin_type in BUILTIN_PLUGINS:
        if selected != plugin_type.plugin_id:
            continue
        plugin = plugin_type()
        if not plugin.supports(provider, model):
            continue
        return plugin
    return None
