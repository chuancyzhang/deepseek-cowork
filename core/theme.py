import qdarktheme
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

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
    QLineEdit#MainInput {{
        font-size: 15px;
        border: 1px solid {c_border};
        border-radius: 20px; /* Pill shape */
        padding: 10px 16px;
        background-color: {c_bg_card};
    }}
    QLineEdit#MainInput:focus {{
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
        from PySide6.QtGui import QGuiApplication
        mode = "light"
        if hasattr(QGuiApplication.styleHints(), "colorScheme"):
            if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                mode = "dark"
    
    # 1. Load qdarktheme base
    base_sheet = qdarktheme.load_stylesheet(mode)
    
    # 2. Append our tech overrides
    tech_sheet = get_tech_stylesheet(mode)
    
    # Combine
    app.setStyleSheet(base_sheet + "\n" + tech_sheet)
