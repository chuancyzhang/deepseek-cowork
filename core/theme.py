import qdarktheme


class DesignTokens:
    primary = "#5e6ad2"
    primary_hover = "#4f5bc4"
    primary_pressed = "#4651b7"
    primary_soft = "#eef0ff"
    primary_focus = "#c9cdf7"
    selection_bg = "#b9c0f4"
    selection_text = "#17181c"
    primary_gradient_start = "#6975dc"
    primary_gradient_end = "#5e6ad2"

    accent_ai = "#5e6ad2"
    accent_user = "#4f566b"
    accent_success = "#2f9e64"
    accent_tool = "#b7791f"

    text_primary = "#202124"
    text_secondary = "#5f6269"
    text_tertiary = "#8b8e96"
    text_inverse = "#ffffff"
    text_disabled = "#a7a9af"

    bg_app = "#f7f7f8"
    bg_main = "#ffffff"
    bg_secondary = "#f4f4f5"
    bg_tertiary = "#ececee"
    bg_sidebar = "#f2f2f3"
    bg_card = "#ffffff"
    bg_card_subtle = "#fafafa"
    bg_glass = "#ffffff"
    bg_hover = "#e9e9eb"
    bg_pressed = "#e2e2e5"
    bg_disabled = "#f0f0f1"
    bg_code = "#f6f6f7"
    bg_panel = "#ffffff"
    bg_panel_strong = "#ffffff"
    bg_sidebar_selected = "#e7e8f8"
    bg_sidebar_hover = "#e8e8ea"
    bg_chat = "#ffffff"
    bg_user_bubble = "#eef0ff"
    bg_assistant_stream = "#ffffff"
    bg_settings_nav = "#f2f2f3"
    bg_settings_nav_selected = "#e5e6f5"
    bg_settings_summary = "#f4f4fb"

    border = "#d8d8dc"
    border_strong = "#c5c5cb"
    separator = "#e6e6e9"
    border_subtle = "#e8e8eb"
    border_panel = "#dedee2"
    border_settings_nav = "#dfdfe3"
    border_settings_summary = "#d9dcf6"

    radius_sm = 6
    radius_md = 8
    radius_lg = 10
    radius_xl = 14

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

    conversation_min_width = 840
    conversation_compact_min_width = 560
    conversation_max_width = 1040
    conversation_closed_min_width = 900
    conversation_closed_max_width = 1040
    conversation_closed_target_ratio = 0.76
    conversation_open_min_width = 840
    conversation_open_compact_min_width = 560
    conversation_open_max_width = 1040
    conversation_open_target_ratio = 0.96
    conversation_open_left_spacer_ratio = 0.40

    message_min_width = 720
    message_compact_min_width = 420
    message_max_width = 880
    message_width_ratio = 0.86
    user_bubble_min_width = 620
    user_bubble_compact_min_width = 360
    user_bubble_max_width = 640
    user_bubble_ratio = 0.88
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

    info_bg = "#f1f2fb"
    info_text = "#4f5bc4"
    info_border = "#d9dcf6"
    info_icon = "#5e6ad2"

    toast_bg = "rgba(255, 255, 255, 0.94)"
    toast_border = "#dde3ec"
    toast_shadow_alpha = 10
    toast_tint_success = "#eefaf3"
    toast_tint_error = "#fdf0f0"
    toast_tint_warning = "#fff7ea"
    toast_tint_info = "#eef5ff"

    muted_chip_bg = "#f0f0f3"
    muted_chip_text = "#636366"

    status_running = "#5e6ad2"
    status_thinking = "#7957c8"
    status_tool = "#a56a16"
    status_success = "#2f9e64"
    status_error = "#c83f49"
    status_idle = "#7d8088"

    activity_indicator_size = 14
    activity_indicator_stroke = 1.6
    activity_indicator_interval_ms = 70


def get_tech_stylesheet(theme="light"):
    is_dark = theme == "dark"

    if is_dark:
        c_bg_main = "#0d1117"
        c_bg_sidebar = "#010409"
        c_bg_card = "#161b22"
        c_bg_input = "#0d1117"
        c_text_primary = "#e6edf3"
        c_text_secondary = "#8b949e"
        c_text_tertiary = "#484f58"
        c_accent = "#0a84ff"
        c_accent_hover = "#409cff"
        c_border = "#30363d"
        c_selection = "#1f6feb"
    else:
        c_bg_main = DesignTokens.bg_main
        c_bg_sidebar = DesignTokens.bg_sidebar
        c_bg_card = DesignTokens.bg_card
        c_bg_input = DesignTokens.bg_secondary
        c_text_primary = DesignTokens.text_primary
        c_text_secondary = DesignTokens.text_secondary
        c_text_tertiary = DesignTokens.text_tertiary
        c_accent = DesignTokens.primary
        c_accent_hover = DesignTokens.primary_hover
        c_border = DesignTokens.border
        c_selection = DesignTokens.selection_bg

    css = f"""
    QWidget {{
        font-family: 'Microsoft YaHei UI', 'Segoe UI Variable', 'Segoe UI', sans-serif;
        font-size: 14px;
        color: {c_text_primary};
        selection-background-color: {c_selection};
        selection-color: {DesignTokens.selection_text};
    }}

    QMainWindow, QWidget#MainContainer {{
        background-color: {c_bg_main};
    }}

    QWidget#Sidebar, QWidget#RightSidebar {{
        background-color: {c_bg_sidebar};
    }}

    QFrame#ContentCard, QFrame#SkillCard, QFrame#PanelCard {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_lg}px;
    }}

    QPushButton {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_md}px;
        padding: 7px 12px;
        color: {c_text_primary};
        text-align: center;
    }}
    QPushButton:hover {{
        border-color: {DesignTokens.border_strong};
        background-color: {DesignTokens.bg_secondary};
    }}

    QPushButton#PrimaryBtn {{
        background-color: {c_accent};
        color: #ffffff;
        border: 1px solid {c_accent};
        font-weight: 600;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {c_accent_hover};
        border-color: {c_accent_hover};
    }}
    QPushButton#PrimaryBtn:pressed {{
        background-color: {DesignTokens.primary_pressed};
        border-color: {DesignTokens.primary_pressed};
    }}
    QPushButton:disabled {{
        background-color: {DesignTokens.bg_disabled};
        color: {DesignTokens.text_disabled};
        border-color: {DesignTokens.border_subtle};
    }}

    QPushButton#SecondaryBtn {{
        background-color: {DesignTokens.bg_main};
        color: {c_text_primary};
        border: 1px solid {c_border};
    }}

    QPushButton#GhostBtn {{
        background-color: transparent;
        border: none;
        color: {c_text_secondary};
    }}
    QPushButton#GhostBtn:hover {{
        color: {c_accent};
        background-color: {DesignTokens.bg_secondary};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
        background-color: {c_bg_input};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_md}px;
        padding: 7px 9px;
        color: {c_text_primary};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {c_accent};
        background-color: {c_bg_card};
    }}

    QTextEdit#MainInput {{
        font-size: 15px;
        border-radius: {DesignTokens.radius_lg}px;
        padding: 12px 16px;
        background-color: {c_bg_card};
    }}

    QTreeView, QListView, QListWidget {{
        background-color: {DesignTokens.bg_main};
        border: none;
        outline: none;
    }}
    QTreeView::item, QListWidget::item {{
        padding: 5px;
        border-radius: {DesignTokens.radius_sm}px;
        margin: 2px 4px;
    }}
    QTreeView::item:selected, QListWidget::item:selected {{
        background-color: {DesignTokens.primary_soft};
        color: {c_text_primary};
        border: 1px solid {c_accent};
    }}

    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background: transparent;
        padding: 8px 14px;
        margin-right: 4px;
        color: {c_text_secondary};
        font-weight: 500;
        border-radius: {DesignTokens.radius_sm}px;
    }}
    QTabBar::tab:hover {{
        color: {c_text_primary};
    }}
    QTabBar::tab:selected {{
        color: {c_text_primary};
        background: {DesignTokens.primary_soft};
    }}

    QScrollBar:vertical {{
        border: none;
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c_text_tertiary}55;
        min-height: 28px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c_text_tertiary}88;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        border: none;
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c_text_tertiary}55;
        min-width: 28px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    QLabel[roleTitle="true"] {{
        font-size: 18px;
        font-weight: 700;
        color: {c_text_primary};
    }}
    QLabel[roleSubtitle="true"] {{
        font-size: 13px;
        color: {c_text_secondary};
    }}

    QMenu {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        padding: 8px;
        border-radius: {DesignTokens.radius_lg}px;
    }}
    QMenu::item {{
        padding: 7px 24px 7px 12px;
        border-radius: {DesignTokens.radius_sm}px;
    }}
    QMenu::item:selected {{
        background-color: {c_accent};
        color: #ffffff;
    }}

    QToolTip {{
        background-color: {c_bg_main};
        color: {c_text_primary};
        border: 1px solid {c_border};
    }}
    """
    return css


def apply_theme(app, theme="auto"):
    mode = "light"
    base_sheet = qdarktheme.load_stylesheet(mode)
    tech_sheet = get_tech_stylesheet(mode)
    app.setStyleSheet(base_sheet + "\n" + tech_sheet)
    # Native tooltip windows still read palette roles on Windows. Keep these
    # colors identical to the deliberately minimal QToolTip stylesheet above;
    # translucent or rounded tooltip rules can render as black native windows.
    from PySide6.QtGui import QColor, QPalette
    palette = app.palette()
    palette.setColor(QPalette.ToolTipBase, QColor(DesignTokens.bg_main))
    palette.setColor(QPalette.ToolTipText, QColor(DesignTokens.text_primary))
    app.setPalette(palette)


def apply_tooltip_theme(app):
    """Apply only the Windows-safe tooltip surface without restyling the UI."""
    app.setStyleSheet(
        f"QToolTip {{ background-color: {DesignTokens.bg_main}; "
        f"color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border}; }}"
    )
    from PySide6.QtGui import QColor, QPalette
    palette = app.palette()
    palette.setColor(QPalette.ToolTipBase, QColor(DesignTokens.bg_main))
    palette.setColor(QPalette.ToolTipText, QColor(DesignTokens.text_primary))
    app.setPalette(palette)
