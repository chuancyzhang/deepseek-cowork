"""Framework-independent theme tokens shared by the desktop and theme tools."""
from .theme_service import configurable_theme_tokens, theme_token_bounds, theme_token_group


class DesignTokens:
    primary = "#404040"
    primary_hover = "#333333"
    primary_pressed = "#282828"
    primary_soft = "#f0f0f0"
    primary_focus = "#b8b8b8"
    selection_bg = "#d5d5d5"
    selection_text = "#242424"
    primary_gradient_start = "#404040"
    primary_gradient_end = "#404040"

    accent_ai = "#505050"
    accent_user = "#606060"
    accent_success = "#2f9e64"
    accent_tool = "#b7791f"

    text_primary = "#303030"
    text_secondary = "#686868"
    text_tertiary = "#767676"
    text_inverse = "#ffffff"
    text_disabled = "#a7a9af"

    bg_app = "#f8f8f8"
    bg_main = "#ffffff"
    bg_secondary = "#f5f5f5"
    bg_tertiary = "#eeeeee"
    bg_sidebar = "#f6f6f6"
    bg_card = "#ffffff"
    bg_card_subtle = "#fafafa"
    bg_glass = "#ffffff"
    bg_hover = "#eeeeee"
    bg_pressed = "#e4e4e4"
    bg_disabled = "#f3f3f3"
    bg_code = "#f6f6f6"
    bg_panel = "#ffffff"
    bg_panel_strong = "#ffffff"
    bg_sidebar_selected = "#eaeaea"
    bg_sidebar_hover = "#eeeeee"
    bg_chat = "#ffffff"
    bg_user_bubble = "#f3f3f3"
    bg_assistant_stream = "#ffffff"
    bg_settings_nav = "#f6f6f6"
    bg_settings_nav_selected = "#eaeaea"
    bg_settings_summary = "#f5f5f5"

    sidebar_text = "#686868"
    sidebar_text_muted = "#767676"
    sidebar_border = "#e6e6e6"
    chat_text = "#303030"
    chat_link = "#1f5fa8"
    chat_text_muted = "#686868"
    chat_border = "#e6e6e6"
    composer_bg = "#ffffff"
    composer_text = "#303030"
    composer_text_muted = "#767676"
    composer_border = "#e6e6e6"
    composer_reference_text = "#45688f"
    composer_reference_bg = "#f0f4f9"
    composer_skill_text = "#78618e"
    composer_skill_bg = "#f5f1f8"
    composer_knowledge_text = "#326f68"
    composer_knowledge_bg = "#eff6f4"
    composer_file_text = "#686868"
    composer_file_bg = "#f5f5f5"
    right_sidebar_bg = "#ffffff"
    right_sidebar_header_bg = "#ffffff"
    right_sidebar_text = "#303030"
    right_sidebar_text_muted = "#686868"
    right_sidebar_border = "#e6e6e6"
    management_bg = "#ffffff"
    management_panel_bg = "#ffffff"
    management_border = "#e6e6e6"
    overlay_bg = "#ffffff"
    overlay_text = "#303030"
    overlay_border = "#dedede"
    preview_shell_bg = "#ffffff"
    preview_shell_toolbar_bg = "#ffffff"
    preview_shell_text = "#303030"
    preview_shell_text_muted = "#686868"
    preview_shell_border = "#e6e6e6"
    scrollbar_track = "#f3f3f3"
    scrollbar_thumb = "#bcbcbc"
    scrollbar_thumb_hover = "#969696"
    icon_primary = "#303030"
    icon_secondary = "#686868"
    icon_disabled = "#a7a9af"

    border = "#dedede"
    border_strong = "#c7c7c7"
    separator = "#e6e6e6"
    border_subtle = "#e8e8e8"
    border_panel = "#e0e0e0"
    border_settings_nav = "#e6e6e6"
    border_settings_summary = "#e6e6e6"

    radius_sm = 6
    radius_md = 8
    radius_lg = 10
    radius_xl = 14
    composer_radius = 10
    preview_shell_radius = 8
    overlay_radius = 10

    # Product geometry. Keep these values on the 4 px grid so dialogs and the
    # main workspace use the same density instead of accumulating local sizes.
    control_height_sm = 28
    control_height = 32
    control_height_lg = 36
    row_height = 36
    row_height_comfortable = 44
    icon_size_sm = 14
    icon_size = 16
    icon_size_lg = 20
    scrollbar_width = 10
    chat_scrollbar_visual_width = 8
    chat_scrollbar_hit_width = 14
    chat_scrollbar_grab_padding = 6

    font_size_caption = 11
    font_size_meta = 12
    font_size_body = 14
    font_size_section = 16
    font_size_page = 20
    font_size_hero = 24
    font_weight_medium = 500
    font_weight_semibold = 600
    font_weight_bold = 700

    focus_ring_width = 2
    content_max_width = 1080
    settings_content_max_width = 1120
    management_split_threshold = 860
    settings_compact_threshold = 760

    spacing_xs = 4
    spacing_sm = 8
    spacing_md = 16
    spacing_lg = 24
    spacing_xl = 32
    spacing_2xl = 40

    shadow_sidebar = "0 18px 38px rgba(15, 23, 42, 0.07)"
    shadow_card = "0 12px 28px rgba(15, 23, 42, 0.06)"
    shadow_soft = "0 6px 16px rgba(15, 23, 42, 0.06)"

    sidebar_min_width = 204
    sidebar_width = 240
    sidebar_max_width = 320
    drawer_min_width = 272
    drawer_preferred_min_width = 360
    drawer_max_width = 500
    drawer_width_ratio = 0.28

    file_navigator_width = 320
    file_navigator_min_width = 240
    file_workbench_content_min_width = 280
    file_tab_preferred_width = 240
    file_tab_min_width = 120
    question_navigator_sidebar_gap = 8
    question_navigator_max_height = 280
    question_navigator_step = 16

    conversation_min_width = 680
    conversation_compact_min_width = 320
    conversation_preferred_width = 960
    conversation_max_width = 1040
    conversation_closed_min_width = 640
    conversation_closed_max_width = 1040
    conversation_closed_target_ratio = 0.84
    conversation_open_min_width = 560
    conversation_open_compact_min_width = 320
    conversation_open_max_width = 1040
    conversation_open_target_ratio = 0.96
    conversation_open_left_spacer_ratio = 0.40

    message_min_width = 560
    message_compact_min_width = 280
    message_max_width = 880
    message_width_ratio = 0.96
    user_bubble_min_width = 400
    user_bubble_compact_min_width = 220
    user_bubble_max_width = 640
    user_bubble_ratio = 0.88

    # Conversation timeline density. Assistant stages remain individually
    # visible, but share one compact rhythm inside the same turn group.
    chat_message_vertical_margin = 6
    assistant_stage_header_height = 28
    assistant_stage_content_gap = 4
    assistant_stage_separator_vertical_margin = 4
    assistant_stage_separator_indent = 32
    assistant_stage_separator_right_margin = 8
    assistant_thinking_timeline_indent = 8
    assistant_thinking_content_indent = 14
    user_message_padding_horizontal = 16
    user_message_padding_vertical = 12
    message_action_gap = 4
    toast_min_width = 260
    toast_max_width = 720
    toast_top_margin = 16
    toast_slide_distance = 8
    toast_enter_duration_ms = 180
    toast_exit_duration_ms = 140
    toast_default_duration_ms = 6000
    toast_warning_duration_ms = 8000
    toast_error_duration_ms = 10000
    toast_edge_margin = 16
    toast_stack_gap = 8
    toast_max_visible = 3

    success_bg = "#edf8f2"
    success_text = "#247a4d"
    success_border = "#c9ead8"
    success_icon = "#247a4d"
    success_accent = "#2f9e64"

    error_bg = "#fef2f2"
    error_hover_bg = "#fde8e8"
    error_pressed_bg = "#fbd5d5"
    error_text = "#991b1b"
    error_border = "#fecaca"
    error_icon = "#991b1b"

    warning_bg = "#fffbeb"
    warning_text = "#92400e"
    warning_border = "#fde68a"
    warning_icon = "#92400e"
    warning_panel_bg = "#fff7ed"
    warning_panel_border = "#fdba74"
    warning_panel_text = "#9a3412"

    info_bg = "#f3f3f3"
    info_text = "#5f5f5f"
    info_border = "#dedede"
    info_icon = "#686868"

    toast_bg = "rgba(255, 255, 255, 0.94)"
    toast_border = "#dde3ec"
    toast_shadow_alpha = 10
    toast_tint_success = "#eefaf3"
    toast_tint_error = "#fdf0f0"
    toast_tint_warning = "#fff7ea"
    toast_tint_info = "#f4f4f4"

    muted_chip_bg = "#f0f0f3"
    muted_chip_text = "#636366"

    status_running = "#686868"
    status_thinking = "#787878"
    status_tool = "#a56a16"
    status_success = "#2f9e64"
    status_error = "#c83f49"
    status_idle = "#7d8088"

    activity_indicator_size = 14
    activity_indicator_stroke = 1.6
    activity_indicator_interval_ms = 70


_DEFAULT_DESIGN_TOKEN_VALUES = {
    name: value
    for name, value in vars(DesignTokens).items()
    if not name.startswith("_") and isinstance(value, (str, int, float))
}


def default_design_tokens():
    """Return an immutable-source copy of the built-in appearance tokens."""
    return dict(_DEFAULT_DESIGN_TOKEN_VALUES)


def theme_token_schema():
    defaults = default_design_tokens()
    schema = {}
    for name, token_type in configurable_theme_tokens(defaults).items():
        item = {
            "type": token_type,
            "group": theme_token_group(name),
            "default": defaults[name],
            "description": name.replace("_", " "),
        }
        bounds = theme_token_bounds(name, token_type)
        if bounds is not None:
            item["minimum"], item["maximum"] = bounds
        schema[name] = item
    return schema


