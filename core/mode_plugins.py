"""Bundled mode plugins. Runtime policies are independent of plugin enablement.

Enablement controls new tasks only; a running task retains its policy and history.
UI plugins and mode plugins share the desktop catalog, not execution ownership.
"""
from dataclasses import dataclass
from core.grilling_mode import GrillingModePolicy


@dataclass(frozen=True)
class ModePlugin:
    id: str
    mode: str
    name: str
    description: str
    scope: str
    policy: object = None

    def describe(self, config):
        disabled = config.get("disabled_mode_plugins") or []
        return {**{key: value for key, value in self.__dict__.items() if key != "policy"}, "version": "1.0.0", "builtin": True,
                "enabled": self.id not in disabled, "kind": "mode"}


CREATION = ModePlugin("cowork.creation", "creation", "创造模式", "设计整个界面、主题与插件", "session")
GRILLING = ModePlugin("cowork.grilling", "grilling", "拷问模式", "先澄清问题，再确认执行", "run", GrillingModePolicy)
MODE_PLUGINS = (GRILLING, CREATION)


def mode_inventory(config):
    return [plugin.describe(config) for plugin in MODE_PLUGINS]


def require_mode(config, mode):
    plugin = next((item for item in MODE_PLUGINS if item.mode == mode), None)
    if plugin and not plugin.describe(config)["enabled"]:
        raise ValueError(f"{plugin.name}插件已停用，请到插件管理中启用。输入和历史仍保留。")

