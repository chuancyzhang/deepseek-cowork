"""Optional, provider-scoped agent startup plugins."""

from .base import AgentBootstrapPlugin
from .deepseek_flash_minimal import DeepSeekFlashMinimalBootstrap


BUILTIN_PLUGINS = (DeepSeekFlashMinimalBootstrap,)


def resolve(provider, model) -> AgentBootstrapPlugin | None:
    """Resolve an enabled plugin without changing the normal agent registry."""
    selected = str(provider.get("bootstrap_plugin") or "").strip()
    for plugin_type in BUILTIN_PLUGINS:
        if selected != plugin_type.plugin_id:
            continue
        plugin = plugin_type()
        if not plugin.supports(provider, model):
            continue
        return plugin
    return None
