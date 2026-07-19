from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .env_utils import get_app_data_dir


DEFAULT_THEME_ID = "default"
THEME_DIRECTORY_NAME = "themes"
THEME_STORE_FILENAME = "_state.json"
THEME_PREVIEW_FILENAME = "_preview.json"

FONT_SIZE_TOKENS = (
    "font_size_caption",
    "font_size_meta",
    "font_size_body",
    "font_size_section",
    "font_size_page",
    "font_size_hero",
)
FONT_WEIGHT_TOKENS = (
    "font_weight_medium",
    "font_weight_semibold",
    "font_weight_bold",
)
RADIUS_TOKENS = ("radius_sm", "radius_md", "radius_lg", "radius_xl")
DENSITY_TOKENS = (
    "control_height_sm",
    "control_height",
    "control_height_lg",
    "row_height",
    "row_height_comfortable",
    "icon_size_sm",
    "icon_size",
    "icon_size_lg",
    "spacing_xs",
    "spacing_sm",
    "spacing_md",
    "spacing_lg",
    "spacing_xl",
    "spacing_2xl",
)

NON_APPEARANCE_TOKENS = {
    "toast_enter_duration_ms",
    "toast_exit_duration_ms",
    "toast_default_duration_ms",
    "toast_warning_duration_ms",
    "toast_error_duration_ms",
    "toast_max_visible",
    "activity_indicator_interval_ms",
}

COLOR_TOKEN_PREFIXES = (
    "primary",
    "accent_",
    "text_",
    "bg_",
    "border",
    "separator",
    "selection_",
    "success_",
    "error_",
    "warning_",
    "info_",
    "toast_",
    "muted_",
    "status_",
)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_RGBA_COLOR_RE = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}"
    r"(?:\s*,\s*(?:0(?:\.\d+)?|1(?:\.0+)?))?\s*\)$"
)


def append_theme_log(data_dir: str, event: str, **fields: Any) -> None:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "theme_debug.log")
    parts = [time.strftime("%Y-%m-%d %H:%M:%S"), str(event or "theme_event")]
    parts.extend(
        f"{key}={json.dumps(value, ensure_ascii=False, default=str)}"
        for key, value in sorted(fields.items())
    )
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(" ".join(parts) + "\n")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix=".theme-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _is_color_token(name: str, default_value: Any) -> bool:
    return isinstance(default_value, str) and (
        default_value.startswith("#")
        or default_value.startswith("rgb")
        or any(name == prefix or name.startswith(prefix) for prefix in COLOR_TOKEN_PREFIXES)
    )


def configurable_theme_tokens(default_tokens: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in default_tokens.items():
        if name.startswith("_"):
            continue
        if name in NON_APPEARANCE_TOKENS:
            continue
        if _is_color_token(name, value):
            result[name] = "color"
        elif name.startswith("shadow_"):
            result[name] = "shadow"
        elif name in FONT_SIZE_TOKENS:
            result[name] = "font_size"
        elif name in FONT_WEIGHT_TOKENS:
            result[name] = "font_weight"
        elif name in RADIUS_TOKENS or name.endswith("_radius"):
            result[name] = "radius"
        elif name in DENSITY_TOKENS:
            result[name] = "density"
        elif isinstance(value, float) and name.endswith("_ratio"):
            result[name] = "ratio"
        elif name.endswith("_stroke"):
            result[name] = "stroke"
        elif name.endswith("_alpha"):
            result[name] = "alpha"
        elif isinstance(value, (int, float)) and any(
            marker in name
            for marker in (
                "_width",
                "_height",
                "_size",
                "_margin",
                "_padding",
                "_gap",
                "_indent",
                "_threshold",
                "_distance",
            )
        ):
            result[name] = "geometry"
    return result


def theme_token_group(name: str) -> str:
    token = str(name or "")
    if token.startswith(("sidebar_", "bg_sidebar")):
        return "left_sidebar"
    if token.startswith(("composer_",)):
        return "composer"
    if token.startswith(("right_sidebar_", "drawer_")):
        return "right_sidebar"
    if token.startswith(("preview_shell_",)):
        return "preview_shell"
    if token.startswith(("management_", "settings_", "bg_settings", "border_settings")):
        return "management"
    if token.startswith(
        (
            "chat_",
            "conversation_",
            "message_",
            "user_",
            "assistant_",
            "bg_chat",
            "bg_user",
            "bg_assistant",
        )
    ):
        return "conversation"
    if token.startswith(
        (
            "toast_",
            "status_",
            "success_",
            "error_",
            "warning_",
            "info_",
            "muted_",
            "overlay_",
        )
    ):
        return "feedback"
    if token.startswith(("scrollbar_", "control_", "icon_")):
        return "controls"
    return "global"


def theme_token_bounds(name: str, token_type: str) -> tuple[float, float] | None:
    if token_type == "ratio":
        return 0.1, 1.25
    if token_type == "stroke":
        return 0.5, 6.0
    if token_type == "alpha":
        return 0, 255
    if token_type != "geometry":
        return None
    if "sidebar" in name and "width" in name:
        return 180, 480
    if "drawer" in name and "width" in name:
        return 240, 720
    if any(part in name for part in ("conversation", "message", "user_bubble")) and "width" in name:
        return 280, 1600
    if "toast" in name and "width" in name:
        return 180, 1200
    if "content_max_width" in name:
        return 600, 1800
    if "threshold" in name:
        return 480, 2200
    if "height" in name:
        return 16, 120
    if "icon" in name or "indicator_size" in name:
        return 8, 48
    if any(part in name for part in ("margin", "padding", "gap", "indent", "distance")):
        return 0, 64
    if "width" in name:
        return 1, 1800
    return 0, 1600


def theme_geometry_errors(tokens: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ordered_groups = (
        ("sidebar_min_width", "sidebar_width", "sidebar_max_width"),
        ("drawer_min_width", "drawer_preferred_min_width", "drawer_max_width"),
        ("conversation_compact_min_width", "conversation_min_width", "conversation_max_width"),
        ("message_compact_min_width", "message_min_width", "message_max_width"),
        ("user_bubble_compact_min_width", "user_bubble_min_width", "user_bubble_max_width"),
        ("toast_min_width", None, "toast_max_width"),
    )
    for minimum_name, preferred_name, maximum_name in ordered_groups:
        minimum = float(tokens.get(minimum_name, 0))
        maximum = float(tokens.get(maximum_name, 0))
        if minimum > maximum:
            errors.append(f"{minimum_name} 不能大于 {maximum_name}。")
        if preferred_name:
            preferred = float(tokens.get(preferred_name, minimum))
            if not minimum <= preferred <= maximum:
                errors.append(
                    f"{preferred_name} 必须位于 {minimum_name} 与 {maximum_name} 之间。"
                )
    preferred_conversation = float(
        tokens.get("conversation_preferred_width", tokens.get("conversation_max_width", 0))
    )
    if not float(tokens.get("conversation_min_width", 0)) <= preferred_conversation <= float(
        tokens.get("conversation_max_width", 0)
    ):
        errors.append(
            "conversation_preferred_width 必须位于 conversation_min_width 与 conversation_max_width 之间。"
        )
    if float(tokens.get("user_bubble_max_width", 0)) > float(
        tokens.get("message_max_width", 0)
    ):
        errors.append("user_bubble_max_width 不能大于 message_max_width。")
    if float(tokens.get("message_max_width", 0)) > float(
        tokens.get("conversation_max_width", 0)
    ):
        errors.append("message_max_width 不能大于 conversation_max_width。")
    return errors


def _normalize_color(value: Any) -> str:
    text = str(value or "").strip()
    if _HEX_COLOR_RE.fullmatch(text):
        return text.lower()
    if _RGBA_COLOR_RE.fullmatch(text):
        channels = [item.strip() for item in text[text.find("(") + 1 : -1].split(",")]
        if any(int(item) > 255 for item in channels[:3]):
            raise ValueError(f"颜色通道超出范围：{text}")
        return text
    raise ValueError(f"颜色必须使用 #RRGGBB、#RRGGBBAA、rgb() 或 rgba()：{text}")


def _coerce_float(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是数字。") from None
    if number < minimum or number > maximum:
        raise ValueError(f"{field} 必须位于 {minimum:g}–{maximum:g} 之间。")
    return round(number, 4)


def _coerce_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数。") from None
    if number < minimum or number > maximum:
        raise ValueError(f"{field} 必须位于 {minimum}–{maximum} 之间。")
    return number


def _normalize_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("主题名称不能为空。")
    if len(name) > 40:
        raise ValueError("主题名称不能超过 40 个字符。")
    return name


def validate_theme_overrides(
    overrides: Any,
    default_tokens: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(overrides, dict):
        raise ValueError("主题 overrides 必须是对象。")
    allowed_top_level = {
        "font_family",
        "mono_font_family",
        "font_scale",
        "density",
        "radius_scale",
        "tokens",
    }
    unknown = sorted(set(overrides) - allowed_top_level)
    if unknown:
        raise ValueError(f"主题包含未知字段：{', '.join(unknown)}")
    normalized: dict[str, Any] = {}
    for field in ("font_family", "mono_font_family"):
        if field not in overrides:
            continue
        family = str(overrides.get(field) or "").strip()
        if not family:
            raise ValueError(f"{field} 不能为空。")
        if len(family) > 120:
            raise ValueError(f"{field} 过长。")
        normalized[field] = family
    if "font_scale" in overrides:
        normalized["font_scale"] = _coerce_float(
            overrides["font_scale"],
            field="font_scale",
            minimum=0.8,
            maximum=1.5,
        )
    density = str(overrides.get("density") or "").strip().lower()
    if density:
        if density not in {"compact", "standard", "comfortable"}:
            raise ValueError("density 必须是 compact、standard 或 comfortable。")
        normalized["density"] = density
    if "radius_scale" in overrides:
        normalized["radius_scale"] = _coerce_float(
            overrides["radius_scale"],
            field="radius_scale",
            minimum=0.5,
            maximum=1.5,
        )

    token_types = configurable_theme_tokens(default_tokens)
    raw_tokens = overrides.get("tokens", {})
    if not isinstance(raw_tokens, dict):
        raise ValueError("overrides.tokens 必须是对象。")
    normalized_tokens: dict[str, Any] = {}
    for name, value in raw_tokens.items():
        if name not in token_types:
            raise ValueError(f"不可配置或未知的主题令牌：{name}")
        token_type = token_types[name]
        if token_type == "color":
            normalized_tokens[name] = _normalize_color(value)
        elif token_type == "font_size":
            normalized_tokens[name] = _coerce_int(value, field=name, minimum=9, maximum=32)
        elif token_type == "font_weight":
            normalized_tokens[name] = _coerce_int(value, field=name, minimum=100, maximum=900)
        elif token_type == "radius":
            normalized_tokens[name] = _coerce_int(value, field=name, minimum=0, maximum=32)
        elif token_type == "density":
            normalized_tokens[name] = _coerce_int(value, field=name, minimum=4, maximum=72)
        elif token_type in {"ratio", "stroke"}:
            minimum, maximum = theme_token_bounds(name, token_type)
            normalized_tokens[name] = _coerce_float(
                value,
                field=name,
                minimum=minimum,
                maximum=maximum,
            )
        elif token_type in {"geometry", "alpha"}:
            minimum, maximum = theme_token_bounds(name, token_type)
            normalized_tokens[name] = _coerce_int(
                value,
                field=name,
                minimum=int(minimum),
                maximum=int(maximum),
            )
        elif token_type == "shadow":
            text = str(value or "").strip()
            if text != "none" and (
                len(text) > 120
                or not re.fullmatch(
                    r"-?\d+px\s+-?\d+px\s+\d+px(?:\s+\d+px)?\s+rgba?\([^)]+\)",
                    text,
                )
            ):
                raise ValueError(f"{name} 必须是 none 或受限 CSS shadow。")
            normalized_tokens[name] = text
    if normalized_tokens:
        normalized["tokens"] = normalized_tokens
    return normalized


def _mix_hex(left: str, right: str, ratio: float) -> str:
    if not (_HEX_COLOR_RE.fullmatch(left or "") and _HEX_COLOR_RE.fullmatch(right or "")):
        return left
    ratio = min(1.0, max(0.0, float(ratio)))
    left_rgb = [int(left[index : index + 2], 16) for index in (1, 3, 5)]
    right_rgb = [int(right[index : index + 2], 16) for index in (1, 3, 5)]
    values = [
        round(left_rgb[index] * (1.0 - ratio) + right_rgb[index] * ratio)
        for index in range(3)
    ]
    return "#" + "".join(f"{value:02x}" for value in values)


def resolve_theme(
    profile: dict[str, Any] | None,
    default_tokens: dict[str, Any],
    *,
    default_font_family: str = "Microsoft YaHei UI",
    default_mono_font_family: str = "Consolas",
) -> dict[str, Any]:
    profile = profile or {
        "id": DEFAULT_THEME_ID,
        "name": "默认主题",
        "base": DEFAULT_THEME_ID,
        "overrides": {},
    }
    overrides = validate_theme_overrides(profile.get("overrides") or {}, default_tokens)
    tokens = copy.deepcopy(default_tokens)
    explicit_tokens = dict(overrides.get("tokens") or {})
    tokens.update(explicit_tokens)

    inherited_region_tokens = {
        "sidebar_text": "text_secondary",
        "sidebar_text_muted": "text_tertiary",
        "sidebar_border": "separator",
        "chat_text": "text_primary",
        "chat_text_muted": "text_secondary",
        "chat_border": "border_subtle",
        "composer_bg": "bg_main",
        "composer_text": "text_primary",
        "composer_text_muted": "text_tertiary",
        "composer_border": "border_subtle",
        "right_sidebar_bg": "bg_main",
        "right_sidebar_header_bg": "bg_main",
        "right_sidebar_text": "text_primary",
        "right_sidebar_text_muted": "text_secondary",
        "right_sidebar_border": "separator",
        "management_bg": "bg_app",
        "management_panel_bg": "bg_main",
        "management_border": "separator",
        "overlay_bg": "bg_main",
        "overlay_text": "text_primary",
        "overlay_border": "border_default",
        "preview_shell_bg": "bg_main",
        "preview_shell_toolbar_bg": "bg_secondary",
        "preview_shell_text": "text_primary",
        "preview_shell_text_muted": "text_secondary",
        "preview_shell_border": "border",
        "scrollbar_track": "bg_app",
        "scrollbar_thumb": "border_strong",
        "scrollbar_thumb_hover": "text_tertiary",
        "icon_primary": "text_secondary",
        "icon_secondary": "text_tertiary",
        "icon_disabled": "text_disabled",
        "error_hover_bg": "error_bg",
        "error_pressed_bg": "error_border",
    }
    for target, source in inherited_region_tokens.items():
        if target in tokens and target not in explicit_tokens and source in tokens:
            tokens[target] = tokens[source]

    if "primary" in explicit_tokens:
        primary = explicit_tokens["primary"]
        surface = explicit_tokens.get("bg_main", tokens.get("bg_main", "#ffffff"))
        derived = {
            "primary_hover": _mix_hex(primary, "#000000", 0.12),
            "primary_pressed": _mix_hex(primary, "#000000", 0.22),
            "primary_soft": _mix_hex(surface, primary, 0.10),
            "primary_focus": _mix_hex(surface, primary, 0.28),
            "selection_bg": _mix_hex(surface, primary, 0.38),
            "accent_ai": primary,
            "info_icon": primary,
            "status_running": primary,
        }
        for name, value in derived.items():
            if name in tokens and name not in explicit_tokens:
                tokens[name] = value
        if "bg_user_bubble" in tokens and "bg_user_bubble" not in explicit_tokens:
            tokens["bg_user_bubble"] = tokens.get("primary_soft")

    font_scale = float(overrides.get("font_scale", 1.0))
    for name in FONT_SIZE_TOKENS:
        if name in tokens:
            tokens[name] = max(1, round(float(tokens[name]) * font_scale))
    density_multiplier = {"compact": 0.90, "standard": 1.0, "comfortable": 1.10}.get(
        overrides.get("density", "standard"),
        1.0,
    )
    for name in DENSITY_TOKENS:
        if name in tokens:
            tokens[name] = max(1, round(float(tokens[name]) * density_multiplier))
    radius_scale = float(overrides.get("radius_scale", 1.0))
    token_types = configurable_theme_tokens(default_tokens)
    for name, token_type in token_types.items():
        if token_type == "radius" and name in tokens:
            tokens[name] = max(0, round(float(tokens[name]) * radius_scale))

    geometry_errors = theme_geometry_errors(tokens)
    if geometry_errors:
        raise ValueError("主题尺寸关系无效：" + " ".join(geometry_errors))

    return {
        "id": str(profile.get("id") or DEFAULT_THEME_ID),
        "name": str(profile.get("name") or "默认主题"),
        "base": DEFAULT_THEME_ID,
        "font_family": overrides.get("font_family", default_font_family),
        "mono_font_family": overrides.get("mono_font_family", default_mono_font_family),
        "font_scale": font_scale,
        "density": overrides.get("density", "standard"),
        "radius_scale": radius_scale,
        "tokens": tokens,
        "overrides": overrides,
    }


def _hex_rgb(value: Any) -> tuple[float, float, float] | None:
    text = str(value or "").strip()
    if not _HEX_COLOR_RE.fullmatch(text):
        return None
    return tuple(int(text[index : index + 2], 16) / 255.0 for index in (1, 3, 5))


def _relative_luminance(value: Any) -> float | None:
    rgb = _hex_rgb(value)
    if rgb is None:
        return None
    channels = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in rgb
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def theme_contrast_warnings(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    """Return advisory WCAG contrast warnings without blocking theme use."""
    tokens = resolved.get("tokens") or {}
    pairs = (
        ("global", "text_primary", "bg_main", 4.5),
        ("global", "text_secondary", "bg_main", 4.5),
        ("left_sidebar", "sidebar_text", "bg_sidebar", 4.5),
        ("conversation", "chat_text", "bg_chat", 4.5),
        ("composer", "composer_text", "composer_bg", 4.5),
        ("right_sidebar", "right_sidebar_text", "right_sidebar_bg", 4.5),
        ("management", "text_primary", "management_panel_bg", 4.5),
        ("feedback", "overlay_text", "overlay_bg", 4.5),
        ("preview_shell", "preview_shell_text", "preview_shell_bg", 4.5),
        ("controls", "text_inverse", "primary", 4.5),
        ("feedback", "error_text", "error_bg", 4.5),
        ("feedback", "warning_text", "warning_bg", 4.5),
    )
    warnings: list[dict[str, Any]] = []
    for area, foreground, background, minimum in pairs:
        left = _relative_luminance(tokens.get(foreground))
        right = _relative_luminance(tokens.get(background))
        if left is None or right is None:
            continue
        lighter, darker = max(left, right), min(left, right)
        ratio = (lighter + 0.05) / (darker + 0.05)
        if ratio < minimum:
            warnings.append(
                {
                    "area": area,
                    "foreground": foreground,
                    "background": background,
                    "ratio": round(ratio, 2),
                    "recommended_minimum": minimum,
                }
            )
    return warnings


def validate_theme_document(
    payload: Any,
    default_tokens: dict[str, Any],
) -> dict[str, Any]:
    """Validate the portable JSON document shared by repository and import UI."""
    if not isinstance(payload, dict):
        raise ValueError("导入主题必须是 JSON 对象。")
    if payload.get("format") != "cowork-theme":
        raise ValueError("不是有效的 cowork-theme 文件。")
    unknown = sorted(set(payload) - {"format", "id", "name", "overrides"})
    if unknown:
        raise ValueError(f"主题文件包含未知字段：{', '.join(unknown)}")
    theme_id = str(payload.get("id") or "").strip()
    if (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", theme_id)
        or theme_id == DEFAULT_THEME_ID
    ):
        raise ValueError("主题文件 id 无效。")
    return {
        "id": theme_id,
        "name": _normalize_name(payload.get("name")),
        "overrides": validate_theme_overrides(
            payload.get("overrides") or {},
            default_tokens,
        ),
    }


@dataclass(frozen=True)
class ThemeStoreSnapshot:
    revision: int
    active_theme_id: str
    themes: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        if self.active_theme_id == DEFAULT_THEME_ID:
            return {}
        return {"active_theme_id": self.active_theme_id}


class ThemeRepository:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = os.path.abspath(data_dir or get_app_data_dir())
        self.themes_dir = os.path.join(self.data_dir, THEME_DIRECTORY_NAME)
        self.store_path = os.path.join(self.themes_dir, THEME_STORE_FILENAME)
        self.preview_path = os.path.join(self.themes_dir, THEME_PREVIEW_FILENAME)

    def theme_path(self, theme_id: str) -> str:
        normalized = str(theme_id or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", normalized):
            raise ValueError(f"主题 ID 不能用于文件名：{normalized}")
        if normalized == DEFAULT_THEME_ID:
            raise ValueError("默认主题没有用户 JSON 文件。")
        return os.path.join(self.themes_dir, normalized + ".json")

    def load(self) -> ThemeStoreSnapshot:
        payload = {}
        if os.path.exists(self.store_path):
            with open(self.store_path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if not isinstance(payload, dict):
                raise ValueError("themes/_state.json 根节点必须是对象。")
            unknown_state_fields = sorted(set(payload) - {"active_theme_id"})
            if unknown_state_fields:
                raise ValueError(
                    "themes/_state.json 包含未知字段："
                    + ", ".join(unknown_state_fields)
                )
        raw_themes = []
        if os.path.isdir(self.themes_dir):
            for filename in sorted(os.listdir(self.themes_dir)):
                if filename.startswith("_") or not filename.lower().endswith(".json"):
                    continue
                path = os.path.join(self.themes_dir, filename)
                with open(path, "r", encoding="utf-8") as stream:
                    raw = json.load(stream)
                if not isinstance(raw, dict) or raw.get("format") != "cowork-theme":
                    raise ValueError(f"主题文件格式无效：{path}")
                unknown_theme_fields = sorted(
                    set(raw) - {"format", "id", "name", "overrides"}
                )
                if unknown_theme_fields:
                    raise ValueError(
                        f"主题文件包含未知字段 {', '.join(unknown_theme_fields)}：{path}"
                    )
                raw = dict(raw)
                raw_themes.append(raw)
        themes = []
        seen_ids = set()
        seen_names = set()
        from .theme import default_design_tokens

        defaults = default_design_tokens()
        for raw in raw_themes:
            if not isinstance(raw, dict):
                raise ValueError("主题记录必须是对象。")
            theme_id = str(raw.get("id") or "").strip()
            if (
                not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", theme_id)
                or theme_id == DEFAULT_THEME_ID
                or theme_id in seen_ids
            ):
                raise ValueError(f"主题 ID 无效或重复：{theme_id or '<empty>'}")
            name = _normalize_name(raw.get("name"))
            name_key = name.casefold()
            if name_key in seen_names:
                raise ValueError(f"主题名称重复：{name}")
            themes.append(
                {
                    "id": theme_id,
                    "name": name,
                    "base": DEFAULT_THEME_ID,
                    "overrides": validate_theme_overrides(
                        raw.get("overrides") or {},
                        defaults,
                    ),
                    "created_at": int(raw.get("created_at") or 0),
                    "updated_at": int(raw.get("updated_at") or 0),
                }
            )
            seen_ids.add(theme_id)
            seen_names.add(name_key)
        active_theme_id = str(payload.get("active_theme_id") or DEFAULT_THEME_ID)
        if active_theme_id != DEFAULT_THEME_ID and active_theme_id not in seen_ids:
            raise ValueError(f"活动主题不存在：{active_theme_id}")
        canonical = {
            "active_theme_id": active_theme_id,
            "themes": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "overrides": item["overrides"],
                }
                for item in themes
            ],
        }
        revision = 0
        if payload or themes:
            digest = hashlib.sha256(
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            revision = int(digest[:15], 16)
        return ThemeStoreSnapshot(
            revision,
            active_theme_id,
            tuple(themes),
        )

    def _save(self, snapshot: ThemeStoreSnapshot) -> ThemeStoreSnapshot:
        next_snapshot = ThemeStoreSnapshot(
            snapshot.revision,
            snapshot.active_theme_id,
            snapshot.themes,
        )
        os.makedirs(self.themes_dir, exist_ok=True)
        desired_files = set()
        for item in next_snapshot.themes:
            path = self.theme_path(item.get("id"))
            desired_files.add(os.path.normcase(os.path.abspath(path)))
            _atomic_write_json(
                path,
                {
                    "format": "cowork-theme",
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "overrides": _json_copy(item.get("overrides") or {}),
                },
            )
        for filename in os.listdir(self.themes_dir):
            if filename.startswith("_") or not filename.lower().endswith(".json"):
                continue
            path = os.path.normcase(os.path.abspath(os.path.join(self.themes_dir, filename)))
            if path not in desired_files:
                os.unlink(path)
        _atomic_write_json(self.store_path, next_snapshot.to_dict())
        return self.load()

    def replace_state(
        self,
        *,
        themes: list[dict[str, Any]],
        active_theme_id: str,
        default_tokens: dict[str, Any],
        expected_revision: int | None = None,
        expected_theme_ids: set[str] | None = None,
    ) -> ThemeStoreSnapshot:
        current = self.load()
        if expected_revision is not None and current.revision != int(expected_revision):
            raise RuntimeError("主题目录已被其他操作更新，请刷新列表后重试。")
        if expected_theme_ids is not None:
            current_ids = {str(item.get("id") or "") for item in current.themes}
            normalized_expected_ids = {str(item or "") for item in expected_theme_ids}
            if current_ids != normalized_expected_ids:
                raise RuntimeError("主题文件夹内容已变化，请刷新列表后重试。")
        normalized_themes = []
        seen_ids = set()
        seen_names = set()
        now = int(time.time())
        for raw in themes:
            if not isinstance(raw, dict):
                raise ValueError("主题记录必须是对象。")
            theme_id = str(raw.get("id") or uuid.uuid4().hex).strip()
            if theme_id == DEFAULT_THEME_ID or theme_id in seen_ids:
                raise ValueError(f"主题 ID 无效或重复：{theme_id}")
            name = _normalize_name(raw.get("name"))
            if name.casefold() in seen_names:
                raise ValueError(f"主题名称重复：{name}")
            normalized_themes.append(
                {
                    "id": theme_id,
                    "name": name,
                    "base": DEFAULT_THEME_ID,
                    "overrides": validate_theme_overrides(
                        raw.get("overrides") or {},
                        default_tokens,
                    ),
                    "created_at": int(raw.get("created_at") or now),
                    "updated_at": now,
                }
            )
            seen_ids.add(theme_id)
            seen_names.add(name.casefold())
        active = str(active_theme_id or DEFAULT_THEME_ID)
        if active != DEFAULT_THEME_ID and active not in seen_ids:
            raise ValueError(f"活动主题不存在：{active}")
        return self._save(
            ThemeStoreSnapshot(current.revision, active, tuple(normalized_themes))
        )

    def get_theme(self, theme_id: str) -> dict[str, Any] | None:
        if theme_id == DEFAULT_THEME_ID:
            return {
                "id": DEFAULT_THEME_ID,
                "name": "默认主题",
                "base": DEFAULT_THEME_ID,
                "overrides": {},
            }
        for item in self.load().themes:
            if item.get("id") == theme_id:
                return _json_copy(item)
        return None

    def upsert_theme(
        self,
        *,
        name: str,
        overrides: dict[str, Any],
        default_tokens: dict[str, Any],
        theme_id: str = "",
    ) -> dict[str, Any]:
        snapshot = self.load()
        normalized_name = _normalize_name(name)
        normalized_overrides = validate_theme_overrides(overrides, default_tokens)
        themes = [_json_copy(item) for item in snapshot.themes]
        existing_index = next(
            (index for index, item in enumerate(themes) if item.get("id") == theme_id),
            -1,
        )
        for index, item in enumerate(themes):
            if index != existing_index and str(item.get("name") or "").casefold() == normalized_name.casefold():
                raise ValueError(f"主题名称已存在：{normalized_name}")
        now = int(time.time())
        if existing_index >= 0:
            existing = themes[existing_index]
            record = {
                **existing,
                "name": normalized_name,
                "overrides": normalized_overrides,
                "updated_at": now,
            }
            themes[existing_index] = record
        else:
            record = {
                "id": uuid.uuid4().hex,
                "name": normalized_name,
                "base": DEFAULT_THEME_ID,
                "overrides": normalized_overrides,
                "created_at": now,
                "updated_at": now,
            }
            themes.append(record)
        saved = self._save(
            ThemeStoreSnapshot(snapshot.revision, snapshot.active_theme_id, tuple(themes))
        )
        return {
            "theme": _json_copy(record),
            "revision": saved.revision,
        }

    def activate_theme(self, theme_id: str) -> ThemeStoreSnapshot:
        snapshot = self.load()
        target = str(theme_id or DEFAULT_THEME_ID).strip()
        valid_ids = {item.get("id") for item in snapshot.themes}
        if target != DEFAULT_THEME_ID and target not in valid_ids:
            raise ValueError(f"主题不存在：{target}")
        return self._save(ThemeStoreSnapshot(snapshot.revision, target, snapshot.themes))

    def delete_theme(self, theme_id: str) -> ThemeStoreSnapshot:
        target = str(theme_id or "").strip()
        if not target or target == DEFAULT_THEME_ID:
            raise ValueError("默认主题不能删除。")
        snapshot = self.load()
        themes = tuple(item for item in snapshot.themes if item.get("id") != target)
        if len(themes) == len(snapshot.themes):
            raise ValueError(f"主题不存在：{target}")
        active = DEFAULT_THEME_ID if snapshot.active_theme_id == target else snapshot.active_theme_id
        return self._save(ThemeStoreSnapshot(snapshot.revision, active, themes))

    def write_preview(
        self,
        *,
        name: str,
        overrides: dict[str, Any],
        default_tokens: dict[str, Any],
        session_id: str = "",
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        existing = self.load_preview() if replace_existing else None
        reuse_existing = bool(
            existing
            and str(existing.get("session_id") or "") == str(session_id or "")
        )
        now = int(time.time())
        payload = {
            "format": "cowork-theme-preview",
            "preview_id": (
                existing["preview_id"] if reuse_existing else uuid.uuid4().hex
            ),
            "revision": (
                int(existing.get("revision") or 0) + 1 if reuse_existing else 1
            ),
            "name": _normalize_name(name),
            "overrides": validate_theme_overrides(overrides, default_tokens),
            "session_id": str(session_id or ""),
            "created_at": (
                int(existing.get("created_at") or now) if reuse_existing else now
            ),
            "updated_at": now,
        }
        _atomic_write_json(self.preview_path, payload)
        return _json_copy(payload)

    def patch_preview(
        self,
        *,
        preview_id: str,
        preview_revision: int,
        set_overrides: dict[str, Any] | None,
        unset_tokens: list[str] | tuple[str, ...] | None,
        default_tokens: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.load_preview()
        if not current or current.get("preview_id") != str(preview_id or ""):
            raise ValueError("主题预览不存在。")
        if int(current.get("revision") or 0) != int(preview_revision):
            raise RuntimeError("主题预览已被更新，请重新检查后再修改。")
        merged = _json_copy(current.get("overrides") or {})
        incoming = dict(set_overrides or {})
        incoming_tokens = dict(incoming.pop("tokens", {}) or {})
        for name, value in incoming.items():
            merged[name] = value
        tokens = dict(merged.get("tokens") or {})
        tokens.update(incoming_tokens)
        for name in unset_tokens or ():
            tokens.pop(str(name or "").strip(), None)
        if tokens:
            merged["tokens"] = tokens
        else:
            merged.pop("tokens", None)
        current["overrides"] = validate_theme_overrides(merged, default_tokens)
        current["revision"] = int(current.get("revision") or 0) + 1
        current["updated_at"] = int(time.time())
        _atomic_write_json(self.preview_path, current)
        return _json_copy(current)

    def load_preview(self) -> dict[str, Any] | None:
        if not os.path.exists(self.preview_path):
            return None
        with open(self.preview_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("themes/_preview.json 根节点必须是对象。")
        if payload.get("format") != "cowork-theme-preview":
            raise ValueError("themes/_preview.json 格式无效。")
        if int(payload.get("revision") or 0) < 1:
            raise ValueError("themes/_preview.json revision 无效。")
        return payload

    def clear_preview(self, preview_id: str = "") -> bool:
        if not os.path.exists(self.preview_path):
            return False
        if preview_id:
            current = self.load_preview()
            if current and current.get("preview_id") != preview_id:
                raise ValueError("预览 ID 与当前主题预览不匹配。")
        os.unlink(self.preview_path)
        return True

    def commit_preview(
        self,
        *,
        preview_id: str,
        preview_revision: int | None = None,
        name: str = "",
        theme_id: str = "",
        activate: bool,
        default_tokens: dict[str, Any],
    ) -> dict[str, Any]:
        preview = self.load_preview()
        if not preview or preview.get("preview_id") != preview_id:
            raise ValueError("主题预览不存在。")
        if preview_revision is not None and int(preview.get("revision") or 0) != int(
            preview_revision
        ):
            raise RuntimeError("主题预览已更新，需要重新确认后才能保存。")
        result = self.upsert_theme(
            name=name or preview.get("name") or "自定义主题",
            overrides=preview.get("overrides") or {},
            default_tokens=default_tokens,
            theme_id=theme_id,
        )
        if activate:
            snapshot = self.activate_theme(result["theme"]["id"])
            result["revision"] = snapshot.revision
        self.clear_preview(preview_id)
        return result

    def export_theme(self, theme_id: str, default_tokens: dict[str, Any]) -> dict[str, Any]:
        theme = self.get_theme(theme_id)
        if not theme or theme.get("id") == DEFAULT_THEME_ID:
            raise ValueError("只能导出用户自定义主题。")
        return {
            "format": "cowork-theme",
            "id": theme.get("id"),
            "name": theme.get("name"),
            "overrides": validate_theme_overrides(theme.get("overrides") or {}, default_tokens),
        }

    def import_theme(self, payload: Any, default_tokens: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_theme_document(payload, default_tokens)
        return self.upsert_theme(
            name=normalized["name"],
            overrides=normalized["overrides"],
            default_tokens=default_tokens,
        )
