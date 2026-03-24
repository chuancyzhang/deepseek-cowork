import qdarktheme


class DesignTokens:
    primary = "#2f6fed"
    primary_hover = "#245fce"
    primary_soft = "#eef4ff"
    primary_gradient_start = "#5384f7"
    primary_gradient_end = "#3f67df"

    accent_ai = "#4f7cf3"
    accent_user = "#4b5563"
    accent_success = "#10b981"
    accent_tool = "#d97706"

    text_primary = "#0f172a"
    text_secondary = "#475569"
    text_tertiary = "#94a3b8"
    text_inverse = "#ffffff"

    bg_app = "#f4f7fb"
    bg_main = "#ffffff"
    bg_secondary = "#f8fafc"
    bg_tertiary = "#eef2f7"
    bg_sidebar = "#f6f8fc"
    bg_card = "#ffffff"
    bg_card_subtle = "#fbfcfe"

    border = "#dbe3ee"
    border_strong = "#c7d2e3"

    radius_sm = 8
    radius_md = 12
    radius_lg = 18
    radius_xl = 24

    spacing_xs = 6
    spacing_sm = 10
    spacing_md = 16
    spacing_lg = 24
    spacing_xl = 32

    shadow_sidebar = "0 10px 30px rgba(15, 23, 42, 0.04)"
    shadow_card = "0 8px 24px rgba(15, 23, 42, 0.06)"
    shadow_soft = "0 2px 8px rgba(15, 23, 42, 0.06)"

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

    muted_chip_bg = "#eef2f7"
    muted_chip_text = "#475569"


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
        c_accent = "#2f81f7"
        c_accent_hover = "#58a6ff"
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
        font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
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
        border-radius: {DesignTokens.radius_md}px;
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
        border-radius: {DesignTokens.radius_md}px;
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
        padding: 10px 16px;
        margin-right: 4px;
        color: {c_text_secondary};
        font-weight: 500;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{
        color: {c_text_primary};
    }}
    QTabBar::tab:selected {{
        color: {c_accent};
        border-bottom: 2px solid {c_accent};
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
        padding: 6px;
        border-radius: {DesignTokens.radius_md}px;
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
