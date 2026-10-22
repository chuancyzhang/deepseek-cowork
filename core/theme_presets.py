"""App-owned theme pairs. User packages remain separate and editable."""

import copy


BUILTIN_THEMES = {
    "default": ("简洁", "清晰、轻盈的日常工作台"),
    "builtin_paper": ("暖纸", "柔和纸色，适合长时间阅读"),
    "builtin_forest": ("青松", "低饱和绿色，安静而专注"),
    "builtin_mist": ("雾蓝", "冷静的灰蓝色层次"),
}


def builtin_theme(theme_id, *, dark=False):
    if theme_id not in BUILTIN_THEMES:
        return None
    name = BUILTIN_THEMES[theme_id][0]
    profile = {
        "format": "cowork-theme", "schema_version": 2, "id": theme_id,
        "name": name, "base": "default", "overrides": {}, "assets": {},
        "workspace_scene": {"attachment": "fixed", "layers": []},
        "surfaces": {}, "components": {}, "content": {},
    }
    # The default light appearance comes from the shared DesignTokens.
    if theme_id == "default" and not dark:
        return profile
    palettes = {
        "default": ("#f8f8f8", "#ffffff", "#f6f6f6", "#404040", "#f0f0f0"),
        "builtin_paper": ("#f4f1e9", "#fffdf8", "#ede9df", "#906b3e", "#f1e7d6"),
        "builtin_forest": ("#f0f4f1", "#fcfdfb", "#e7eee8", "#39735a", "#e3efe7"),
        "builtin_mist": ("#f0f3f7", "#fcfdff", "#e6ecf3", "#4e6e96", "#e5edf8"),
    }
    bg, panel, sidebar, accent, soft = palettes[theme_id]
    if dark:
        bg, panel, sidebar, soft = "#191c21", "#21252b", "#171a1e", "#303b4e"
        accent = {"default": "#d5d5d5", "builtin_paper": "#dfba83",
                  "builtin_forest": "#93c9ad", "builtin_mist": "#a2bddf"}[theme_id]
    text, muted, faint = ("#e9ebef", "#b3bac5", "#8c96a6") if dark else ("#262b30", "#606973", "#79838e")
    border = "#3a414b" if dark else "#dce0e1"
    hover = "#2c323a" if dark else soft
    if dark and theme_id == "default":
        bg, panel, sidebar, soft = "#1b1b1b", "#242424", "#202020", "#343434"
        text, muted, faint = "#ededed", "#b3b3b3", "#929292"
        border, hover = "#404040", "#303030"
    tokens = {}
    for key in ("bg_app", "management_bg", "bg_secondary", "bg_code", "bg_settings_summary", "bg_card_subtle"):
        tokens[key] = bg
    for key in ("bg_main", "bg_chat", "bg_card", "bg_panel", "bg_panel_strong", "bg_glass", "bg_assistant_stream", "composer_bg", "right_sidebar_bg", "right_sidebar_header_bg", "management_panel_bg", "overlay_bg", "preview_shell_bg", "preview_shell_toolbar_bg"):
        tokens[key] = panel
    for key in ("bg_sidebar", "bg_settings_nav", "scrollbar_track"):
        tokens[key] = sidebar
    for key in ("text_primary", "chat_text", "composer_text", "right_sidebar_text", "overlay_text", "preview_shell_text", "icon_primary"):
        tokens[key] = text
    for key in ("text_secondary", "sidebar_text", "chat_text_muted", "right_sidebar_text_muted", "preview_shell_text_muted", "icon_secondary"):
        tokens[key] = muted
    for key in ("text_tertiary", "text_disabled", "sidebar_text_muted", "composer_text_muted", "icon_disabled"):
        tokens[key] = faint
    for key in ("border", "border_strong", "separator", "border_subtle", "border_panel", "border_settings_nav", "border_settings_summary", "sidebar_border", "chat_border", "composer_border", "right_sidebar_border", "management_border", "overlay_border", "preview_shell_border"):
        tokens[key] = border
    for key in ("primary", "primary_hover", "primary_pressed", "primary_gradient_start", "primary_gradient_end", "accent_ai"):
        tokens[key] = accent
    for key in ("primary_soft", "primary_focus", "bg_sidebar_selected", "bg_settings_nav_selected", "bg_user_bubble", "selection_bg"):
        tokens[key] = soft
    for key in ("bg_hover", "bg_pressed", "bg_tertiary", "bg_disabled", "bg_sidebar_hover"):
        tokens[key] = hover
    tokens.update(selection_text=text, text_inverse="#171c22" if dark else "#ffffff",
                  scrollbar_thumb=faint, scrollbar_thumb_hover=muted)
    if dark:
        for prefix, foreground, background in (
            ("success", "#9bd8b5", "#213c30"), ("error", "#f6aaa9", "#472b2e"),
            ("warning", "#e7ca91", "#443a26"), ("info", "#b7c4ed", "#2c344a"),
        ):
            tokens.update({f"{prefix}_bg": background, f"{prefix}_text": foreground,
                           f"{prefix}_border": background, f"{prefix}_icon": foreground})
        tokens.update(warning_panel_bg="#443a26", warning_panel_text="#e7ca91",
                      warning_panel_border="#6a5734", error_hover_bg="#553035", error_pressed_bg="#61363b")
    profile["overrides"] = {"tokens": tokens}
    return copy.deepcopy(profile)
