import qdarktheme
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

class DesignTokens:
    # Core Colors
    primary = "#2563eb"  # Blue 600
    primary_hover = "#1d4ed8" # Blue 700
    
    # Gradient for User Bubble
    primary_gradient_start = "#4d6bfe" 
    primary_gradient_end = "#3d5ce5"
    
    # Text
    text_primary = "#111827" # Gray 900
    text_secondary = "#6b7280" # Gray 500
    text_tertiary = "#9ca3af" # Gray 400
    
    # Borders & Backgrounds
    border = "#e5e7eb" # Gray 200
    bg_main = "#ffffff"
    bg_secondary = "#f9fafb" # Gray 50
    
    # Shadows
    shadow_sidebar = "2px 0 8px rgba(0,0,0,0.04)"
    shadow_card = "0 1px 3px rgba(0,0,0,0.1)"

    # Semantic Colors (Success, Error, Warning, Info)
    # Success
    success_bg = "#f0fdf4"
    success_text = "#166534"
    success_border = "#bbf7d0"
    success_icon = "#166534"
    success_accent = "#10b981" # Emerald 500
    
    # Error
    error_bg = "#fef2f2"
    error_text = "#991b1b"
    error_border = "#fecaca"
    error_icon = "#991b1b"
    
    # Warning
    warning_bg = "#fffbeb"
    warning_text = "#92400e"
    warning_border = "#fde68a"
    warning_icon = "#92400e"
    
    # Info
    info_bg = "#eff6ff"
    info_text = "#1e40af"
    info_border = "#bfdbfe"
    info_icon = "#1e40af"

def get_tech_stylesheet(theme="dark"):
    is_dark = theme == "dark"
    
    # Tech Palette
    # Dark: GitHub Dark Dimmed / VS Code Dark inspired
    # Light: Clean White / Google Material inspired
    
    if is_dark:
        c_bg_main = "#0d1117"      # Main window background
        c_bg_sidebar = "#010409"   # Sidebar background
        c_bg_card = "#161b22"      # Card/Container background
        c_bg_input = "#0d1117"     # Input field background
        
        c_text_primary = "#e6edf3"
        c_text_secondary = "#8b949e"
        c_text_tertiary = "#484f58"
        
        c_accent = "#2f81f7"       # Tech Blue
        c_accent_hover = "#58a6ff"
        
        c_border = "#30363d"       # Subtle border
        c_border_active = "#8b949e"
        
        c_success = "#238636"
        c_error = "#da3633"
        
        c_selection = "#1f6feb" # Selection background
    else:
        c_bg_main = "#ffffff"
        c_bg_sidebar = "#f6f8fa"
        c_bg_card = "#ffffff"
        c_bg_input = "#f6f8fa"
        
        c_text_primary = "#24292f"
        c_text_secondary = "#57606a"
        c_text_tertiary = "#8c959f"
        
        c_accent = "#0969da"
        c_accent_hover = "#2188ff"
        
        c_border = "#d0d7de"
        c_border_active = "#0969da"
        
        c_success = "#1a7f37"
        c_error = "#cf222e"
        
        c_selection = "#b3d7ff"

    css = f"""
    /* Global Font & Reset */
    QWidget {{
        font-family: 'Segoe UI', 'Microsoft YaHei', 'Roboto', sans-serif;
        font-size: 14px;
        color: {c_text_primary};
        selection-background-color: {c_selection};
        selection-color: {c_text_primary};
    }}
    
    /* Main Layout Areas */
    QMainWindow, QWidget#MainContainer {{
        background-color: {c_bg_main};
    }}
    
    QWidget#Sidebar, QWidget#RightSidebar {{
        background-color: {c_bg_sidebar};
        border-right: 1px solid {c_border};
    }}
    QWidget#RightSidebar {{
        border-right: none;
        border-left: 1px solid {c_border};
    }}

    /* Card Containers */
    QFrame#ContentCard, QFrame#SkillCard {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: 8px;
    }}
    
    /* Buttons */
    QPushButton {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: 6px;
        padding: 6px 12px;
        color: {c_text_primary};
        text-align: center;
    }}
    QPushButton:hover {{
        border-color: {c_text_secondary};
        background-color: {c_bg_sidebar};
    }}
    QPushButton:pressed {{
        background-color: {c_border};
    }}
    
    /* Primary Action Button (Solid Accent) */
    QPushButton#PrimaryBtn {{
        background-color: {c_accent};
        color: #ffffff;
        border: 1px solid {c_accent};
        font-weight: bold;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {c_accent_hover};
        border-color: {c_accent_hover};
    }}
    
    /* Ghost/Text Button */
    QPushButton#GhostBtn {{
        background-color: transparent;
        border: none;
        color: {c_text_secondary};
    }}
    QPushButton#GhostBtn:hover {{
        color: {c_accent};
        background-color: {c_bg_sidebar};
    }}

    /* Input Fields */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {c_bg_input};
        border: 1px solid {c_border};
        border-radius: 6px;
        padding: 8px;
        color: {c_text_primary};
    }}
    QLineEdit:focus, QTextEdit:focus {{
        border: 1px solid {c_accent};
        background-color: {c_bg_card};
    }}
    
    /* Search Box / Chips */
    QTextEdit#MainInput {{
        font-size: 15px;
        border: 1px solid {c_border};
        border-radius: 20px; /* Pill shape */
        padding: 10px 16px;
        background-color: {c_bg_card};
    }}
    QTextEdit#MainInput:focus {{
        border: 2px solid {c_accent};
    }}

    /* Lists & Trees */
    QTreeView, QListView {{
        background-color: {c_bg_sidebar};
        border: none;
        outline: none;
    }}
    QTreeView::item {{
        padding: 4px;
        border-radius: 4px;
        margin: 1px 4px;
    }}
    QTreeView::item:hover {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
    }}
    QTreeView::item:selected {{
        background-color: {c_accent}22; /* Transparent accent */
        color: {c_text_primary};
        border: 1px solid {c_accent};
    }}
    
    /* Tab Widget */
    QTabWidget::pane {{
        border: none;
        background: {c_bg_main};
    }}
    QTabBar::tab {{
        background: transparent;
        padding: 8px 16px;
        margin-bottom: 2px;
        color: {c_text_secondary};
        font-weight: 500;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{
        color: {c_text_primary};
        background-color: {c_bg_sidebar};
        border-radius: 4px;
    }}
    QTabBar::tab:selected {{
        color: {c_accent};
        border-bottom: 2px solid {c_accent};
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        border: none;
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c_text_tertiary}44;
        min-height: 30px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c_text_tertiary}88;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    
    /* Specific Labels */
    QLabel[roleTitle="true"] {{
        font-size: 20px;
        font-weight: 600;
        color: {c_text_primary};
    }}
    QLabel[roleSubtitle="true"] {{
        font-size: 13px;
        color: {c_text_secondary};
    }}
    
    /* Menus */
    QMenu {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        padding: 4px;
        border-radius: 6px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 12px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {c_accent};
        color: #ffffff;
    }}
    QMenu::separator {{
        height: 1px;
        background: {c_border};
        margin: 4px 0;
    }}
    
    /* Tooltips */
    QToolTip {{
        background-color: {c_bg_card};
        color: {c_text_primary};
        border: 1px solid {c_border};
        padding: 4px;
        border-radius: 4px;
    }}
    """
    return css

def apply_theme(app, theme="auto"):
    # Determine mode for our palette
    mode = theme
    if theme == "auto":
        # Check system (Qt6 specific)
        # User requested to disable dark mode detection for now as it may conflict with custom styling
        mode = "light" 
        # from PySide6.QtGui import QGuiApplication
        # if hasattr(QGuiApplication.styleHints(), "colorScheme"):
        #     if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        #         mode = "dark"
    
    # 1. Load qdarktheme base
    base_sheet = qdarktheme.load_stylesheet(mode)
    
    # 2. Append our tech overrides
    tech_sheet = get_tech_stylesheet(mode)
    
    # Combine
    app.setStyleSheet(base_sheet + "\n" + tech_sheet)
