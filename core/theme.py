import qdarktheme


class DesignTokens:
    primary = "#007aff"
    primary_hover = "#0066d6"
    primary_soft = "#e8f2ff"
    primary_gradient_start = "#4aa3ff"
    primary_gradient_end = "#007aff"

    accent_ai = "#007aff"
    accent_user = "#3a3a3c"
    accent_success = "#34c759"
    accent_tool = "#ff9500"

    text_primary = "#1d1d1f"
    text_secondary = "#636366"
    text_tertiary = "#a1a1a6"
    text_inverse = "#ffffff"

    bg_app = "#f5f5f7"
    bg_main = "#ffffff"
    bg_secondary = "#f7f7f9"
    bg_tertiary = "#ececf0"
    bg_sidebar = "#fbfbfd"
    bg_card = "#ffffff"
    bg_card_subtle = "#fafafa"
    bg_glass = "rgba(255, 255, 255, 0.88)"
    bg_hover = "#f2f2f7"
    bg_code = "#fbfbfd"

    border = "#d2d2d7"
    border_strong = "#b8b8bf"
    separator = "#e5e5ea"

    radius_sm = 8
    radius_md = 12
    radius_lg = 16
    radius_xl = 22

    spacing_xs = 6
    spacing_sm = 10
    spacing_md = 16
    spacing_lg = 24
    spacing_xl = 32

    shadow_sidebar = "0 18px 44px rgba(0, 0, 0, 0.08)"
    shadow_card = "0 10px 30px rgba(0, 0, 0, 0.06)"
    shadow_soft = "0 4px 14px rgba(0, 0, 0, 0.06)"

    success_bg = "#f0fdf4"
    success_text = "#166534"
    success_border = "#bbf7d0"
    success_icon = "#166534"
    success_accent = "#10b981"

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

    info_bg = "#eff6ff"
    info_text = "#1e40af"
    info_border = "#bfdbfe"
    info_icon = "#1e40af"

    muted_chip_bg = "#f0f0f3"
    muted_chip_text = "#636366"

    status_running = "#007aff"
    status_thinking = "#5856d6"
    status_tool = "#ff9500"
    status_success = "#34c759"
    status_error = "#ff3b30"
    status_idle = "#8e8e93"


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
        c_selection = "#cfe0ff"

    css = f"""
    QWidget {{
        font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif;
        font-size: 14px;
        color: {c_text_primary};
        selection-background-color: {c_selection};
        selection-color: {c_text_primary};
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
        border-radius: 17px;
        padding: 8px 14px;
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
        border-radius: {DesignTokens.radius_lg}px;
        padding: 8px 10px;
        color: {c_text_primary};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {c_accent};
        background-color: {c_bg_card};
    }}

    QTextEdit#MainInput {{
        font-size: 15px;
        border-radius: {DesignTokens.radius_xl}px;
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
        border-radius: 14px;
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
        background-color: {c_bg_card};
        color: {c_text_primary};
        border: 1px solid {c_border};
        padding: 4px 8px;
        border-radius: {DesignTokens.radius_sm}px;
    }}
    """
    return css


def apply_theme(app, theme="auto"):
    mode = "light"
    base_sheet = qdarktheme.load_stylesheet(mode)
    tech_sheet = get_tech_stylesheet(mode)
    app.setStyleSheet(base_sheet + "\n" + tech_sheet)
