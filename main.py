import sys
import subprocess
import tempfile
import os
import ast
import re
import json
import platform
import uuid
import glob
import markdown
from datetime import datetime
from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.agent import LLMWorker, CodeWorker
from core.skill_generator import SkillGenerator
from skills.skill_creator.impl import create_new_skill
from core.interaction import bridge
from core.env_utils import get_app_data_dir, get_base_dir
from core.theme import apply_theme, DesignTokens
import shutil
import qtawesome as qta
from PySide6.QtGui import (QAction, QTextOption, QIcon, QFont, QFontMetrics, QPixmap, 
                          QDesktopServices, QGuiApplication, QColor, QPainter, 
                          QBrush, QPainterPath, QTextCursor, QTextCharFormat)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox, QFileDialog, QScrollArea, QFrame, QDialog, QFormLayout, QCheckBox, QGroupBox, QInputDialog, QMenu, QTabWidget, QToolButton, QFileSystemModel, QTreeView, QSplitter, QStackedWidget, QSizePolicy, QGraphicsOpacityEffect, QGraphicsDropShadowEffect, QGridLayout)
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer, QSize, QRect, QPoint, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QAbstractAnimation, QVariantAnimation

# Try importing OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import qdarktheme
    QDARKTHEME_AVAILABLE = True
except ImportError:
    QDARKTHEME_AVAILABLE = False

# Global Menu Stylesheet to ensure consistency and force light theme
MENU_STYLESHEET = """
QMenu {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
    color: #24292f;
    background-color: transparent;
}
QMenu::item:selected {
    background-color: #0969da;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #d0d7de;
    margin: 4px 0;
}
"""

# --- Helper Classes for UI ---

class Avatar(QLabel):
    def __init__(self, role, size=36, parent=None): # 稍微加大一点尺寸到 36
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.role = role
        self.setAttribute(Qt.WA_TranslucentBackground) # 关键：设置背景透明，消除锯齿黑边
        self.setText("")
        
        # 预先生成图标，提升性能并确保尺寸一致
        # icon_size 设置为控件大小的 60%，视觉上更平衡
        icon_size = int(size * 0.6)
        
        if self.role == "User":
            self.bg_color = QColor("#4b5563") # 用户灰
            # 使用 user-alt 通常比 user 好看一点
            self.pixmap = qta.icon('fa5s.user', color='white').pixmap(icon_size, icon_size)
        else:
            self.bg_color = QColor("#4d6bfe") # DeepSeek 蓝
            # 也可以尝试 fa5s.brain 代表 AI，或者保持 fa5s.robot
            self.pixmap = qta.icon('fa5s.robot', color='white').pixmap(icon_size, icon_size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing) # 开启抗锯齿
        
        # 绘制圆形背景
        # 使用 fillPath 替代 setClipPath，边缘更平滑
        path = QPainterPath()
        # 稍微留一点边距(0.5px)避免边缘被切掉
        path.addEllipse(1, 1, self.width()-2, self.height()-2)
        painter.fillPath(path, self.bg_color)
        
        # 绘制居中图标
        if self.pixmap:
            # 使用浮点数计算中心点，虽然 drawPixmap 接受整数，但在小尺寸下逻辑更清晰
            x = (self.width() - self.pixmap.width()) // 2
            y = (self.height() - self.pixmap.height()) // 2
            painter.drawPixmap(x, y, self.pixmap)

class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(400, 200)
        self.config_manager = config_manager
        
        layout = QVBoxLayout(self)

        # API Key
        form_layout = QFormLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.config_manager.get("api_key", ""))
        form_layout.addRow("DeepSeek API Key:", self.api_key_input)
        
        # API Key Guide
        guide_label = QLabel('API Key 获取方法：<br>① 进入 <a href="https://platform.deepseek.com/">DeepSeek 官方开发者平台</a> 注册登录<br>② 在开发者平台首页 -> API keys -> 创建 API key')
        guide_label.setStyleSheet("color: #5f6368; font-size: 11px; margin-bottom: 8px;")
        guide_label.setOpenExternalLinks(True)
        guide_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        form_layout.addRow("", guide_label)
        
        # Base URL
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://api.deepseek.com")
        self.base_url_input.setText(self.config_manager.get("base_url", "https://api.deepseek.com"))
        form_layout.addRow("API Base URL (可选):", self.base_url_input)

        self.default_ws_input = QLineEdit()
        self.default_ws_input.setPlaceholderText("未设置")
        self.default_ws_input.setText(self.config_manager.get("default_workspace", ""))
        default_ws_container = QWidget()
        default_ws_layout = QHBoxLayout(default_ws_container)
        default_ws_layout.setContentsMargins(0, 0, 0, 0)
        default_ws_layout.addWidget(self.default_ws_input, 1)
        default_ws_btn = QPushButton("选择")
        default_ws_btn.setFixedWidth(60)
        default_ws_layout.addWidget(default_ws_btn)
        form_layout.addRow("默认工作区:", default_ws_container)

        def choose_default_workspace():
            directory = QFileDialog.getExistingDirectory(self, "选择默认工作区")
            if directory:
                self.default_ws_input.setText(directory)

        default_ws_btn.clicked.connect(choose_default_workspace)

        # Chat History Dir
        self.history_dir_input = QLineEdit()
        self.history_dir_input.setText(self.config_manager.get_chat_history_dir())
        history_dir_container = QWidget()
        history_dir_layout = QHBoxLayout(history_dir_container)
        history_dir_layout.setContentsMargins(0, 0, 0, 0)
        history_dir_layout.addWidget(self.history_dir_input, 1)
        history_dir_btn = QPushButton("选择")
        history_dir_btn.setFixedWidth(60)
        history_dir_layout.addWidget(history_dir_btn)
        form_layout.addRow("聊天记录存储:", history_dir_container)

        def choose_history_dir():
            directory = QFileDialog.getExistingDirectory(self, "选择聊天记录目录")
            if directory:
                self.history_dir_input.setText(directory)

        history_dir_btn.clicked.connect(choose_history_dir)
        
        # God Mode Toggle
        self.god_mode_check = QCheckBox("启用 God Mode (解除安全限制)")
        self.god_mode_check.setToolTip("警告：开启后，Agent 将拥有对全盘文件的访问权限，并可执行任意 Python 代码。\n请仅在您完全信任 Agent 操作时开启。")
        self.god_mode_check.setChecked(self.config_manager.get_god_mode())
        self.god_mode_check.setStyleSheet("QCheckBox { color: #d93025; font-weight: bold; }")
        form_layout.addRow("", self.god_mode_check)
        

        layout.addLayout(form_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def save_settings(self):
        # Save API Key
        self.config_manager.set("api_key", self.api_key_input.text().strip())
        # Save Base URL
        base_url = self.base_url_input.text().strip()
        if not base_url:
            base_url = "https://api.deepseek.com"
        self.config_manager.set("base_url", base_url)
        self.config_manager.set("default_workspace", self.default_ws_input.text().strip())
        self.config_manager.set_chat_history_dir(self.history_dir_input.text().strip())
        # Save God Mode
        self.config_manager.set_god_mode(self.god_mode_check.isChecked())
        
        self.accept()

class SkillsCenterDialog(QDialog):
    def __init__(self, skill_manager, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("功能中心 (Skills Center)")
        self.resize(600, 500)
        self.skill_manager = skill_manager
        self.config_manager = config_manager
        
        layout = QVBoxLayout(self)
        
        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        
        # Tab 1: Standard Skills
        self.tab_standard = QWidget()
        self.layout_standard = QVBoxLayout(self.tab_standard)
        self.scroll_standard = QScrollArea()
        self.scroll_standard.setWidgetResizable(True)
        self.content_standard = QWidget()
        self.layout_content_standard = QVBoxLayout(self.content_standard)
        self.layout_content_standard.addStretch()
        self.scroll_standard.setWidget(self.content_standard)
        self.layout_standard.addWidget(self.scroll_standard)
        self.tabs.addTab(self.tab_standard, "已安装的功能模块")
        
        # Tab 2: AI Generated Skills
        self.tab_ai = QWidget()
        self.layout_ai = QVBoxLayout(self.tab_ai)
        self.scroll_ai = QScrollArea()
        self.scroll_ai.setWidgetResizable(True)
        self.content_ai = QWidget()
        self.layout_content_ai = QVBoxLayout(self.content_ai)
        self.layout_content_ai.addStretch()
        self.scroll_ai.setWidget(self.content_ai)
        self.layout_ai.addWidget(self.scroll_ai)
        self.tabs.addTab(self.tab_ai, "AI 生成的技能")
        
        # Bottom Bar (Import & Refresh)
        bottom_layout = QHBoxLayout()
        import_btn = QPushButton(" 导入新功能包")
        import_btn.setIcon(qta.icon('fa5s.box-open', color='#374151'))
        import_btn.clicked.connect(self.import_skill)
        bottom_layout.addWidget(import_btn)
        
        refresh_btn = QPushButton(" 刷新列表")
        refresh_btn.setIcon(qta.icon('fa5s.sync', color='#374151'))
        refresh_btn.clicked.connect(self.manual_refresh)
        bottom_layout.addWidget(refresh_btn)

        bottom_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)
        layout.addLayout(bottom_layout)
        
        self.refresh_list()

    def manual_refresh(self):
        self.skill_manager.load_skills()
        self.refresh_list()
        QMessageBox.information(self, "刷新成功", "已重新扫描并加载所有技能模块。")

    def refresh_list(self):
        # Clear existing
        self._clear_layout(self.layout_content_standard)
        self._clear_layout(self.layout_content_ai)
        
        # Get skills
        skills = self.skill_manager.get_all_skills()
        for skill in skills:
            # Determine type
            is_ai = False
            if skill.get('type') == 'ai_generated' or skill.get('created_by') == 'ai':
                is_ai = True
            
            if is_ai:
                self.add_skill_card(skill, self.layout_content_ai)
            else:
                self.add_skill_card(skill, self.layout_content_standard)

    def _clear_layout(self, layout):
        while layout.count() > 1: # Keep stretch
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_skill_card(self, skill, parent_layout):
        card = QFrame()
        card.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        card.setStyleSheet("background-color: #f9f9f9; border-radius: 5px; margin-bottom: 5px;")
        
        h_layout = QHBoxLayout(card)
        
        # Info
        v_layout = QVBoxLayout()
        name_lbl = QLabel(f"{skill['name']}")
        name_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        desc_lbl = QLabel(skill.get('description_cn') or skill.get('description', ''))
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #555;")
        
        v_layout.addWidget(name_lbl)
        v_layout.addWidget(desc_lbl)

        # Dependencies
        deps = skill.get('dependencies', [])
        if deps and isinstance(deps, list):
            deps_str = ", ".join(deps)
            deps_lbl = QLabel(f" 依赖: {deps_str}")
            deps_lbl.setPixmap(qta.icon('fa5s.box', color='#1a73e8').pixmap(12, 12))
            deps_lbl = QLabel() # Re-create to use layout for icon+text or just text with emoji? 
            # Let's keep it simple with text but replace emoji with a small icon if possible or just text
            # Using simple text for now to avoid layout complexity in this list
            deps_lbl.setText(f"  依赖: {deps_str}")
            # Actually, let's use rich text to insert an icon or just use a simple char if qta is hard here
            # We can use a small pixmap label + text label in a horizontal layout
            
            deps_container = QWidget()
            deps_layout = QHBoxLayout(deps_container)
            deps_layout.setContentsMargins(0,0,0,0)
            deps_layout.setSpacing(4)
            
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon('fa5s.box', color='#1a73e8').pixmap(12, 12))
            icon_lbl.setFixedSize(14, 14)
            
            txt_lbl = QLabel(f"依赖: {deps_str}")
            txt_lbl.setStyleSheet("color: #1a73e8; font-size: 11px;")
            
            deps_layout.addWidget(icon_lbl)
            deps_layout.addWidget(txt_lbl)
            deps_layout.addStretch()
            
            v_layout.addWidget(deps_container)

        # Experience (Evolution)
        exp = skill.get('experience', [])
        if exp and isinstance(exp, list):
             exp_frame = QFrame()
             exp_frame.setStyleSheet("background-color: #f1f8e9; border-radius: 4px; padding: 4px; margin-top: 4px;")
             exp_layout = QVBoxLayout(exp_frame)
             exp_layout.setContentsMargins(4,4,4,4)
             exp_layout.setSpacing(2)
             
             header_container = QWidget()
             h_layout_exp = QHBoxLayout(header_container)
             h_layout_exp.setContentsMargins(0,0,0,0)
             h_layout_exp.setSpacing(4)
             
             exp_icon = QLabel()
             exp_icon.setPixmap(qta.icon('fa5s.chart-line', color='#33691e').pixmap(12, 12))
             exp_header = QLabel(f"进化记录 ({len(exp)})")
             exp_header.setStyleSheet("font-weight: bold; color: #33691e; font-size: 11px;")
             
             h_layout_exp.addWidget(exp_icon)
             h_layout_exp.addWidget(exp_header)
             h_layout_exp.addStretch()
             
             exp_layout.addWidget(header_container)
             
             for e in exp:
                 e_lbl = QLabel(f"• {e}")
                 e_lbl.setStyleSheet("color: #558b2f; font-size: 10px;")
                 e_lbl.setWordWrap(True)
                 exp_layout.addWidget(e_lbl)
             v_layout.addWidget(exp_frame)

        # Security Level
        if 'security_level' in skill:
             sec_lvl = skill['security_level']
             color = "#e67c73" if "high" in sec_lvl.lower() else "#fbbc04"
             
             sec_container = QWidget()
             sec_layout = QHBoxLayout(sec_container)
             sec_layout.setContentsMargins(0,4,0,0)
             sec_layout.setSpacing(4)
             
             sec_icon = QLabel()
             sec_icon.setPixmap(qta.icon('fa5s.shield-alt', color=color).pixmap(12, 12))
             
             sec_lbl = QLabel(f"安全等级: {sec_lvl}")
             sec_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
             
             sec_layout.addWidget(sec_icon)
             sec_layout.addWidget(sec_lbl)
             sec_layout.addStretch()
             
             v_layout.addWidget(sec_container)

        h_layout.addLayout(v_layout)
        
        # Controls
        c_layout = QVBoxLayout()
        c_layout.setAlignment(Qt.AlignCenter)
        
        is_enabled = skill['enabled']
        toggle_btn = QPushButton("已启用" if is_enabled else "已禁用")
        toggle_btn.setFixedWidth(80)
        toggle_btn.setCursor(Qt.PointingHandCursor)
        
        if is_enabled:
             toggle_btn.setStyleSheet("""
                QPushButton { background-color: #e6f4ea; color: #137333; border: none; border-radius: 4px; font-weight: bold; padding: 6px; }
                QPushButton:hover { background-color: #ceead6; }
             """)
             toggle_btn.setToolTip("点击禁用")
        else:
             toggle_btn.setStyleSheet("""
                QPushButton { background-color: #f1f3f4; color: #5f6368; border: none; border-radius: 4px; font-weight: bold; padding: 6px; }
                QPushButton:hover { background-color: #e8eaed; }
             """)
             toggle_btn.setToolTip("点击启用")

        toggle_btn.clicked.connect(lambda: self.toggle_skill(skill['name'], not is_enabled))
        
        c_layout.addWidget(toggle_btn)
        h_layout.addLayout(c_layout)
        
        # Insert before stretch
        parent_layout.insertWidget(parent_layout.count()-1, card)

    def toggle_skill(self, name, enabled):
        self.config_manager.set_skill_enabled(name, enabled)
        self.refresh_list()

    def import_skill(self):
        path = QFileDialog.getExistingDirectory(self, "选择功能包目录 (包含 SKILL.md)")
        if path:
            success, msg = self.skill_manager.import_skill(path)
            if success:
                QMessageBox.information(self, "成功", msg)
                self.refresh_list()
            else:
                QMessageBox.warning(self, "失败", msg)

class AutoResizingLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.setCursor(Qt.IBeamCursor)
        # Use a transparent background and specific text color
        self.setStyleSheet("background: transparent; border: none; color: #6b7280; font-size: 13px; font-family: 'Segoe UI', sans-serif;")

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        # 复制 (Copy)
        action_copy = QAction("复制", self)
        action_copy.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        action_copy.triggered.connect(lambda: QApplication.clipboard().setText(self.selectedText()))
        action_copy.setEnabled(self.hasSelectedText())
        menu.addAction(action_copy)
        
        # 全选 (Select All)
        action_select_all = QAction("全选", self)
        action_select_all.setIcon(qta.icon('fa5s.mouse-pointer', color='#4b5563'))
        action_select_all.triggered.connect(lambda: self.setSelection(0, len(self.text())))
        menu.addAction(action_select_all)
        
        menu.exec(event.globalPos())

class ReadOnlyTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        # 复制 (Copy)
        action_copy = QAction("复制", self)
        action_copy.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        action_copy.triggered.connect(self.copy)
        action_copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_copy)
        
        # 全选 (Select All)
        action_select_all = QAction("全选", self)
        action_select_all.setIcon(qta.icon('fa5s.mouse-pointer', color='#4b5563'))
        action_select_all.triggered.connect(self.selectAll)
        menu.addAction(action_select_all)
        
        menu.exec(event.globalPos())

class AutoResizingTextEdit(ReadOnlyTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        # self.setReadOnly(True) # Inherited
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.NoFrame)
        self.textChanged.connect(self.adjustHeight)
        self.setStyleSheet("background: transparent;")
        
        # Set word wrap mode to break anywhere if needed (for long strings)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

    def adjustHeight(self):
        doc_height = self.document().size().height()
        margins = self.contentsMargins()
        height = int(doc_height + margins.top() + margins.bottom())
        # Ensure minimum height to avoid invisible widget
        self.setFixedHeight(max(height, 24))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjustHeight()

class AutoResizingInputEdit(QTextEdit):
    returnPressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.NoFrame)
        self.textChanged.connect(self.adjustHeight)
        self.setFixedHeight(45) # Initial height
        self.min_height = 45
        self.max_height = 150
        self.anim = None
        
    def adjustHeight(self):
        doc_height = self.document().size().height()
        margins = self.contentsMargins()
        height = int(doc_height + margins.top() + margins.bottom())
        
        # Clamp height
        if height < self.min_height:
            height = self.min_height
        elif height > self.max_height:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            height = self.max_height
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            height = max(height, self.min_height)
            
        if self.height() != height:
            if self.anim: self.anim.stop()
            self.anim = QVariantAnimation()
            self.anim.setDuration(150)
            self.anim.setStartValue(self.height())
            self.anim.setEndValue(height)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
            self.anim.valueChanged.connect(lambda v: self.setFixedHeight(int(v)))
            self.anim.start()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # self.adjustHeight() # Avoid recursive loop or double adjust

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                event.ignore() 
                return
            elif os.path.isfile(path):
                self.insertPlainText(path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)
    
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        # Undo
        action_undo = QAction("撤销", self)
        action_undo.setIcon(qta.icon('fa5s.undo', color='#4b5563'))
        action_undo.triggered.connect(self.undo)
        action_undo.setEnabled(self.document().isUndoAvailable())
        menu.addAction(action_undo)
        
        # Redo
        action_redo = QAction("重做", self)
        action_redo.setIcon(qta.icon('fa5s.redo', color='#4b5563'))
        action_redo.triggered.connect(self.redo)
        action_redo.setEnabled(self.document().isRedoAvailable())
        menu.addAction(action_redo)
        
        menu.addSeparator()

        # Cut
        action_cut = QAction("剪切", self)
        action_cut.setIcon(qta.icon('fa5s.cut', color='#4b5563'))
        action_cut.triggered.connect(self.cut)
        action_cut.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_cut)

        # Copy
        action_copy = QAction("复制", self)
        action_copy.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        action_copy.triggered.connect(self.copy)
        action_copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_copy)
        
        # Paste
        action_paste = QAction("粘贴", self)
        action_paste.setIcon(qta.icon('fa5s.paste', color='#4b5563'))
        action_paste.triggered.connect(self.paste)
        action_paste.setEnabled(self.canPaste())
        menu.addAction(action_paste)
        
        menu.addSeparator()
        
        # Select All
        action_select_all = QAction("全选", self)
        action_select_all.setIcon(qta.icon('fa5s.mouse-pointer', color='#4b5563'))
        action_select_all.triggered.connect(self.selectAll)
        menu.addAction(action_select_all)
        
        menu.exec(event.globalPos())

class EmptyStateWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        
        # Icon
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.robot', color=DesignTokens.border).pixmap(64, 64))
        icon.setAlignment(Qt.AlignCenter)
        
        # Title
        title = QLabel("今天想处理什么文件？")
        title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {DesignTokens.text_primary};")
        title.setAlignment(Qt.AlignCenter)
        
        # Grid
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(24) # Increase spacing
        
        actions = [
            ("📁 整理文件", "按类型自动分类", "帮我把当前目录下的文件按类型分类整理"),
            ("🖼️ 处理图片", "批量重命名/压缩", "帮我把所有图片重命名为日期格式"),
            ("🔍 代码搜索", "在项目中查找内容", "搜索当前项目中关于 'TODO' 的代码"),
            ("📊 生成报告", "分析目录结构", "分析当前目录结构并生成一份报告")
        ]
        
        for i, (text, desc, prompt) in enumerate(actions):
            btn = self.create_action_card(text, desc, prompt)
            grid.addWidget(btn, i // 2, i % 2)
            
        layout.addStretch()
        layout.addWidget(icon)
        layout.addSpacing(24)
        layout.addWidget(title)
        layout.addSpacing(40)
        layout.addWidget(grid_widget)
        layout.addStretch()
        
    def create_action_card(self, title, desc, prompt):
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(140) # Significantly increase card height
        btn.setMinimumWidth(260) # Ensure sufficient width
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {DesignTokens.bg_main};
                border: 1px solid {DesignTokens.border};
                border-radius: 16px;
                padding: 24px;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 1px solid {DesignTokens.primary};
                background-color: {DesignTokens.bg_secondary};
            }}
        """)
        
        layout = QVBoxLayout(btn)
        layout.setSpacing(10) 
        
        t_label = QLabel(title)
        t_label.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {DesignTokens.text_primary}; background: transparent; border: none;") 
        
        d_label = QLabel(desc)
        d_label.setStyleSheet(f"font-size: 14px; color: {DesignTokens.text_secondary}; background: transparent; border: none;") 
        d_label.setWordWrap(True) # Ensure text is fully visible
        
        layout.addWidget(t_label)
        layout.addWidget(d_label)
        layout.addStretch() # Push content to top
        
        btn.clicked.connect(lambda: self.main_window.input_field.setText(prompt))
        return btn

class SystemToast(QFrame):
    """System Notification in Chat Stream"""
    def __init__(self, text, type="info"):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setAlignment(Qt.AlignCenter)
        
        icon_label = QLabel()
        if type == "error":
            icon_label.setPixmap(qta.icon('fa5s.times-circle', color=DesignTokens.error_icon).pixmap(16, 16))
            bg_color = DesignTokens.error_bg
            text_color = DesignTokens.error_text
            border_color = DesignTokens.error_border
        elif type == "success":
            icon_label.setPixmap(qta.icon('fa5s.check-circle', color=DesignTokens.success_icon).pixmap(16, 16))
            bg_color = DesignTokens.success_bg
            text_color = DesignTokens.success_text
            border_color = DesignTokens.success_border
        elif type == "warning":
            icon_label.setPixmap(qta.icon('fa5s.exclamation-triangle', color=DesignTokens.warning_icon).pixmap(16, 16))
            bg_color = DesignTokens.warning_bg
            text_color = DesignTokens.warning_text
            border_color = DesignTokens.warning_border
        else:
            icon_label.setPixmap(qta.icon('fa5s.info-circle', color=DesignTokens.info_icon).pixmap(16, 16))
            bg_color = DesignTokens.info_bg
            text_color = DesignTokens.info_text
            border_color = DesignTokens.info_border
            
        layout.addWidget(icon_label)
        
        msg_label = QLabel(text)
        msg_label.setStyleSheet(f"color: {text_color}; font-weight: 500; font-size: 13px; background: transparent;")
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(msg_label)
        
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                margin: 8px 40px;
            }}
        """)

class ChatBubble(QFrame):
    """Refined Chat Bubble component with Avatar and Better Thinking UI"""
    def __init__(self, role, text, thinking=None, duration=None):
        super().__init__()
        self.role = role
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWidth(0)
        
        # Main Horizontal Layout (Avatar | Content)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 10, 0, 10)
        main_layout.setSpacing(16)
        
        if role == "User":
            main_layout.setAlignment(Qt.AlignRight | Qt.AlignTop)
            
            # 1. Content Wrapper (to push content to right)
            content_wrapper = QWidget()
            cw_layout = QVBoxLayout(content_wrapper)
            cw_layout.setContentsMargins(0,0,0,0)
            
            # Bubble Frame
            bubble_frame = QFrame()
            bubble_frame.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                              stop:0 {DesignTokens.primary_gradient_start}, 
                                              stop:1 {DesignTokens.primary_gradient_end});
                    border-radius: 16px;
                    border-bottom-right-radius: 4px;
                }}
            """)
            bubble_layout = QVBoxLayout(bubble_frame)
            bubble_layout.setContentsMargins(16, 12, 16, 12)
            
            content_label = QLabel(text)
            content_label.setWordWrap(True)
            content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            content_label.setStyleSheet("color: #ffffff; font-size: 14px; line-height: 1.6; border: none; background: transparent;")
            
            # Smart Width: If text is long, force a minimum width to avoid narrow tall bubbles
            fm = QFontMetrics(content_label.font())
            # Check if text is long enough to warrant a wider bubble
            if len(text) > 50 or fm.horizontalAdvance(text) > 400:
                content_label.setMinimumWidth(400)
                
            bubble_layout.addWidget(content_label)
            
            cw_layout.addWidget(bubble_frame)
            
            # Add to main
            main_layout.addStretch() # Push everything right
            main_layout.addWidget(content_wrapper)
            
            # Avatar
            avatar = Avatar("User", 40)
            avatar_container = QWidget()
            avatar_layout = QVBoxLayout(avatar_container)
            avatar_layout.setContentsMargins(0, 5, 0, 0) # Top margin for alignment
            avatar_layout.setSpacing(0)
            avatar_layout.addWidget(avatar)
            avatar_layout.addStretch()
            main_layout.addWidget(avatar_container)

        else: # Agent
            main_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            
            # Avatar
            avatar = Avatar("Agent", 40)
            avatar_container = QWidget()
            avatar_layout = QVBoxLayout(avatar_container)
            avatar_layout.setContentsMargins(0, 5, 0, 0) # Top margin for alignment
            avatar_layout.setSpacing(0)
            avatar_layout.addWidget(avatar)
            avatar_layout.addStretch()
            main_layout.addWidget(avatar_container)
            
            # Content Column
            content_col = QWidget()
            content_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            col_layout = QVBoxLayout(content_col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(10)
            
            # 1. Thinking Section (DeepSeek Style - Grey Block)
            self.thinking_widget = QWidget()
            think_layout = QVBoxLayout(self.thinking_widget)
            think_layout.setContentsMargins(0, 0, 0, 0)
            think_layout.setSpacing(0)
            
            # Toggle Header
            self.think_toggle_btn = QPushButton(" 思考过程")
            self.think_toggle_btn.setIcon(qta.icon('fa5s.lightbulb', color='#f59e0b'))
            self.think_toggle_btn.setCursor(Qt.PointingHandCursor)
            self.think_toggle_btn.setCheckable(True)
            self.think_toggle_btn.setChecked(False)
            self.think_toggle_btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    background-color: #ffffff;
                    color: #6b7280;
                    border: 1px solid #e5e7eb;
                    border-radius: 18px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: 500;
                    margin-bottom: 8px;
                }
                QPushButton:hover { background-color: #f9fafb; color: #4b5563; border-color: #d1d5db; }
                QPushButton:checked { 
                    background-color: #f8fafc; 
                    color: #475569; 
                    border-color: #cbd5e1; 
                    border-bottom-left-radius: 0; 
                    border-bottom-right-radius: 0; 
                }
            """)
            self.think_toggle_btn.toggled.connect(self.toggle_thinking)
            think_layout.addWidget(self.think_toggle_btn)

            # Container for Thinking Stream
            self.think_container = QWidget()
            self.think_container.setVisible(False)
            self.think_container.setStyleSheet("""
                QWidget {
                    background: #f9fafb;
                    border: 1px solid #e5e7eb;
                    border-top: none;
                    margin-top: -1px;
                    margin-left: 0px;
                    border-bottom-left-radius: 18px;
                    border-bottom-right-radius: 18px;
                }
            """)
            self.think_container_layout = QVBoxLayout(self.think_container)
            self.think_container_layout.setContentsMargins(12, 12, 12, 12)
            self.think_container_layout.setSpacing(8)
            self._start_new_think_segment = False
            self._last_thinking_segment_text = ""
            self._strip_prefix = ""
            
            think_layout.addWidget(self.think_container)
            col_layout.addWidget(self.thinking_widget)
            
            # 2. Main Content
            self.content_edit = AutoResizingTextEdit()
            self.content_edit.setStyleSheet("background: transparent; border: none; padding: 0;")
            col_layout.addWidget(self.content_edit)
            
            # Handle Initial State
            if thinking == "...":
                self.set_thinking_state(True)
            elif thinking:
                self.update_thinking(thinking, duration, is_final=True)
            else:
                self.thinking_widget.setVisible(False)
                
            if text:
                self.set_main_content(text)
                
            main_layout.addWidget(content_col)
            # main_layout.addStretch() # Removed to allow content to take full width

    def toggle_thinking(self, checked):
        # Animation for Folding
        if not hasattr(self, 'think_animation'):
             self.think_animation = QPropertyAnimation(self.think_container, b"maximumHeight")
             self.think_animation.setEasingCurve(QEasingCurve.OutCubic)
             self.think_animation.setDuration(300)

        # Calculate target height
        # Since we can't easily get exact height if it's dynamic and hidden,
        # we can set a large max height for open, and 0 for closed.
        # Or better: use sizeHint if visible, or a large number.
        
        # When opening
        if checked:
            self.think_container.setVisible(True)
            self.think_container.setMaximumHeight(0) # Start from 0
            
            # Disconnect previous connections to avoid conflict (e.g. setVisible(False) from closing)
            try: self.think_animation.finished.disconnect() 
            except: pass
            
            # We need to force layout to calculate size
            self.think_container.adjustSize() 
            # This might be tricky with dynamic content. 
            # Simple approach: Animate to a large value (e.g. 1000 or 5000), 
            # then remove constraint or set to minimum required.
            
            # Better approach for smooth slide:
            # 1. Get total height of content
            total_height = self.think_container_layout.sizeHint().height()
            # If sizeHint is small (hidden), try measure content
            if total_height < 50: total_height = 800 # Fallback
            
            self.think_animation.setStartValue(0)
            self.think_animation.setEndValue(total_height)
            self.think_animation.finished.connect(lambda: self.think_container.setMaximumHeight(16777215)) # Reset to QWIDGETSIZE_MAX
            self.think_animation.start()
            
        else:
            # Closing
            current_h = self.think_container.height()
            self.think_animation.setStartValue(current_h)
            self.think_animation.setEndValue(0)
            # Disconnect previous connections to avoid stacking
            try: self.think_animation.finished.disconnect() 
            except: pass
            self.think_animation.finished.connect(lambda: self.think_container.setVisible(False))
            self.think_animation.start()
            
        # Use Chevron or similar, but keep the Lightbulb fixed
        text = self.think_toggle_btn.text()
        base_text = " 思考过程"
        
        # If we have duration in text
        if "(" in text:
             parts = text.split("(")
             duration_part = "(" + parts[1]
             base_text = f" 思考过程 {duration_part}"
             
        if checked:
             self.think_toggle_btn.setText(base_text) # Maybe add arrow if needed, but styling shows state
        else:
             self.think_toggle_btn.setText(base_text)
        
    def set_thinking_state(self, is_thinking):
        if is_thinking:
            self.think_toggle_btn.setText(" 思考中…")
            self.think_toggle_btn.setChecked(True)
            self.thinking_widget.setVisible(True)

    def get_active_think_widget(self, force_new=False):
        if not force_new:
            count = self.think_container_layout.count()
            if count > 0:
                item = self.think_container_layout.itemAt(count - 1)
                widget = item.widget()
                if isinstance(widget, AutoResizingLabel):
                    return widget

        new_widget = AutoResizingLabel()
        self.think_container_layout.addWidget(new_widget)
        new_widget.show()
        return new_widget

    def update_thinking(self, text=None, duration=None, is_final=False):
        if text is not None:
            # Simple streaming append for now
            widget = self.get_active_think_widget()
            current = widget.text()
            widget.setText(current + text)
        
        if duration:
            self.think_toggle_btn.setText(f" 深度思考 ({duration:.1f}s)")
        
        if is_final:
            self.think_toggle_btn.setChecked(False) # Collapse by default when done
            
    def set_main_content(self, text):
        try:
            # GitHub-like CSS for Markdown
            style = """
            <style>
               body { 
                   font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                   line-height: 1.6; 
                   color: #1f2937; 
                   margin: 0; 
                   font-size: 14px;
               }
               p { margin-top: 0; margin-bottom: 12px; }
               pre { 
                   background-color: #f3f4f6; 
                   padding: 12px; 
                   border-radius: 6px; 
                   border: 1px solid #e5e7eb; 
                   white-space: pre-wrap; 
                   margin-bottom: 12px;
                   font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
               }
               code { 
                   font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; 
                   font-size: 90%; 
                   padding: 0.2em 0.4em; 
                   background-color: #f3f4f6; 
                   border-radius: 4px; 
               }
               h1, h2, h3 { color: #111827; font-weight: 600; margin-top: 24px; margin-bottom: 12px; }
               h1 { font-size: 1.5em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3em; }
               h2 { font-size: 1.3em; }
               a { color: #2563eb; text-decoration: none; }
               blockquote { 
                   border-left: 3px solid #d1d5db; 
                   color: #4b5563; 
                   padding-left: 1em; 
                   margin: 0 0 16px 0; 
               }
               table { 
                   border-collapse: separate; 
                   border-spacing: 0; 
                   width: 100%; 
                   margin-bottom: 16px; 
                   font-size: 13px; 
                   border: 1px solid #e5e7eb;
                   border-radius: 6px;
                   overflow: hidden;
               }
               th, td { 
                   border-bottom: 1px solid #e5e7eb; 
                   border-right: 1px solid #e5e7eb; 
                   padding: 8px 12px; 
                   text-align: left; 
               }
               th { 
                   background-color: #f8fafc; 
                   font-weight: 600; 
                   color: #4b5563;
                   border-bottom: 1px solid #e5e7eb;
               }
               tr:last-child td { border-bottom: none; }
               tr:hover td { background-color: #f8fafc; }
               th:last-child, td:last-child { border-right: none; }
            </style>
            """
            html_content = markdown.markdown(text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])
            self.content_edit.setHtml(style + html_content)
        except Exception:
            self.content_edit.setPlainText(text)
        self.content_edit.adjustHeight()
        
    def add_tool_card(self, card_widget, session_id=None):
        # Tools inside thinking container? Or after?
        # DeepSeek puts tool calls usually in the thought process or just after.
        # Let's put them in the thought container if visible, else append to content column.
        
        # We'll put it in the Thinking Container for a cleaner log look
        self.think_container_layout.addWidget(card_widget)
        self._start_new_think_segment = True
        
        # Ensure thinking is accessible
        self.thinking_widget.setVisible(True)
        # If we are streaming and a tool is called, expand to show it
        if not self.think_toggle_btn.isChecked():
            self.think_toggle_btn.setChecked(True)

class ToolCallCard(QFrame):
    clicked = Signal(str, str, str) # tool_id, args, result

    def __init__(self, tool_name, args, tool_id):
        super().__init__()
        self.tool_id = tool_id
        self.args = args
        self.result = ""
        self.tool_name = tool_name
        self.is_selected = False
        
        self.setFocusPolicy(Qt.StrongFocus)
        
        self.setFrameShape(QFrame.NoFrame)
        # Minimalist "List Item" Style
        self.setStyleSheet("""
            ToolCallCard {
                background-color: transparent;
                border: none;
                margin: 2px 0;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # --- Main Row Container (The "List Item") ---
        self.main_row = QFrame()
        self.main_row.setCursor(Qt.PointingHandCursor)
        self.main_row.setStyleSheet(f"""
            QFrame {{
                background-color: {DesignTokens.bg_main};
                border: 1px solid {DesignTokens.border};
                border-left: 3px solid {DesignTokens.text_tertiary};
                border-radius: 6px;
            }}
            QFrame:hover {{
                background-color: {DesignTokens.bg_secondary};
                border-color: {DesignTokens.text_secondary};
                border-left-color: {DesignTokens.text_secondary};
            }}
        """)
        # Make the whole card clickable
        self.main_row.mousePressEvent = self.on_card_clicked
        
        row_layout = QHBoxLayout(self.main_row)
        row_layout.setContentsMargins(12, 10, 12, 10)
        row_layout.setSpacing(12)
        
        # 1. Icon Area 
        tool_icons = {
            "list_files": "fa5s.folder", "read_file": "fa5s.book-open", "write_file": "fa5s.pen-alt",
            "update_file": "fa5s.pen", "delete_file": "fa5s.trash-alt", "run_command": "fa5s.terminal",
            "open_preview": "fa5s.compass", "search_codebase": "fa5s.search", "grep": "fa5s.filter",
            "glob": "fa5s.globe", "web_search": "fa5s.globe-americas", "get_diagnostics": "fa5s.stethoscope",
        }
        icon_name = tool_icons.get(tool_name, "fa5s.tools")
        
        # Icon with base
        self.icon_label = QLabel()
        self.icon_label.setPixmap(qta.icon(icon_name, color=DesignTokens.text_secondary).pixmap(16, 16))
        self.icon_label.setFixedSize(28, 28)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet(f"""
            background-color: {DesignTokens.bg_secondary};
            color: {DesignTokens.text_secondary};
            border-radius: 14px; 
        """)
        
        # 2. Text Content
        text_container = QWidget()
        text_container.setStyleSheet("background: transparent; border: none;")
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        
        # Title
        name_label = QLabel(f"{tool_name}")
        name_label.setStyleSheet(f"font-weight: 600; color: {DesignTokens.text_primary}; font-size: 13px; border: none;")
        
        # Subtitle (Short Args Summary)
        short_args = str(args)
        if len(short_args) > 60:
            short_args = short_args[:60] + "..."
        args_preview = QLabel(short_args)
        args_preview.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 11px; border: none;")
        
        text_layout.addWidget(name_label)
        text_layout.addWidget(args_preview)
        
        # 3. Right Side Controls
        self.status_icon = QLabel() # Default running
        self.status_icon.setPixmap(qta.icon('fa5s.spinner', color=DesignTokens.text_secondary, animation=qta.Spin(self.status_icon)).pixmap(14, 14))
        self.status_icon.setStyleSheet("border: none; background: transparent;")
        
        self.view_btn = QPushButton("查看")
        self.view_btn.setCursor(Qt.PointingHandCursor)
        self.view_btn.setFixedWidth(40)
        self.view_btn.setToolTip("在右侧查看详情")
        self.view_btn.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 4px;
                background: {DesignTokens.bg_secondary};
                color: {DesignTokens.text_secondary};
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {DesignTokens.primary};
                background: #eff6ff;
            }}
        """)
        self.view_btn.clicked.connect(lambda: self.clicked.emit(self.tool_id, str(self.args), str(self.result)))

        row_layout.addWidget(self.icon_label)
        row_layout.addWidget(text_container, 1) # Expand
        row_layout.addWidget(self.status_icon)
        row_layout.addWidget(self.view_btn)
        
        layout.addWidget(self.main_row)

        # 4. Sub-agents Container (Hidden by default)
        self.sub_agents_container = QWidget()
        self.sub_agents_layout = QVBoxLayout(self.sub_agents_container)
        self.sub_agents_layout.setContentsMargins(32, 4, 4, 4) # Indent
        self.sub_agents_layout.setSpacing(4)
        self.sub_agents_container.setVisible(False)
        layout.addWidget(self.sub_agents_container)
        
        self.sub_agent_widgets = {}

    def update_agent_state(self, state):
        agent_id = state.get("agent_id")
        status = state.get("status")
        task = state.get("task", "")
        
        if not agent_id: return
        
        if not self.sub_agents_container.isVisible():
            self.sub_agents_container.setVisible(True)
            
        if agent_id not in self.sub_agent_widgets:
            # Create new row for agent
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            
            icon = QLabel()
            icon.setPixmap(qta.icon('fa5s.robot', color='#6b7280').pixmap(12, 12))
            
            name = QLabel(agent_id)
            name.setStyleSheet("font-weight: bold; color: #4b5563; font-size: 11px;")
            
            status_label = QLabel(status)
            status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
            
            row_layout.addWidget(icon)
            row_layout.addWidget(name)
            row_layout.addWidget(status_label, 1) # Expand
            
            self.sub_agents_layout.addWidget(row_widget)
            self.sub_agent_widgets[agent_id] = {
                "widget": row_widget,
                "status_label": status_label
            }
        
        # Update status
        widgets = self.sub_agent_widgets[agent_id]
        status_text = f"{status}"
        
        # Default style
        style = "color: #6b7280; font-size: 11px;"
        
        if status == "pending":
            status_text = f"Pending: {task[:30]}..." if task else "Pending"
        elif status == "completed":
            status_text = "Completed"
            style = "color: #10b981; font-size: 11px; font-weight: bold;"
        elif status == "active":
             status_text = "Running..."
             style = "color: #3b82f6; font-size: 11px;"
        elif status == "thinking":
            status_text = "Thinking..." 
            style = "color: #6366f1; font-size: 11px; font-style: italic;"
        elif status == "tool_use":
            # task contains "Tool: <name>"
            status_text = f"Action: {task}"
            style = "color: #f59e0b; font-size: 11px; font-weight: bold;"
        
        widgets["status_label"].setText(status_text)
        widgets["status_label"].setStyleSheet(style)

    def focusInEvent(self, event):
        if not self.is_selected:
            self.main_row.setStyleSheet(f"""
                QFrame {{
                    background-color: {DesignTokens.bg_main};
                    border: 1px solid {DesignTokens.primary};
                    border-left: 3px solid {DesignTokens.primary};
                    border-radius: 6px;
                }}
            """)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.set_selected(self.is_selected)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.on_card_clicked(None)
        else:
            super().keyPressEvent(event)

    def on_card_clicked(self, event):
        self.clicked.emit(self.tool_id, str(self.args), str(self.result))

    def set_selected(self, selected):
        self.is_selected = selected
        if selected:
            # Selected: Blue Border + Light Blue BG
            self.main_row.setStyleSheet(f"""
                QFrame {{
                    background-color: {DesignTokens.info_bg};
                    border: 1px solid {DesignTokens.primary};
                    border-left: 3px solid {DesignTokens.primary};
                    border-radius: 6px;
                }}
            """)
        else:
            # Normal: Border Color based on Status
            left_color = DesignTokens.success_accent if self.result else DesignTokens.text_tertiary
            self.main_row.setStyleSheet(f"""
                QFrame {{
                    background-color: {DesignTokens.bg_main};
                    border: 1px solid {DesignTokens.border};
                    border-left: 3px solid {left_color};
                    border-radius: 6px;
                }}
                QFrame:hover {{
                    background-color: {DesignTokens.bg_secondary};
                    border-color: {DesignTokens.text_secondary};
                    border-left-color: {DesignTokens.text_secondary};
                }}
            """)

    def set_result(self, result_text):
        self.status_icon.setPixmap(qta.icon('fa5s.check-circle', color=DesignTokens.success_accent).pixmap(14, 14))
        self.result = result_text
        
        # Update style to show success (Green left border)
        if not self.is_selected:
            self.main_row.setStyleSheet(f"""
                QFrame {{
                    background-color: {DesignTokens.bg_main};
                    border: 1px solid {DesignTokens.border};
                    border-left: 3px solid {DesignTokens.success_accent};
                    border-radius: 6px;
                }}
                QFrame:hover {{
                    background-color: {DesignTokens.bg_secondary};
                    border-color: {DesignTokens.text_secondary};
                    border-left-color: {DesignTokens.success_accent};
                }}
            """)

class SubAgentMonitor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #f3f4f6;
                color: #6b7280;
                padding: 6px 12px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #3b82f6;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.tabs)
        
        self.agents = {} # {agent_id: {"text_edit": QTextEdit}}

    def update_log(self, agent_id, content, status):
        if agent_id not in self.agents:
            self._create_agent_tab(agent_id)
            
        widgets = self.agents[agent_id]
        text_edit = widgets["text_edit"]
        
        # Timestamp
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        
        # Determine format
        if status == "pending":
            # New task started, clear previous log
            text_edit.clear()
            
            cursor = text_edit.textCursor()
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#111827"))
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontPointSize(12)
            cursor.insertText(f"🚀 Task Started at {ts}\n{content}\n\n", fmt)
            text_edit.setTextCursor(cursor)
            
            # Update Tab Icon/Text
            self._update_tab_status(agent_id, "running")
            
        elif status == "thinking" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#6366f1"))
            fmt.setFontItalic(True)
            fmt.setFontWeight(QFont.Normal)
            fmt.setFontPointSize(11)
            
            cursor.insertText(content, fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            
            self._update_tab_status(agent_id, "thinking")
            
        elif status == "log" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            if not text_edit.toPlainText().endswith("\n") and len(text_edit.toPlainText()) > 0:
                cursor.insertText("\n")
                
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#6b7280"))
            fmt.setFontItalic(False)
            fmt.setFontWeight(QFont.Normal)
            fmt.setFontPointSize(10)
            
            cursor.insertText(f"[{ts}] {content}\n", fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            
        elif status == "tool_use" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            if not text_edit.toPlainText().endswith("\n") and len(text_edit.toPlainText()) > 0:
                cursor.insertText("\n")
                
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#d97706")) # Amber
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontItalic(False)
            fmt.setFontPointSize(11)
            
            cursor.insertText(f"\n[{ts}] 🛠️ Action: {content}\n", fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            
            self._update_tab_status(agent_id, "tool")
            
        elif status == "completed":
             cursor = text_edit.textCursor()
             cursor.movePosition(QTextCursor.End)
             if not text_edit.toPlainText().endswith("\n") and len(text_edit.toPlainText()) > 0:
                cursor.insertText("\n")
                
             fmt = QTextCharFormat()
             fmt.setForeground(QColor("#10b981")) # Green
             fmt.setFontWeight(QFont.Bold)
             fmt.setFontItalic(False)
             fmt.setFontPointSize(12)
             
             cursor.insertText(f"\n✅ Completed at {ts}.\n", fmt)
             text_edit.setTextCursor(cursor)
             text_edit.ensureCursorVisible()
             
             self._update_tab_status(agent_id, "completed")

    def _update_tab_status(self, agent_id, state):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith(agent_id):
                icon = None
                if state == "running":
                    icon = qta.icon('fa5s.play-circle', color='#3b82f6')
                elif state == "thinking":
                    icon = qta.icon('fa5s.brain', color='#8b5cf6')
                elif state == "tool":
                    icon = qta.icon('fa5s.tools', color='#f59e0b')
                elif state == "completed":
                    icon = qta.icon('fa5s.check-circle', color='#10b981')
                
                if icon:
                    self.tabs.setTabIcon(i, icon)
                break

    def _create_agent_tab(self, agent_id):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("""
            border: none;
            padding: 8px;
            background: #ffffff;
            font-family: 'Consolas', monospace;
            font-size: 11px;
            line-height: 1.5;
        """)
        layout.addWidget(text_edit)
        
        index = self.tabs.addTab(tab, agent_id)
        self.agents[agent_id] = {"text_edit": text_edit}
        if self.tabs.count() == 1:
            self.tabs.setCurrentIndex(index)

class SubAgentMonitorWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DeepSeek Cowork - AI 分身监控")
        self.resize(600, 400)
        self.setWindowFlags(self.windowFlags() | Qt.Window) # Ensure it acts like a window
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.monitor = SubAgentMonitor()
        layout.addWidget(self.monitor)

class SessionState:
    def __init__(self, session_id, chat_layout, active_skills_label, session_widget, chat_scroll):
        self.session_id = session_id
        self.messages = []
        self.tool_cards = {}
        self.current_content_buffer = ""
        self.temp_thinking_bubble = None
        self.last_agent_bubble = None
        self.llm_worker = None
        self.code_worker = None
        self.chat_layout = chat_layout
        self.active_skills_label = active_skills_label
        self.session_widget = session_widget
        self.chat_scroll = chat_scroll
        self.empty_state = None
        self.displayed_count = 0
        self.load_more_btn = None

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepSeek Cowork")
        
        # Set Window Icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images', 'logo.png')
        if not os.path.exists(icon_path):
             # Try _internal/images for one-dir mode
             icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_internal', 'images', 'logo.png')
        
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)
            # Ensure taskbar icon is set for Windows
            if platform.system() == 'Windows':
                import ctypes
                myappid = f'deepseek.cowork.v3.2.{uuid.getnode()}' # arbitrary string
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

            
        self.resize(1100, 800)
        self.setAcceptDrops(True)
        self.workspace_dir = None
        
        # Apply Clean Light Theme manually for optimized components
        self.setStyleSheet("""
            QMainWindow { background-color: #ffffff; }
            QLabel[roleTitle="true"] { font-size: 18px; font-weight: 600; color: #111827; }
            QLabel[roleSubtitle="true"] { font-size: 13px; color: #6b7280; }
            QTextEdit#MainInput {
                padding: 12px 16px;
                border-radius: 24px;
                border: 1px solid #e2e8f0;
                background: #ffffff;
                font-size: 14px;
                color: #1e293b;
            }
            QTextEdit#MainInput:focus {
                border: 1px solid #3b82f6;
                background: #ffffff;
            }
            QScrollArea { border: none; background: transparent; }
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: transparent;
                padding: 8px 16px;
                margin-right: 4px;
                border-radius: 6px;
                color: #6b7280;
            }
            QTabBar::tab:selected {
                background: #eff6ff;
                color: #2563eb;
                font-weight: bold;
            }

            /* Global Scrollbar Beautification */
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #e5e7eb;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #d1d5db;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar:horizontal {
                border: none;
                background: transparent;
                height: 6px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #e5e7eb;
                min-width: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #d1d5db;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
        """)
        
        self.sessions = {}
        self.current_session_id = None
        self.messages = []
        self.tool_cards = {}
        self.current_content_buffer = ""
        self.temp_thinking_bubble = None
        self.last_agent_bubble = None
        self.llm_worker = None
        self.code_worker = None
        self.active_run_session_id = None
        self.active_code_session_id = None
        self.chat_layout = None
        self.active_skills_label = None
        self.current_selected_tool_id = None
        
        self.config_manager = ConfigManager()
        self.skill_manager = SkillManager(None, self.config_manager)
        self.skill_generator = SkillGenerator(self.config_manager)

        # Connect to Interaction Bridge
        bridge.request_confirmation_signal.connect(self.handle_confirmation_request)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Sidebar ---
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(260)
        # sidebar.setStyleSheet("background-color: #f9fafb; border-right: 1px solid #e5e7eb;")
        sidebar.setStyleSheet(f"background-color: #ffffff; border-right: 1px solid {DesignTokens.border};")
        
        # Add shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 15))
        shadow.setOffset(4, 0)
        sidebar.setGraphicsEffect(shadow)
        sidebar.raise_()

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 24, 16, 24)
        sidebar_layout.setSpacing(16)

        app_title = QLabel("DeepSeek Cowork")
        app_title.setProperty("roleTitle", True)
        sidebar_layout.addWidget(app_title)
        
        app_subtitle = QLabel("智能文件助手")
        app_subtitle.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(app_subtitle)

        new_chat_btn = QPushButton(" 新建对话")
        new_chat_btn.setIcon(qta.icon('fa5s.plus', color='#ffffff'))
        new_chat_btn.setCursor(Qt.PointingHandCursor)
        new_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6; 
                color: white; 
                border-radius: 8px; 
                padding: 10px 16px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover { background-color: #2563eb; }
        """)
        new_chat_btn.clicked.connect(self.new_conversation)
        sidebar_layout.addWidget(new_chat_btn)

        # History List
        history_label = QLabel("历史会话")
        history_label.setStyleSheet("color: #6b7280; font-size: 12px; font-weight: 600; margin-top: 12px;")
        sidebar_layout.addWidget(history_label)

        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        self.history_scroll.setWidget(self.history_container)
        sidebar_layout.addWidget(self.history_scroll, 1)

        sidebar_footer_label = QLabel("设置")
        sidebar_footer_label.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(sidebar_footer_label)
        
        sidebar_btn_style = """
            QPushButton { text-align: left; padding: 8px; border: none; color: #4b5563; background: transparent; border-radius: 6px; }
            QPushButton:hover { background-color: #e5e7eb; color: #111827; }
        """
        
        sidebar_settings_btn = QPushButton(" 系统设置")
        sidebar_settings_btn.setIcon(qta.icon('fa5s.cog', color='#4b5563'))
        sidebar_settings_btn.setCursor(Qt.PointingHandCursor)
        sidebar_settings_btn.setStyleSheet(sidebar_btn_style)
        sidebar_settings_btn.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(sidebar_settings_btn)
        
        sidebar_skills_btn = QPushButton(" 功能中心")
        sidebar_skills_btn.setIcon(qta.icon('fa5s.puzzle-piece', color='#4b5563'))
        sidebar_skills_btn.setCursor(Qt.PointingHandCursor)
        sidebar_skills_btn.setStyleSheet(sidebar_btn_style)
        sidebar_skills_btn.clicked.connect(self.open_skills_center)
        sidebar_layout.addWidget(sidebar_skills_btn)

        root_layout.addWidget(sidebar)

        # --- Main Content ---
        main_container = QWidget()
        main_container.setObjectName("MainContainer")
        root_layout.addWidget(main_container, 1)

        # Right Sidebar (Workspace File Tree)
        self.right_sidebar = QWidget()
        self.right_sidebar.setFixedWidth(280)
        self.right_sidebar.setStyleSheet("background-color: #ffffff; border-left: 1px solid #e5e7eb;")
        self.right_sidebar.setVisible(False)
        
        right_layout = QVBoxLayout(self.right_sidebar)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        
        # Right Sidebar Tabs (Workspace / Tool Details)
        self.right_tabs = QTabWidget()
        self.right_tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: transparent;
                padding: 8px 12px;
                margin-right: 2px;
                border-bottom: 2px solid transparent;
                color: #6b7280;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                color: #2563eb;
                border-bottom: 2px solid #2563eb;
            }
            QTabBar::tab:hover {
                background: #f3f4f6;
            }
        """)
        
        # Tab 1: Workspace Files
        self.workspace_tab = QWidget()
        ws_tab_layout = QVBoxLayout(self.workspace_tab)
        ws_tab_layout.setContentsMargins(0, 0, 0, 0)
        ws_tab_layout.setSpacing(0)
        
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("") 
        
        self.file_tree = QTreeView()
        self.file_tree.setModel(self.file_model)
        self.file_tree.setRootIndex(self.file_model.index(""))
        self.file_tree.setHeaderHidden(True)
        for i in range(1, 4): self.file_tree.setColumnHidden(i, True)
        self.file_tree.setStyleSheet("""
             QTreeView { border: none; } 
             QTreeView::item { padding: 4px; }
             QTreeView::item:selected { background-color: #eff6ff; color: #1d4ed8; }
        """)
        self.file_tree.clicked.connect(self.on_file_clicked)
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self.show_file_context_menu)
        ws_tab_layout.addWidget(self.file_tree, 2)
        
        # Preview Area in Workspace Tab
        r_preview_header = QLabel("  内容预览")
        r_preview_header.setStyleSheet("font-weight: 600; color: #4b5563; padding: 8px 12px; border-top: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb; background: #f9fafb;")
        ws_tab_layout.addWidget(r_preview_header)
        
        self.preview_stack = QStackedWidget()
        self.preview_text = ReadOnlyTextEdit()
        # self.preview_text.setReadOnly(True) # Handled by class
        self.preview_text.setStyleSheet("border: none; padding: 8px; color: #374151; font-family: 'Consolas', monospace; font-size: 11px;")
        self.preview_text.setPlaceholderText("点击文件预览内容...")
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_stack.addWidget(self.preview_text)
        self.preview_stack.addWidget(self.preview_image)
        self.preview_stack.setCurrentWidget(self.preview_text)
        self.preview_pixmap = None
        ws_tab_layout.addWidget(self.preview_stack, 1)
        
        self.right_tabs.addTab(self.workspace_tab, "工作区文件")
        
        # Tab 2: Tool Details
        self.tool_details_tab = QWidget()
        td_layout = QVBoxLayout(self.tool_details_tab)
        td_layout.setContentsMargins(12, 12, 12, 12)
        td_layout.setSpacing(12)
        
        td_header = QLabel("工具调用详情")
        td_header.setStyleSheet("font-size: 14px; font-weight: bold; color: #111827;")
        td_layout.addWidget(td_header)
        
        # Tool ID / Name
        self.td_info_label = QLabel("选择左侧工具卡片查看详情")
        self.td_info_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        td_layout.addWidget(self.td_info_label)
        
        # Args
        td_args_label = QLabel("Arguments:")
        td_args_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; margin-top: 8px;")
        td_layout.addWidget(td_args_label)
        
        self.td_args_edit = ReadOnlyTextEdit()
        # self.td_args_edit.setReadOnly(True)
        self.td_args_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background: #f9fafb;
                color: #374151;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        td_layout.addWidget(self.td_args_edit, 1)
        
        # Result
        td_result_label = QLabel("Result:")
        td_result_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; margin-top: 8px;")
        td_layout.addWidget(td_result_label)
        
        self.td_result_edit = ReadOnlyTextEdit()
        # self.td_result_edit.setReadOnly(True)
        self.td_result_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background: #f9fafb;
                color: #374151;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        td_layout.addWidget(self.td_result_edit, 2)
        
        self.right_tabs.addTab(self.tool_details_tab, "工具详情")
        
        # Sub-Agent Monitor (Now as independent window, initialized later)
        self.sub_agent_monitor_window = None
        
        right_layout.addWidget(self.right_tabs)
        
        root_layout.addWidget(self.right_sidebar)

        # Main Layout Construction
        layout = QVBoxLayout(main_container)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(20)

        # Top Bar
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        title_label = QLabel("你好，需要我为你做些什么？")
        title_label.setProperty("roleTitle", True)
        subtitle_label = QLabel("选择工作区，描述你的需求，我会帮你完成文件操作。")
        subtitle_label.setProperty("roleSubtitle", True)
        title_box.addWidget(title_label)
        title_box.addWidget(subtitle_label)
        top_bar.addLayout(title_box)
        top_bar.addStretch()
        
        # Workspace Selector
        ws_container = QFrame()
        ws_container.setStyleSheet("background: #f3f4f6; border-radius: 8px; padding: 4px;")
        ws_layout = QHBoxLayout(ws_container)
        ws_layout.setContentsMargins(8, 4, 8, 4)
        
        self.ws_label = QLabel("当前文件夹: 未选择")
        self.ws_label.setStyleSheet("color: #6b7280; font-weight: 500;")
        
        self.recent_btn = QPushButton()
        self.recent_btn.setIcon(qta.icon('fa5s.history', color='#6b7280'))
        self.recent_btn.setToolTip("最近使用的文件夹")
        self.recent_btn.setFixedWidth(32)
        self.recent_btn.setCursor(Qt.PointingHandCursor)
        self.recent_btn.setStyleSheet("border: none; background: transparent;")
        self.recent_btn.clicked.connect(self.show_recent_menu)
        
        self.ws_btn = QPushButton(" 切换")
        self.ws_btn.setIcon(qta.icon('fa5s.folder-open', color='#374151'))
        self.ws_btn.setCursor(Qt.PointingHandCursor)
        self.ws_btn.setStyleSheet("background: white; border: 1px solid #e5e7eb; border-radius: 6px; padding: 4px 12px; color: #374151;")
        self.ws_btn.clicked.connect(self.select_workspace)
        
        ws_layout.addWidget(self.ws_label)
        ws_layout.addWidget(self.recent_btn)
        ws_layout.addWidget(self.ws_btn)
        top_bar.addWidget(ws_container)
        
        layout.addLayout(top_bar)
        
        self.recent_workspaces = self.config_manager.get("recent_workspaces", [])

        # Chat Area
        self.session_tabs = QTabWidget()
        self.session_tabs.setDocumentMode(True)
        self.session_tabs.setTabsClosable(True)
        self.session_tabs.currentChanged.connect(self.on_session_tab_changed)
        self.session_tabs.tabCloseRequested.connect(self.close_session_tab)
        layout.addWidget(self.session_tabs, 3)

        # Input Area
        input_card = QFrame()
        input_card.setObjectName("ContentCard")
        # Styling handled in global stylesheet
        input_layout = QHBoxLayout(input_card)
        input_layout.setContentsMargins(0, 0, 0, 0)

        self.input_field = AutoResizingInputEdit()
        self.input_field.setObjectName("MainInput")
        self.input_field.setPlaceholderText("例如：把这个文件夹里的图片按日期分类")
        self.input_field.returnPressed.connect(self.handle_send)

        self.pause_btn = QPushButton()
        self.pause_btn.setIcon(qta.icon('fa5s.pause', color='#4b5563'))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setVisible(False)
        self.pause_btn.setStyleSheet("border: none; font-size: 16px;")
        
        self.action_btn = QPushButton("发送")
        self.action_btn.setIcon(qta.icon('fa5s.paper-plane', color='white'))
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setFixedSize(60, 36)
        self.action_btn.setStyleSheet("background-color: #4d6bfe; color: white; border-radius: 18px; font-weight: bold; border: none;")
        self.action_btn.clicked.connect(self.on_action_clicked)
        
        self.loop_hint = QPushButton(" 循环中")
        self.loop_hint.setIcon(qta.icon('fa5s.exclamation-circle', color='#ef4444'))
        self.loop_hint.setFlat(True)
        self.loop_hint.setStyleSheet("color: #ef4444; font-size: 11px; margin-right: 8px; border: none; text-align: left;")
        self.loop_hint.setVisible(False)

        # Input Layout
        input_wrapper = QWidget()
        input_wrapper_layout = QHBoxLayout(input_wrapper)
        input_wrapper_layout.setContentsMargins(0,0,0,0)
        input_wrapper_layout.addWidget(self.input_field)
        
        # Position buttons inside the input field visually (using negative margins or overlapping layout would be complex, 
        # so we place them in a row)
        
        bottom_controls = QHBoxLayout()
        bottom_controls.addWidget(input_wrapper, 1)
        bottom_controls.addWidget(self.pause_btn)
        bottom_controls.addWidget(self.loop_hint)
        bottom_controls.addWidget(self.action_btn)

        layout.addLayout(bottom_controls)

        # Init Data
        self.data_dir = get_app_data_dir()
        self.chat_history_dir = self.config_manager.get_chat_history_dir()
        os.makedirs(self.chat_history_dir, exist_ok=True)
        
        self.create_new_session()
        self.refresh_history_list()
        self.load_default_workspace()

    # --- Session & Logic Methods (No changes to logic, only UI wrappers) ---
    def get_current_session(self):
        if not self.current_session_id: return None
        return self.sessions.get(self.current_session_id)

    def get_session(self, session_id=None):
        if session_id is None: return self.get_current_session()
        return self.sessions.get(session_id)

    def sync_current_session_state(self):
        state = self.get_current_session()
        if not state: return
        state.messages = self.messages
        state.tool_cards = self.tool_cards
        state.current_content_buffer = self.current_content_buffer

    def set_current_session(self, session_id):
        state = self.sessions.get(session_id)
        if not state: return
        self.current_session_id = session_id
        self.messages = state.messages
        self.tool_cards = state.tool_cards
        self.current_content_buffer = state.current_content_buffer
        self.temp_thinking_bubble = state.temp_thinking_bubble
        self.last_agent_bubble = state.last_agent_bubble
        self.llm_worker = state.llm_worker
        self.code_worker = state.code_worker
        self.chat_layout = state.chat_layout
        self.active_skills_label = state.active_skills_label

    def normalize_session_ui(self, state):
        if not state: return
        running = state.llm_worker and state.llm_worker.isRunning()
        paused = running and state.llm_worker.is_paused
        running_code = state.code_worker and state.code_worker.isRunning()
        
        if running or running_code:
            self.action_btn.setText("停止")
            self.action_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.action_btn.setStyleSheet("background-color: #ef4444; color: white; border-radius: 18px; font-weight: bold; border: none;")
            self.action_btn.setEnabled(True)
            self.input_field.setEnabled(False)
            
            # Hide extra buttons/prompts when running
            self.pause_btn.setVisible(False)
            self.loop_hint.setVisible(False)
        else:
            self.action_btn.setText("发送")
            self.action_btn.setIcon(qta.icon('fa5s.paper-plane', color='white'))
            self.action_btn.setStyleSheet("background-color: #4d6bfe; color: white; border-radius: 18px; font-weight: bold; border: none;")
            self.action_btn.setEnabled(True)
            self.input_field.setEnabled(True)
            
            self.pause_btn.setVisible(False)
            self.loop_hint.setVisible(False)

    def get_session_id_for_tab(self, index):
        widget = self.session_tabs.widget(index)
        if not widget: return None
        for session_id, state in self.sessions.items():
            if state.session_widget == widget: return session_id
        return None

    def close_session_tab(self, index):
        session_id = self.get_session_id_for_tab(index)
        if not session_id: return
        state = self.sessions.get(session_id)
        if state:
            if state.llm_worker: state.llm_worker.stop()
            if state.code_worker: state.code_worker.stop()
            del self.sessions[session_id]
        self.session_tabs.removeTab(index)
        if self.session_tabs.count() == 0: self.create_new_session()

    def on_session_tab_changed(self, index):
        self.sync_current_session_state()
        session_id = self.get_session_id_for_tab(index)
        if session_id:
            self.set_current_session(session_id)
            self.refresh_history_list()
            self.normalize_session_ui(self.get_current_session())

    def clear_chat_layout(self, chat_layout):
        while chat_layout.count():
            item = chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None: widget.deleteLater()
        chat_layout.addStretch()

    def update_session_tab_title(self, session_id):
        state = self.sessions.get(session_id)
        if not state: return
        title = "新对话"
        for msg in state.messages:
            if msg.get("role") == "user":
                content = msg.get("content") or ""
                if content: title = content[:15] + "..." if len(content) > 15 else content
                break
        index = self.session_tabs.indexOf(state.session_widget)
        if index >= 0: self.session_tabs.setTabText(index, title)

    def create_new_session(self, session_id=None, title=None):
        if session_id is None: session_id = uuid.uuid4().hex
        session_widget = QWidget()
        session_layout = QVBoxLayout(session_widget)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(12)

        active_skills_label = QLabel("本次会话使用的功能: ")
        active_skills_label.setStyleSheet("color: #9ca3af; font-size: 11px; margin-left: 12px;")
        session_layout.addWidget(active_skills_label)

        chat_scroll = QScrollArea()
        chat_scroll.setWidgetResizable(True)
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(12, 12, 12, 24) # Bottom padding
        chat_layout.setSpacing(24) # Space between messages
        
        # Add Empty State
        empty_state = EmptyStateWidget(self)
        chat_layout.addWidget(empty_state)
        
        chat_layout.addStretch()
        chat_scroll.setWidget(chat_container)
        session_layout.addWidget(chat_scroll, 1)

        tab_title = title or "新对话"
        tab_index = self.session_tabs.addTab(session_widget, tab_title)

        state = SessionState(session_id, chat_layout, active_skills_label, session_widget, chat_scroll)
        state.empty_state = empty_state
        self.sessions[session_id] = state
        self.session_tabs.setCurrentIndex(tab_index)
        self.set_current_session(session_id)
        return session_id

    def handle_confirmation_request(self, message):
        dialog = QDialog(self)
        dialog.setWindowTitle("请再次确认")
        dialog.resize(500, 400)
        layout = QVBoxLayout(dialog)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet("font-size: 14px; line-height: 1.4;")
        content_layout.addWidget(label)
        content_layout.addStretch()
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)

        hint_label = QLabel("如果不确定，可以先在下方输入问题问问 AI：")
        layout.addWidget(hint_label)
        ai_input = QLineEdit()
        ai_input.setPlaceholderText("例如：这一步会删除原文件吗？")
        layout.addWidget(ai_input)
        button_layout = QHBoxLayout()
        ask_btn = QPushButton("发送给AI")
        yes_btn = QPushButton("是的，继续")
        no_btn = QPushButton("先不要")
        button_layout.addWidget(ask_btn)
        button_layout.addStretch()
        button_layout.addWidget(yes_btn)
        button_layout.addWidget(no_btn)
        layout.addLayout(button_layout)
        decision = {"value": False}

        def send_to_ai():
            text = ai_input.text().strip()
            if not text: return
            decision["value"] = text
            dialog.accept()

        def on_yes():
            decision["value"] = True
            dialog.accept()

        def on_no():
            decision["value"] = False
            dialog.reject()

        ask_btn.clicked.connect(send_to_ai)
        yes_btn.clicked.connect(on_yes)
        no_btn.clicked.connect(on_no)
        dialog.exec()
        bridge.respond(decision["value"])

    def refresh_history_list(self):
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        
        history_dir = self.chat_history_dir
        if not os.path.exists(history_dir):
            self.history_layout.addStretch()
            return

        files = glob.glob(os.path.join(history_dir, 'chat_history_*.json'))
        files.sort(key=os.path.getmtime, reverse=True)

        for file_path in files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not data: continue
                    title = "新对话"
                    for msg in data:
                        if msg['role'] == 'user':
                            content = msg['content']
                            title = content[:15] + "..." if len(content) > 15 else content
                            break
                    filename = os.path.basename(file_path)
                    session_id = filename.replace('chat_history_', '').replace('.json', '')
                    
                    btn = QPushButton(title)
                    btn.setCursor(Qt.PointingHandCursor)
                    if session_id == self.current_session_id:
                         btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: #eff6ff; color: #1d4ed8; font-weight: 600;")
                    else:
                         btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: transparent; color: #4b5563;")
                    
                    btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))
                    self.history_layout.addWidget(btn)
            except Exception as e:
                continue
        self.history_layout.addStretch()

    def create_load_more_btn(self):
        btn = QPushButton("显示更多历史消息")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                border: none;
                background: transparent;
                color: {DesignTokens.text_secondary};
                font-size: 12px;
                padding: 8px;
                margin-bottom: 8px;
            }}
            QPushButton:hover {{
                color: {DesignTokens.primary};
            }}
        """)
        btn.clicked.connect(self.load_more_history)
        return btn

    def load_more_history(self):
        state = self.get_current_session()
        if not state: return
        
        PAGE_SIZE = 20
        total = len(state.messages)
        remaining = total - state.displayed_count
        if remaining <= 0: return
        
        count_to_load = min(PAGE_SIZE, remaining)
        start_idx = total - state.displayed_count - count_to_load
        end_idx = total - state.displayed_count
        
        msgs_to_load = state.messages[start_idx:end_idx]
        
        # Save scroll position
        vbar = state.chat_scroll.verticalScrollBar()
        old_max = vbar.maximum()
        old_val = vbar.value()
        
        # Insert after the button (index 1)
        # Use animate=False to prevent scroll jumping and ensure instant layout update
        self.render_message_batch(msgs_to_load, state.session_id, insert_index=1, animate=False)
        
        # Restore scroll position (adjust for new content height)
        QApplication.processEvents() # Ensure layout updates
        new_max = vbar.maximum()
        vbar.setValue(old_val + (new_max - old_max))
        
        state.displayed_count += count_to_load
        
        if state.displayed_count >= total:
            if state.load_more_btn:
                state.load_more_btn.deleteLater()
                state.load_more_btn = None

    def render_message_batch(self, messages, session_id, insert_index=None, animate=True):
        state = self.get_session(session_id)
        if not state: return
        
        current_idx = insert_index
        backup_last_agent = state.last_agent_bubble
        state.last_agent_bubble = None 
        
        for msg in messages:
            role = msg.get('role')
            content = msg.get('content')
            reasoning = msg.get('reasoning')
            
            if role == 'user':
                self.add_chat_bubble('User', content, index=current_idx, animate=animate)
                if current_idx is not None: current_idx += 1
                state.last_agent_bubble = None
                
            elif role == 'assistant':
                bubble = None
                if content or reasoning or msg.get('tool_calls'):
                    bubble = self.add_chat_bubble('Agent', content, thinking=reasoning, index=current_idx, animate=animate)
                    if current_idx is not None: current_idx += 1
                
                state.last_agent_bubble = bubble
                
                tool_calls = msg.get('tool_calls')
                if tool_calls:
                    for tc in tool_calls:
                        t_id = tc.get('id')
                        func = tc.get('function', {})
                        t_name = func.get('name')
                        t_args = func.get('arguments')
                        self.add_tool_card({
                            'id': t_id,
                            'name': t_name,
                            'args': t_args
                        }, session_id=session_id, index=current_idx, animate=animate)
                        
                        if not bubble:
                             if current_idx is not None: current_idx += 1

            elif role == 'tool':
                t_id = msg.get('tool_call_id')
                t_result = content
                if t_id:
                    self.update_tool_card({
                        'id': t_id,
                        'result': t_result
                    }, session_id=session_id)
                    
        if insert_index is not None:
             state.last_agent_bubble = backup_last_agent

    def load_session(self, session_id):
        if session_id in self.sessions:
            state = self.sessions[session_id]
            index = self.session_tabs.indexOf(state.session_widget)
            if index >= 0: self.session_tabs.setCurrentIndex(index)
            self.set_current_session(session_id)
            self.refresh_history_list()
            self.normalize_session_ui(self.get_current_session())
            return
        else:
            self.create_new_session(session_id=session_id)

        state = self.get_current_session()
        if not state: return

        self.clear_chat_layout(state.chat_layout)
        state.empty_state = None # Reset empty state reference
        
        state.messages = []
        state.tool_cards = {}
        state.current_content_buffer = ""
        state.temp_thinking_bubble = None
        state.last_agent_bubble = None
        state.llm_worker = None
        state.active_skills_label.setText("本次会话使用的功能: ")
        state.displayed_count = 0
        state.load_more_btn = None

        history_path = os.path.join(self.chat_history_dir, f'chat_history_{session_id}.json')
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    state.messages = json.load(f)
                
                # Pagination: Load last 20 messages
                PAGE_SIZE = 20
                total = len(state.messages)
                start_idx = max(0, total - PAGE_SIZE)
                
                display_msgs = state.messages[start_idx:]
                state.displayed_count = len(display_msgs)
                
                # Add Load More button if needed
                if start_idx > 0:
                    btn = self.create_load_more_btn()
                    state.load_more_btn = btn
                    state.chat_layout.addWidget(btn) # Add to top (since layout is empty)
                
                self.render_message_batch(display_msgs, session_id, animate=False)
                
            except Exception as e:
                print(f"Error loading session: {e}")
        
        # Restore Empty State if no messages
        if len(state.messages) == 0:
            empty_state = EmptyStateWidget(self)
            state.chat_layout.insertWidget(0, empty_state)
            state.empty_state = empty_state

        self.update_session_tab_title(session_id)
        self.refresh_history_list()
        self.normalize_session_ui(self.get_current_session())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
            
        path = urls[0].toLocalFile()
        if os.path.isdir(path):
            # Switch workspace
            self.load_workspace(path)
        elif os.path.isfile(path):
            # Add file path to input
            if hasattr(self, 'input_field'):
                current_text = self.input_field.toPlainText()
                new_text = f"{current_text}\n{path}" if current_text else path
                self.input_field.setText(new_text)

    def new_conversation(self):
        self.create_new_session()
        self.refresh_history_list()

    def save_chat_history(self):
        state = self.get_current_session()
        if not state or not state.messages: return
        history_path = os.path.join(self.chat_history_dir, f'chat_history_{state.session_id}.json')
        try:
            with open(history_path, 'w', encoding='utf-8') as f:
                json.dump(state.messages, f, ensure_ascii=False, indent=2)
        except Exception: pass

    def load_default_workspace(self):
        default_dir = self.config_manager.get("default_workspace", "")
        if default_dir and os.path.isdir(default_dir):
            self.load_workspace(default_dir)

    def select_workspace(self):
        directory = QFileDialog.getExistingDirectory(self, "选择工作区")
        if directory: self.load_workspace(directory)

    def load_workspace(self, directory):
        self.workspace_dir = directory
        font_metrics = QFontMetrics(self.ws_label.font())
        display_path = font_metrics.elidedText(directory, Qt.ElideMiddle, 400)
        self.ws_label.setText(f"当前工作区: {display_path}")
        self.ws_label.setToolTip(directory)
        self.ws_label.setStyleSheet("color: #059669; font-weight: 600;")
        self.update_recent_workspaces(directory)
        
        if hasattr(self, 'file_model'):
            self.file_model.setRootPath(directory)
            self.file_tree.setRootIndex(self.file_model.index(directory))
            self.right_sidebar.setVisible(True)

    def update_recent_workspaces(self, path):
        if path in self.recent_workspaces: self.recent_workspaces.remove(path)
        self.recent_workspaces.insert(0, path)
        self.recent_workspaces = self.recent_workspaces[:10]
        self.config_manager.set("recent_workspaces", self.recent_workspaces)

    def show_recent_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        if not self.recent_workspaces:
            no_action = QAction("无最近记录", self)
            no_action.setEnabled(False)
            menu.addAction(no_action)
        else:
            for path in self.recent_workspaces:
                action = QAction(path, self)
                action.triggered.connect(lambda checked=False, p=path: self.load_workspace(p))
                menu.addAction(action)
            menu.addSeparator()
            clear_action = QAction("清除记录", self)
            clear_action.triggered.connect(self.clear_recent_workspaces)
            menu.addAction(clear_action)
        menu.exec(self.recent_btn.mapToGlobal(self.recent_btn.rect().bottomLeft()))

    def clear_recent_workspaces(self):
        self.recent_workspaces = []
        self.config_manager.set("recent_workspaces", [])

    def show_file_context_menu(self, position):
        index = self.file_tree.indexAt(position)
        if not index.isValid(): return
        path = self.file_model.filePath(index)
        if not os.path.exists(path): return
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        
        open_action = QAction("打开", self)
        open_action.setIcon(qta.icon('fa5s.external-link-alt', color='#4b5563'))
        
        reveal_action = QAction("在资源管理器中显示", self)
        reveal_action.setIcon(qta.icon('fa5s.folder-open', color='#4b5563'))
        
        copy_path_action = QAction("复制路径", self)
        copy_path_action.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        
        delete_action = QAction("删除", self)
        delete_action.setIcon(qta.icon('fa5s.trash-alt', color='#ef4444'))

        open_action.triggered.connect(lambda: self.open_path_in_system(path))
        reveal_action.triggered.connect(lambda: self.reveal_in_explorer(path))
        copy_path_action.triggered.connect(lambda: self.copy_path_to_clipboard(path))
        delete_action.triggered.connect(lambda: self.delete_path(path))

        menu.addAction(open_action)
        menu.addAction(reveal_action)
        menu.addSeparator()
        menu.addAction(copy_path_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.file_tree.viewport().mapToGlobal(position))

    def open_path_in_system(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def reveal_in_explorer(self, path):
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def copy_path_to_clipboard(self, path):
        QApplication.clipboard().setText(path)

    def delete_path(self, path):
        confirm = QMessageBox.question(self, "确认删除", f"确定要删除该项目吗？\n{path}")
        if confirm != QMessageBox.Yes: return
        try:
            if os.path.isdir(path): shutil.rmtree(path)
            else: os.remove(path)
        except Exception: pass

    def on_file_clicked(self, index):
        path = self.file_model.filePath(index)
        if not os.path.isfile(path): return
        ext = os.path.splitext(path)[1].lower()
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        try:
            size = os.path.getsize(path)
            if ext in image_exts:
                if size > 10 * 1024 * 1024:
                    self.preview_text.setPlainText("文件过大")
                    self.preview_stack.setCurrentWidget(self.preview_text)
                    return
                pixmap = QPixmap(path)
                self.preview_pixmap = pixmap
                scaled = pixmap.scaled(self.preview_stack.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_image.setPixmap(scaled)
                self.preview_stack.setCurrentWidget(self.preview_image)
                return
            if size > 1024 * 1024:
                self.preview_text.setPlainText("文件过大")
                self.preview_stack.setCurrentWidget(self.preview_text)
                return
            try:
                with open(path, 'r', encoding='utf-8') as f: content = f.read()
                self.preview_text.setPlainText(content)
                self.preview_stack.setCurrentWidget(self.preview_text)
            except UnicodeDecodeError:
                self.preview_text.setPlainText("二进制文件")
                self.preview_stack.setCurrentWidget(self.preview_text)
        except Exception: pass

    def open_settings(self):
        SettingsDialog(self.config_manager, self).exec()

    def open_skills_center(self):
        SkillsCenterDialog(self.skill_manager, self.config_manager, self).exec()

    def handle_skill_used(self, skill_name, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        current_text = state.active_skills_label.text()
        if f"[{skill_name}]" not in current_text:
            state.active_skills_label.setText(current_text + f" [{skill_name}]")

    def toggle_pause(self):
        state = self.get_current_session()
        if state and state.llm_worker and state.llm_worker.isRunning():
            if state.llm_worker.is_paused:
                state.llm_worker.resume()
                self.pause_btn.setText("")
                self.pause_btn.setIcon(qta.icon('fa5s.pause', color='#4b5563'))
                self.pause_btn.setToolTip("暂停")
            else:
                state.llm_worker.pause()
                self.pause_btn.setText("")
                self.pause_btn.setIcon(qta.icon('fa5s.play', color='#10b981'))
                self.pause_btn.setToolTip("继续")

    def stop_agent(self):
        state = self.get_current_session()
        if not state: return
        if state.llm_worker and state.llm_worker.isRunning(): state.llm_worker.stop()
        if state.code_worker and state.code_worker.isRunning(): state.code_worker.stop()
        state.code_worker = None
        self.code_worker = None
        self.add_system_toast("已强制停止当前任务", "warning", session_id=state.session_id)
        self.normalize_session_ui(state)

    def on_action_clicked(self):
        state = self.get_current_session()
        if not state: return
        
        # Check if running
        is_running = (state.llm_worker and state.llm_worker.isRunning()) or \
                     (state.code_worker and state.code_worker.isRunning())
        
        if is_running:
            self.stop_agent()
        else:
            self.handle_send()

    def handle_send(self):
        if not self.workspace_dir:
            QMessageBox.warning(self, "提示", "请先选择一个工作区目录！")
            return
        user_text = self.input_field.toPlainText().strip()
        if not user_text: return

        self.add_chat_bubble("User", user_text)
        self.input_field.clear()
        
        state = self.get_current_session()
        if not state: return
        state.messages.append({"role": "user", "content": user_text})
        self.save_chat_history()
        self.update_session_tab_title(state.session_id)
        self.process_agent_logic(user_text)

    def show_tool_details(self, tool_id, args, result, switch_tab=True):
        # 1. Update selection state in UI
        state = self.get_current_session()
        if state:
            for tid, card in state.tool_cards.items():
                card.set_selected(tid == tool_id)
        
        self.current_selected_tool_id = tool_id

        # 2. Open Sidebar & Switch Tab
        if not self.right_sidebar.isVisible():
            self.right_sidebar.setVisible(True)
            
        if switch_tab:
            self.right_tabs.setCurrentIndex(1) # Switch to Tool Details tab
        
        # 3. Update Content
        self.td_info_label.setText(f"工具 ID: {tool_id}")
        
        # Format JSON if possible
        try:
            if isinstance(args, str):
                args_obj = json.loads(args)
                args_text = json.dumps(args_obj, indent=2, ensure_ascii=False)
            else:
                args_text = json.dumps(args, indent=2, ensure_ascii=False)
        except:
            args_text = str(args)
            
        self.td_args_edit.setPlainText(args_text)
        
        try:
            if isinstance(result, str):
                # Try to parse result if it looks like JSON
                if result.strip().startswith("{") or result.strip().startswith("["):
                    res_obj = json.loads(result)
                    res_text = json.dumps(res_obj, indent=2, ensure_ascii=False)
                else:
                    res_text = result
            else:
                res_text = json.dumps(result, indent=2, ensure_ascii=False)
        except:
            res_text = str(result)
            
        self.td_result_edit.setPlainText(res_text)

    def add_tool_card(self, data, session_id=None, index=None, animate=True):
        card = ToolCallCard(data['name'], data['args'], data['id'])
        card.clicked.connect(self.show_tool_details)
        
        state = self.get_session(session_id)
        if not state: return
        state.tool_cards[data['id']] = card
        
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.add_tool_card(card)
        elif state.last_agent_bubble:
             state.last_agent_bubble.add_tool_card(card)
        else:
            wrapper = QWidget()
            layout = QHBoxLayout(wrapper)
            layout.setContentsMargins(48, 4, 16, 4)
            layout.addWidget(card)
            layout.addStretch()
            
            # Animation: Fade + Slide
            opacity_effect = QGraphicsOpacityEffect(wrapper)
            wrapper.setGraphicsEffect(opacity_effect)
            
            if index is not None:
                state.chat_layout.insertWidget(index, wrapper)
            else:
                state.chat_layout.insertWidget(state.chat_layout.count() - 1, wrapper)
            
            QApplication.processEvents()
            
            if animate:
                opacity_effect.setOpacity(0)
                fade_anim = QPropertyAnimation(opacity_effect, b"opacity", wrapper)
                fade_anim.setDuration(350)
                fade_anim.setStartValue(0.0)
                fade_anim.setEndValue(1.0)
                fade_anim.setEasingCurve(QEasingCurve.OutCubic)
                
                slide_anim = QPropertyAnimation(wrapper, b"pos", wrapper)
                slide_anim.setDuration(350)
                slide_anim.setStartValue(wrapper.pos() + QPoint(0, 20))
                slide_anim.setEndValue(wrapper.pos())
                slide_anim.setEasingCurve(QEasingCurve.OutBack)
                
                group = QParallelAnimationGroup(wrapper)
                group.addAnimation(fade_anim)
                group.addAnimation(slide_anim)
                group.start(QAbstractAnimation.DeleteWhenStopped)
            else:
                opacity_effect.setOpacity(1.0)
            
        QApplication.processEvents()

    def update_tool_card(self, data, session_id=None):
        tool_id = data['id']
        result = data['result']
        state = self.get_session(session_id)
        if not state: return
        if tool_id in state.tool_cards:
            card = state.tool_cards[tool_id]
            card.set_result(result)
            
            # [Optimization] Real-time refresh if currently viewing this tool
            if (hasattr(self, 'current_selected_tool_id') and 
                self.current_selected_tool_id == tool_id and 
                self.right_sidebar.isVisible() and 
                self.right_tabs.currentIndex() == 1):
                
                self.show_tool_details(tool_id, card.args, result, switch_tab=False)

    def add_chat_bubble(self, role, text, thinking=None, duration=None, index=None, animate=True):
        state = self.get_current_session()
        if not state: return
        
        # Hide Empty State if this is the first message
        if state.empty_state and state.empty_state.isVisible():
            state.empty_state.setVisible(False)
            
        bubble = ChatBubble(role, text, thinking, duration)
        
        # Animation: Fade + Slide
        opacity_effect = QGraphicsOpacityEffect(bubble)
        bubble.setGraphicsEffect(opacity_effect)
        
        if index is not None:
            state.chat_layout.insertWidget(index, bubble)
        else:
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, bubble)
        
        QApplication.processEvents() 
        
        if animate:
            opacity_effect.setOpacity(0)
            fade_anim = QPropertyAnimation(opacity_effect, b"opacity", bubble)
            fade_anim.setDuration(350)
            fade_anim.setStartValue(0.0)
            fade_anim.setEndValue(1.0)
            fade_anim.setEasingCurve(QEasingCurve.OutCubic)
            
            slide_anim = QPropertyAnimation(bubble, b"pos", bubble)
            slide_anim.setDuration(350)
            slide_anim.setStartValue(bubble.pos() + QPoint(0, 20))
            slide_anim.setEndValue(bubble.pos())
            slide_anim.setEasingCurve(QEasingCurve.OutBack)
            
            group = QParallelAnimationGroup(bubble)
            group.addAnimation(fade_anim)
            group.addAnimation(slide_anim)
            group.start(QAbstractAnimation.DeleteWhenStopped)
        else:
            opacity_effect.setOpacity(1.0)
        
        # Scroll to bottom only if appending
        if index is None and hasattr(state, 'chat_scroll') and state.chat_scroll:
            state.chat_scroll.verticalScrollBar().setValue(
                state.chat_scroll.verticalScrollBar().maximum()
            )
            
        return bubble

    def add_system_toast(self, text, type="info", session_id=None, auto_close_ms=None):
        state = self.get_session(session_id)
        if not state: return
        toast = SystemToast(text, type)
        state.chat_layout.insertWidget(state.chat_layout.count() - 1, toast)
        QApplication.processEvents()
        if auto_close_ms: QTimer.singleShot(auto_close_ms, toast.deleteLater)

    def append_log(self, text):
        print(f"[Log] {text}")

    def process_agent_logic(self, user_text):
        state = self.get_current_session()
        if not state: return
        state.current_content_buffer = ""
        
        # Insert "Thinking" bubble
        state.temp_thinking_bubble = ChatBubble("agent", "", thinking="...")
        state.chat_layout.insertWidget(state.chat_layout.count()-1, state.temp_thinking_bubble)
        QApplication.processEvents()

        state.llm_worker = LLMWorker(state.messages, self.config_manager, self.workspace_dir)
        if state.session_id == self.current_session_id:
            self.llm_worker = state.llm_worker
        session_id = state.session_id
        state.llm_worker.finished_signal.connect(lambda result, sid=session_id: self.handle_llm_response(result, sid))
        state.llm_worker.content_signal.connect(lambda text, sid=session_id: self.handle_content_signal(text, sid))
        state.llm_worker.step_signal.connect(self.append_log)
        state.llm_worker.thinking_signal.connect(lambda text, sid=session_id: self.handle_thinking_signal(text, sid))
        state.llm_worker.skill_used_signal.connect(lambda name, sid=session_id: self.handle_skill_used(name, sid))
        state.llm_worker.tool_call_signal.connect(lambda data, sid=session_id: self.add_tool_card(data, sid))
        state.llm_worker.tool_result_signal.connect(lambda data, sid=session_id: self.update_tool_card(data, sid))
        state.llm_worker.agent_state_signal.connect(lambda data, sid=session_id: self.handle_agent_state(data, sid))
        state.llm_worker.start()
        
        if state.session_id == self.current_session_id:
             self.normalize_session_ui(state)

    def handle_agent_state(self, data, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        
        # Update Tool Card
        tool_call_id = data.get("tool_call_id")
        if tool_call_id and tool_call_id in state.tool_cards:
            card = state.tool_cards[tool_call_id]
            card.update_agent_state(data)

        # Update Sub-Agent Monitor
        if session_id == self.current_session_id:
            agent_id = data.get("agent_id")
            status = data.get("status")
            
            if agent_id:
                content = None
                if status == "thinking":
                    content = data.get("reasoning_delta")
                elif status == "log":
                    content = data.get("log_content")
                elif status == "tool_use":
                    content = data.get("task")
                elif status == "pending":
                    content = f"Task: {data.get('task')}"
                elif status == "completed":
                    content = "Done"
                    
                if content or status in ["completed", "pending"]:
                    # Lazy init monitor window
                    if not self.sub_agent_monitor_window:
                        self.sub_agent_monitor_window = SubAgentMonitorWindow(self)
                    
                    self.sub_agent_monitor_window.monitor.update_log(agent_id, content, status)

                # Auto show monitor window if active/thinking/pending
                if status in ["active", "thinking", "pending", "tool_use"]:
                     if not self.sub_agent_monitor_window:
                        self.sub_agent_monitor_window = SubAgentMonitorWindow(self)
                     
                     if not self.sub_agent_monitor_window.isVisible():
                         self.sub_agent_monitor_window.show()
                         self.sub_agent_monitor_window.raise_()

    def handle_content_signal(self, text, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        state.current_content_buffer += text
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.set_main_content(state.current_content_buffer)
        elif state.last_agent_bubble:
            state.last_agent_bubble.set_main_content(state.current_content_buffer)
        if state.session_id == self.current_session_id:
            self.current_content_buffer = state.current_content_buffer

    def handle_thinking_signal(self, text, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.update_thinking(text)
        elif state.last_agent_bubble:
            state.last_agent_bubble.update_thinking(text)

    def handle_llm_response(self, result, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        is_current = state.session_id == self.current_session_id
        if state.temp_thinking_bubble:
            bubble = state.temp_thinking_bubble
            state.temp_thinking_bubble = None
        else:
            bubble = ChatBubble("agent", "", thinking=result.get("reasoning"))
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, bubble)
        
        state.last_agent_bubble = bubble
        if is_current:
            self.last_agent_bubble = bubble
            self.temp_thinking_bubble = state.temp_thinking_bubble

        if "error" in result:
            self.append_log(f"Error: {result['error']}")
            self.add_system_toast(f"Error: {result['error']}", "error", session_id=state.session_id)
            bubble.set_main_content(f"⚠️ Error: {result['error']}")
            if is_current: self.normalize_session_ui(state)
            return

        reasoning = result.get("reasoning", "")
        content = result.get("content", "")
        role = result.get("role", "assistant")
        duration = result.get("duration", None)

        bubble.update_thinking(duration=duration, is_final=True)
        bubble.set_main_content(content)

        generated_messages = result.get("generated_messages", [])
        if generated_messages:
            state.messages.extend(generated_messages)
        else:
            state.messages.append({
                "role": role, 
                "content": content,
                "reasoning": reasoning
            })
        self.save_chat_history()
        self.update_session_tab_title(state.session_id)

        code_match = re.search(r'```\s*python(.*?)```', content, re.DOTALL | re.IGNORECASE)
        if code_match:
            code_block = code_match.group(1).strip()
            self.append_log("System: 检测到代码块，准备执行...")
            god_mode = self.config_manager.get_god_mode()
            
            if god_mode:
                 self.add_system_toast("⚠️ God Mode 已启用：正在执行高权限代码，请注意风险", "warning", session_id=state.session_id)

            state.code_worker = CodeWorker(code_block, self.workspace_dir, god_mode=god_mode)
            state.code_worker.output_signal.connect(lambda text, sid=state.session_id: self.handle_code_output(text, sid))
            state.code_worker.finished_signal.connect(lambda sid=state.session_id: self.handle_code_finished(sid))
            state.code_worker.input_request_signal.connect(self.handle_code_input_request)
            
            if is_current:
                self.code_worker = state.code_worker
            
            state.code_worker.start()
            if is_current: self.normalize_session_ui(state)
        else:
            if is_current: self.normalize_session_ui(state)

    def handle_code_output(self, text, session_id=None):
        state = self.get_session(session_id)
        if state and state.last_agent_bubble:
            if not hasattr(state.last_agent_bubble, 'code_output_edit'):
                label = QLabel("执行结果:")
                label.setStyleSheet("font-weight: bold; color: #333; margin-top: 8px; margin-left: 4px;")
                state.last_agent_bubble.layout().addWidget(label)
                
                state.last_agent_bubble.code_output_edit = AutoResizingTextEdit()
                state.last_agent_bubble.code_output_edit.setStyleSheet("color: #444; font-family: Consolas; background: #f8f9fa; border: 1px solid #eee; padding: 8px; border-radius: 4px; margin-left: 4px;")
                state.last_agent_bubble.code_output_edit.setReadOnly(True)
                state.last_agent_bubble.layout().addWidget(state.last_agent_bubble.code_output_edit)
            
            state.last_agent_bubble.code_output_edit.append(text)
            state.last_agent_bubble.code_output_edit.adjustHeight()
            QApplication.processEvents()

    def handle_code_finished(self, session_id=None):
        state = self.get_session(session_id)
        if state: state.code_worker = None
        if session_id == self.current_session_id:
            self.code_worker = None
            self.normalize_session_ui(state)

    def handle_code_input_request(self, prompt):
        if any(k in prompt.lower() for k in ["confirm", "yes/no", "是否"]):
             reply = QMessageBox.question(self, '需要确认', prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
             response = "yes" if reply == QMessageBox.Yes else "no"
        else:
             text, ok = QInputDialog.getText(self, "输入请求", prompt)
             response = text if ok else ""
        self.code_worker.provide_input(response)

if __name__ == "__main__":
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    
    # Use Fusion style as a base for cross-platform consistency
    app.setStyle("Fusion")
    
    # Global Font
    font = app.font()
    font.setFamily("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
