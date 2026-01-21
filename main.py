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
from PySide6.QtGui import QAction, QTextOption, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox, QFileDialog, QScrollArea, QFrame, QDialog, QFormLayout, QCheckBox, QGroupBox, QInputDialog, QMenu, QTabWidget)
from PySide6.QtCore import Qt, QThread, Signal

# Try importing OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

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
        
        # Bottom Bar (Import)
        bottom_layout = QHBoxLayout()
        import_btn = QPushButton("📦 导入新功能包")
        import_btn.clicked.connect(self.import_skill)
        bottom_layout.addWidget(import_btn)
        bottom_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)
        layout.addLayout(bottom_layout)
        
        self.refresh_list()

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

        # Security Level
        if 'security_level' in skill:
             sec_lvl = skill['security_level']
             color = "#e67c73" if "high" in sec_lvl.lower() else "#fbbc04"
             sec_lbl = QLabel(f"🛡️ 安全等级: {sec_lvl}")
             sec_lbl.setStyleSheet(f"color: {color}; font-size: 11px; margin-top: 4px;")
             v_layout.addWidget(sec_lbl)

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

class AutoResizingTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QFrame.NoFrame)
        self.textChanged.connect(self.adjustHeight)
        self.setStyleSheet("background: transparent;")
        
        # Set word wrap mode to break anywhere if needed (for long strings)
        self.setWordWrapMode(QTextOption.WrapAnywhere)

    def adjustHeight(self):
        doc_height = self.document().size().height()
        self.setFixedHeight(int(doc_height + 10))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjustHeight()

class EventCard(QFrame):
    save_skill_requested = Signal(str)

    def __init__(self, title, content, type="info", parent=None):
        super().__init__(parent)
        self.type = type
        self.code_content = content if type == "code_source" else None
        self.setObjectName("EventCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setLineWidth(1)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        
        # Header
        header_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setFixedWidth(24)
        icon_label.setAlignment(Qt.AlignCenter)
        if type == "think":
            icon_label.setText("🧠")
        elif type == "tool":
            icon_label.setText("🛠️")
        elif type == "code":
            icon_label.setText("💻")
        elif type == "code_source":
            icon_label.setText("📜")
        elif type == "success":
            icon_label.setText("✅")
        elif type == "error":
            icon_label.setText("❌")
        else:
            icon_label.setText("ℹ️")
            
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; color: #333; font-size: 13px;")
        
        header_layout.addWidget(icon_label)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Content
        if content:
            self.content_edit = AutoResizingTextEdit()
            self.content_edit.setPlainText(content)
            self.content_edit.setStyleSheet("color: #666; font-size: 12px; margin-top: 4px; line-height: 1.4; background: transparent;")
            layout.addWidget(self.content_edit)
            self.content_edit.adjustHeight()
            
        # Save as Skill Button
        if type == "code_source":
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            save_btn = QPushButton("保存为技能")
            save_btn.setCursor(Qt.PointingHandCursor)
            save_btn.setStyleSheet("""
                QPushButton { background-color: #e8f0fe; color: #1a73e8; border: none; border-radius: 4px; padding: 6px 12px; font-size: 11px; font-weight: bold; }
                QPushButton:hover { background-color: #d2e3fc; }
            """)
            save_btn.clicked.connect(lambda: self.save_skill_requested.emit(self.code_content))
            btn_layout.addWidget(save_btn)
            layout.addLayout(btn_layout)

        # Style
        self.setStyleSheet("""
            QFrame#EventCard {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)

    def append_content(self, text):
        if hasattr(self, 'content_edit'):
            self.content_edit.append(text)
        else:
            # Initialize if not present (though usually created with content)
            self.content_edit = AutoResizingTextEdit()
            self.content_edit.setPlainText(text)
            self.content_edit.setStyleSheet("color: #666; font-size: 12px; margin-top: 4px; line-height: 1.4; background: transparent;")
            self.layout().addWidget(self.content_edit)
            self.content_edit.adjustHeight()

class ChatBubble(QFrame):
    """自定义聊天气泡组件，支持展示 Thinking 过程"""
    def __init__(self, role, text, thinking=None):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setLineWidth(1)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        
        # 角色标签
        role_label = QLabel(f"<b>{role}</b>")
        role_label.setStyleSheet("color: #2c3e50;" if role == "User" else "color: #27ae60;")
        layout.addWidget(role_label)

        if thinking == "...":
            # Temporary Thinking State
            think_label = QLabel("🧠 正在思考...")
            think_label.setStyleSheet("color: #666; font-style: italic;")
            layout.addWidget(think_label)
        else:
            if thinking:
                 # Add thinking section
                 think_header = QLabel("💭 深度思考")
                 think_header.setStyleSheet("color: #5f6368; font-size: 11px; font-weight: bold; margin-bottom: 2px;")
                 layout.addWidget(think_header)
                 
                 think_content = AutoResizingTextEdit()
                 think_content.setPlainText(thinking)
                 think_content.setStyleSheet("color: #666; font-size: 12px; background: #f8f9fa; border: 1px solid #eee; border-radius: 4px;")
                 layout.addWidget(think_content)
                 think_content.adjustHeight()

            if text:
                 # Normal Content
                 if role == "Agent":
                     content_edit = AutoResizingTextEdit()
                     # Markdown rendering
                     try:
                        html_content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
                        # Basic styling
                        style = """
                        <style>
                           pre { background-color: #f1f3f4; padding: 8px; border-radius: 4px; }
                           code { background-color: #f1f3f4; padding: 2px 4px; border-radius: 2px; }
                           h1, h2, h3, h4 { color: #202124; margin-top: 10px; margin-bottom: 5px; }
                           a { color: #1a73e8; text-decoration: none; }
                           ul, ol { margin-left: 0px; padding-left: 20px; }
                        </style>
                        """
                        content_edit.setHtml(style + html_content)
                     except Exception as e:
                        # Fallback if markdown fails
                        content_edit.setPlainText(text)
                        
                     content_edit.setStyleSheet("color: #000; font-size: 13px; background: transparent;")
                     layout.addWidget(content_edit)
                     content_edit.adjustHeight()
                 else:
                     content_label = QLabel(text)
                     content_label.setWordWrap(True)
                     content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                     layout.addWidget(content_label)
        
        if role == "User":
            self.setStyleSheet("background-color: #e3f2fd; border-radius: 10px; margin: 5px;")
        else:
            self.setStyleSheet("background-color: #ffffff; border-radius: 10px; margin: 5px; border: 1px solid #ddd;")

class TaskMonitorWidget(QWidget):
    save_skill_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self.setStyleSheet("background-color: #f8f9fa; border-left: 1px solid #dadce0;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 2. Timeline Header
        timeline_header = QLabel("运行记录")
        timeline_header.setStyleSheet("padding: 16px 16px 8px; font-weight: bold; color: #5f6368; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;")
        layout.addWidget(timeline_header)
        
        # 3. Timeline Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border: none; background: transparent;")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self.timeline_container = QWidget()
        self.timeline_layout = QVBoxLayout(self.timeline_container)
        self.timeline_layout.setContentsMargins(16, 0, 16, 16)
        self.timeline_layout.setSpacing(12)
        self.timeline_layout.addStretch()
        
        self.scroll.setWidget(self.timeline_container)
        layout.addWidget(self.scroll)
        
    def set_status(self, status, desc=""):
        pass
        # Status board removed as per user request
        # if status == "idle":
        #     self.status_icon.setText("🟢")
        #     self.status_label.setText("就绪")
        #     self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #137333;")
        #     self.status_desc.setText("等待指令...")
        # elif status == "thinking":
        #     self.status_icon.setText("🔵")
        #     self.status_label.setText("思考中")
        #     self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #1a73e8;")
        #     self.status_desc.setText(desc or "正在分析需求...")
        # elif status == "running":
        #     self.status_icon.setText("🟠")
        #     self.status_label.setText("执行中")
        #     self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #f9ab00;")
        #     self.status_desc.setText(desc or "正在操作文件...")
        # elif status == "waiting":
        #     self.status_icon.setText("🔴")
        #     self.status_label.setText("等待确认")
        #     self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #d93025;")
        #     self.status_desc.setText(desc or "请在弹窗中确认...")
            
    def add_event(self, title, content, type="info"):
        card = EventCard(title, content, type)
        if type == "code_source":
            card.save_skill_requested.connect(self.save_skill_signal)
            
        # Insert before stretch (last item)
        self.timeline_layout.insertWidget(self.timeline_layout.count()-1, card)
        # Auto scroll to bottom
        QApplication.processEvents()
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def add_code_output(self, text):
        # Check if last item is code
        if self.timeline_layout.count() > 1: # 0 is stretch
            last_item = self.timeline_layout.itemAt(self.timeline_layout.count()-2)
            if last_item and last_item.widget():
                widget = last_item.widget()
                if isinstance(widget, EventCard) and widget.type == "code":
                    widget.append_content(text)
                    self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
                    return
        
        self.add_event("执行输出", text, "code")

    def clear(self):
        while self.timeline_layout.count() > 1:
            item = self.timeline_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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

            
        self.resize(1000, 800)
        self.workspace_dir = None
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f7fb; }
            QLabel[roleTitle="true"] { font-size: 18px; font-weight: 600; color: #202124; }
            QLabel[roleSubtitle="true"] { font-size: 12px; color: #5f6368; }
            QLineEdit#MainInput {
                padding: 10px 14px;
                border-radius: 22px;
                border: 1px solid #dadce0;
                background: #ffffff;
            }
            QLineEdit#MainInput:focus {
                border: 1px solid #1a73e8;
            }
        """)
        
        # Initialize conversation history
        self.messages = []
        
        self.config_manager = ConfigManager()
        # API Key is now in config_manager
        
        # Initialize SkillManager for UI
        self.skill_manager = SkillManager(None, self.config_manager)
        self.skill_generator = SkillGenerator(self.config_manager)

        # Connect to Interaction Bridge
        bridge.request_confirmation_signal.connect(self.handle_confirmation_request)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QWidget()
        sidebar.setFixedWidth(260)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 16, 16, 16)
        sidebar_layout.setSpacing(16)

        app_title = QLabel("DeepSeek Cowork")
        app_title.setProperty("roleTitle", True)
        app_subtitle = QLabel("文件助手 · DeepSeek")
        app_subtitle.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(app_title)
        sidebar_layout.addWidget(app_subtitle)

        new_chat_btn = QPushButton("＋ 新建对话")
        new_chat_btn.setStyleSheet("background-color: #1a73e8; color: white; border-radius: 999px; padding: 8px 16px;")
        new_chat_btn.clicked.connect(self.new_conversation)
        sidebar_layout.addWidget(new_chat_btn)

        # History List
        history_label = QLabel("历史会话")
        history_label.setStyleSheet("color: #5f6368; font-size: 12px; font-weight: bold; margin-top: 10px;")
        sidebar_layout.addWidget(history_label)

        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setStyleSheet("background: transparent; border: none;")
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        self.history_scroll.setWidget(self.history_container)
        sidebar_layout.addWidget(self.history_scroll, 1)

        sidebar_footer_label = QLabel("设置")
        sidebar_footer_label.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(sidebar_footer_label)
        sidebar_settings_btn = QPushButton("⚙️ 打开设置")
        sidebar_settings_btn.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(sidebar_settings_btn)
        
        sidebar_skills_btn = QPushButton("🧩 功能中心")
        sidebar_skills_btn.clicked.connect(self.open_skills_center)
        sidebar_layout.addWidget(sidebar_skills_btn)

        root_layout.addWidget(sidebar)

        main_container = QWidget()
        root_layout.addWidget(main_container, 1)
        layout = QVBoxLayout(main_container)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title_label = QLabel("你好，需要我为你做些什么？")
        title_label.setProperty("roleTitle", True)
        subtitle_label = QLabel("在这里描述你想对当前文件夹做的事，我会帮你完成。")
        subtitle_label.setProperty("roleSubtitle", True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)

        steps_label = QLabel("① 选择文件夹 → ② 点击设置，输入DeepSeek API key → ③ 描述你的需求 → ④ 查看并确认操作")
        steps_label.setStyleSheet("color: #5f6368; font-size: 12px; padding: 4px 0;")
        layout.addWidget(steps_label)

        ws_layout = QHBoxLayout()
        self.ws_label = QLabel("当前文件夹: 未选择")
        self.ws_label.setStyleSheet("color: red; font-weight: bold;")
        
        self.recent_btn = QPushButton("🕒")
        self.recent_btn.setToolTip("最近使用的文件夹")
        self.recent_btn.setFixedWidth(40)
        self.recent_btn.clicked.connect(self.show_recent_menu)
        
        self.ws_btn = QPushButton("📂 选择文件夹")
        self.ws_btn.clicked.connect(self.select_workspace)
        
        ws_layout.addWidget(self.ws_label)
        ws_layout.addStretch()
        ws_layout.addWidget(self.recent_btn)
        ws_layout.addWidget(self.ws_btn)
        layout.addLayout(ws_layout)
        
        self.recent_workspaces = self.config_manager.get("recent_workspaces", [])

        chat_frame = QFrame()
        chat_frame.setStyleSheet("background-color: #ffffff; border-radius: 16px;")
        chat_frame_layout = QVBoxLayout(chat_frame)
        chat_frame_layout.setContentsMargins(16, 16, 16, 16)
        
        # Active Skills Tag Area
        self.active_skills_layout = QHBoxLayout()
        self.active_skills_label = QLabel("本次会话使用的功能: ")
        self.active_skills_label.setStyleSheet("color: #666; font-size: 11px;")
        self.active_skills_layout.addWidget(self.active_skills_label)
        self.active_skills_layout.addStretch()
        chat_frame_layout.addLayout(self.active_skills_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.addStretch()
        scroll.setWidget(self.chat_container)
        chat_frame_layout.addWidget(scroll)
        layout.addWidget(chat_frame, 3)

        input_card = QFrame()
        input_card.setStyleSheet("background-color: #ffffff; border-radius: 999px; padding: 4px 12px;")
        input_layout = QHBoxLayout(input_card)
        input_layout.setContentsMargins(8, 4, 8, 4)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("MainInput")
        self.input_field.setPlaceholderText("例如：把这个文件夹里的图片按日期分类")
        self.input_field.returnPressed.connect(self.handle_send)

        self.example_btn = QPushButton("示例")
        self.example_btn.clicked.connect(self.insert_example)
        
        self.pause_btn = QPushButton("⏸️ 暂停")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setVisible(False)
        
        self.stop_btn = QPushButton("⏹️ 停止")
        self.stop_btn.clicked.connect(self.stop_agent)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet("color: #d93025;")
        self.stop_btn.setToolTip("User Control : If the model gets stuck in a loop (e.g., repeatedly listing the same directory), please use the Stop button to terminate the operation manually.")
        
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.handle_send)
        
        self.loop_hint = QLabel("⚠️ 若陷入死循环请按停止")
        self.loop_hint.setStyleSheet("color: #e74c3c; font-size: 10px; margin-left: 5px;")
        self.loop_hint.setVisible(False)

        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(self.example_btn)
        input_layout.addWidget(self.pause_btn)
        input_layout.addWidget(self.stop_btn)
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.loop_hint)

        layout.addWidget(input_card)

        chips_layout = QHBoxLayout()
        chips = ["批量整理文件", "重命名图片", "清理重复文件", "生成报表", "备份重要资料"]
        for text in chips:
            chip_btn = QPushButton(text)
            chip_btn.setStyleSheet("QPushButton { background-color: #f1f3f4; border: none; border-radius: 16px; padding: 6px 12px; color: #3c4043; } QPushButton:hover { background-color: #e4e7eb; }")
            chip_btn.clicked.connect(lambda _, t=text: self.apply_chip_text(t))
            chips_layout.addWidget(chip_btn)
        chips_layout.addStretch()
        layout.addLayout(chips_layout)

        # Right Sidebar (Task Monitor)
        self.task_monitor = TaskMonitorWidget()
        self.task_monitor.save_skill_signal.connect(self.handle_save_skill_request)
        root_layout.addWidget(self.task_monitor)

        # Initialize session state
        self.current_session_id = None
        os.makedirs(os.path.join(os.getcwd(), 'chat_history'), exist_ok=True)
        self.refresh_history_list()

    def handle_confirmation_request(self, message):
        dialog = QDialog(self)
        dialog.setWindowTitle("请再次确认")
        layout = QVBoxLayout(dialog)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        hint_label = QLabel("如果不确定，可以先在下方输入问题问问 AI：")
        layout.addWidget(hint_label)
        ai_input = QLineEdit()
        ai_input.setPlaceholderText("例如：这一步会删除原文件吗？是否可以撤销？")
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
            if not text:
                return
            
            # 将用户的回复显示在聊天记录中
            self.add_chat_bubble("User", text)
            self.messages.append({"role": "user", "content": text})
            self.save_chat_history()
            
            # 将文本作为决策结果返回给 Agent
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

    def insert_example(self):
        examples = [
            "把这个文件夹里所有的 .txt 文件改成 .md",
            "帮我把图片按日期分类到不同文件夹",
            "找出文件名里包含“备份”的文件并列出来"
        ]
        text, ok = QInputDialog.getItem(self, "示例指令", "选择一个示例填入输入框：", examples, 0, False)
        if ok and text:
            self.input_field.setText(text)
            self.input_field.setFocus()
            self.input_field.selectAll()

    def apply_chip_text(self, text):
        self.input_field.setText(text)
        self.input_field.setFocus()
        self.input_field.selectAll()

    def refresh_history_list(self):
        """刷新侧边栏的历史会话列表"""
        # Clear existing items
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        history_dir = os.path.join(os.getcwd(), 'chat_history')
        if not os.path.exists(history_dir):
            self.history_layout.addStretch()
            return

        files = glob.glob(os.path.join(history_dir, 'chat_history_*.json'))
        # Sort by modification time, newest first
        files.sort(key=os.path.getmtime, reverse=True)

        for file_path in files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not data: continue
                    
                    # Get title from first user message
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
                    # Check if this is the current session
                    if session_id == self.current_session_id:
                         btn.setStyleSheet("text-align: left; padding: 8px; border: none; border-radius: 8px; background-color: #e8f0fe; color: #1a73e8; font-weight: bold;")
                    else:
                         btn.setStyleSheet("text-align: left; padding: 8px; border: none; border-radius: 8px; background-color: transparent; color: #5f6368;")
                    
                    # Use closure to capture session_id
                    btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))
                    self.history_layout.addWidget(btn)
            except Exception as e:
                print(f"Error loading history file {file_path}: {e}")
                continue
        
        self.history_layout.addStretch()

    def load_session(self, session_id):
        """加载指定的会话"""
        self.current_session_id = session_id
        
        # Clear current view
        self.messages = []
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.chat_layout.addStretch()
        self.task_monitor.clear()
        self.active_skills_label.setText("本次会话使用的功能: ")

        history_path = os.path.join(os.getcwd(), 'chat_history', f'chat_history_{session_id}.json')
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    self.messages = json.load(f)
                
                # Reconstruct UI bubbles
                for msg in self.messages:
                    role = msg.get('role')
                    content = msg.get('content')
                    if role == 'user':
                        self.add_chat_bubble('User', content)
                    elif role == 'assistant' and content:
                        self.add_chat_bubble('Agent', content)
                
                self.append_log(f"System: 已加载会话 {session_id}")
            except Exception as e:
                self.append_log(f"Error loading session: {e}")
        
        # Update sidebar highlight
        self.refresh_history_list()

    def new_conversation(self):
        self.current_session_id = None
        self.messages = []
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.chat_layout.addStretch()
        self.task_monitor.clear()
        self.active_skills_label.setText("本次会话使用的功能: ")
        self.append_log("System: 新对话已创建")
        self.refresh_history_list()

    def save_chat_history(self):
        """Save chat history to JSON file"""
        if not self.messages:
            return

        if not self.current_session_id:
            self.current_session_id = uuid.uuid4().hex

        history_dir = os.path.join(os.getcwd(), 'chat_history')
        history_path = os.path.join(history_dir, f'chat_history_{self.current_session_id}.json')
        
        try:
            with open(history_path, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            # Only refresh if this is a new session (handled above) or if we really need to update titles.
            # To avoid flickering during chat, we don't refresh here.
            # The initial creation of session_id above triggers a refresh which is enough for the item to appear.
        except Exception as e:
            self.append_log(f"Error saving history: {e}")

    def select_workspace(self):
        directory = QFileDialog.getExistingDirectory(self, "选择工作区")
        if directory:
            self.load_workspace(directory)

    def load_workspace(self, directory):
        self.workspace_dir = directory
        self.ws_label.setText(f"当前工作区: {directory}")
        self.ws_label.setStyleSheet("color: green; font-weight: bold;")
        self.append_log(f"System: 工作区已切换至 {directory}")
        self.update_recent_workspaces(directory)

    def update_recent_workspaces(self, path):
        if path in self.recent_workspaces:
            self.recent_workspaces.remove(path)
        self.recent_workspaces.insert(0, path)
        self.recent_workspaces = self.recent_workspaces[:10]
        self.config_manager.set("recent_workspaces", self.recent_workspaces)

    def show_recent_menu(self):
        menu = QMenu(self)
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

    def open_settings(self):
        try:
            dialog = SettingsDialog(self.config_manager, self)
            if dialog.exec():
                self.append_log("System: 配置已更新")
        except Exception as e:
            self.append_log(f"Error opening settings: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to open settings: {str(e)}")

    def open_skills_center(self):
        try:
            dialog = SkillsCenterDialog(self.skill_manager, self.config_manager, self)
            dialog.exec()
        except Exception as e:
             self.append_log(f"Error opening skills center: {str(e)}")
             QMessageBox.critical(self, "Error", f"Failed to open skills center: {str(e)}")

    def handle_skill_used(self, skill_name):
        current_text = self.active_skills_label.text()
        if f"[{skill_name}]" not in current_text:
            self.active_skills_label.setText(current_text + f" [{skill_name}]")

    def toggle_pause(self):
        if hasattr(self, 'llm_worker') and self.llm_worker.isRunning():
            if self.llm_worker.is_paused:
                self.llm_worker.resume()
                self.pause_btn.setText("⏸️ 暂停")
            else:
                self.llm_worker.pause()
                self.pause_btn.setText("▶️ 继续")

    def stop_agent(self):
        if hasattr(self, 'llm_worker') and self.llm_worker.isRunning():
            self.llm_worker.stop()
            self.stop_btn.setEnabled(False) # Prevent double click

    def handle_send(self):
        if not self.workspace_dir:
            QMessageBox.warning(self, "提示", "请先选择一个工作区目录！")
            return
        user_text = self.input_field.text().strip()
        if not user_text:
            return

        self.add_chat_bubble("User", user_text)
        self.input_field.clear()
        self.send_btn.setEnabled(False)
        
        # Update messages history
        self.messages.append({"role": "user", "content": user_text})
        self.save_chat_history()
        
        self.process_agent_logic(user_text)

    def add_chat_bubble(self, role, text, thinking=None):
        bubble = ChatBubble(role, text, thinking)
        # 插入到倒数第二个位置（stretch之前）
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        # 滚动到底部
        QApplication.processEvents() 
        # (实际滚动逻辑略，PySide 自动处理较好)

    def append_log(self, text):
        if text.startswith("System:"):
             self.task_monitor.add_event("系统", text.replace("System: ", ""), "info")
        elif text.startswith("Error:"):
             self.task_monitor.add_event("错误", text.replace("Error: ", ""), "error")
        elif text.startswith("Agent:"):
             self.task_monitor.add_event("思考", text.replace("Agent: ", ""), "think")
        elif text.startswith("Reasoning:"):
             # Display full reasoning in TaskMonitor
             self.task_monitor.add_event("深度思考", text.replace("Reasoning: ", ""), "think")
        else:
             self.task_monitor.add_event("信息", text, "info")

    def handle_save_skill_request(self, code):
        """Handle 'Save as Skill' request"""
        # 1. Show Progress
        progress = QDialog(self)
        progress.setWindowTitle("正在生成技能")
        progress.setFixedSize(300, 100)
        p_layout = QVBoxLayout(progress)
        p_layout.addWidget(QLabel("正在分析代码并生成通用技能...\n这可能需要几秒钟。"))
        progress.setModal(True)
        progress.show()
        QApplication.processEvents()

        # 2. Call Generator
        result = self.skill_generator.refactor_code(code)
        progress.close()

        if "error" in result:
            QMessageBox.critical(self, "生成失败", f"Error: {result['error']}")
            return

        # 3. Confirmation Dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("确认新技能")
        dialog.resize(500, 600)
        d_layout = QVBoxLayout(dialog)
        
        # Form
        form = QFormLayout()
        name_input = QLineEdit(result.get("skill_name", ""))
        desc_input = QLineEdit(result.get("description", ""))
        desc_cn_input = QLineEdit(result.get("description_cn", ""))
        tool_name_input = QLineEdit(result.get("tool_name", ""))
        tool_desc_input = QLineEdit(result.get("description", "")) # reuse desc
        
        form.addRow("技能名称 (Skill Name):", name_input)
        form.addRow("中文描述:", desc_cn_input)
        form.addRow("英文描述:", desc_input)
        form.addRow("函数名称 (Tool Name):", tool_name_input)
        form.addRow("函数描述:", tool_desc_input)
        d_layout.addLayout(form)
        
        # Code Editor
        d_layout.addWidget(QLabel("代码实现:"))
        code_edit = QTextEdit()
        code_edit.setPlainText(result.get("code", ""))
        d_layout.addWidget(code_edit)
        
        # Buttons
        btns = QHBoxLayout()
        save_btn = QPushButton("确认创建")
        save_btn.setStyleSheet("background-color: #1a73e8; color: white; padding: 8px;")
        cancel_btn = QPushButton("取消")
        
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        d_layout.addLayout(btns)
        
        def on_save():
            res = create_new_skill(
                self.workspace_dir or os.getcwd(),
                name_input.text(),
                desc_input.text(),
                tool_name_input.text(),
                tool_desc_input.text(),
                code_edit.toPlainText(),
                desc_cn_input.text()
            )
            if "Success" in res:
                QMessageBox.information(dialog, "成功", res)
                dialog.accept()
                # Reload skills
                self.skill_manager.load_skills()
            else:
                QMessageBox.critical(dialog, "错误", res)

        save_btn.clicked.connect(on_save)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog.exec()

    def process_agent_logic(self, user_text):
        """启动 LLM 线程获取响应"""
        self.task_monitor.set_status("thinking")
        self.append_log(f"Agent: 正在深度思考 (DeepSeek CoT)...")
        
        # Insert a temporary "Thinking" bubble in the center
        self.temp_thinking_bubble = ChatBubble("agent", "正在思考...", thinking="...")
        self.chat_layout.insertWidget(self.chat_layout.count()-1, self.temp_thinking_bubble)
        QApplication.processEvents()

        self.llm_worker = LLMWorker(self.messages, self.config_manager, self.workspace_dir)
        self.llm_worker.finished_signal.connect(self.handle_llm_response)
        self.llm_worker.step_signal.connect(self.append_log) # Use the unified handler
        self.llm_worker.skill_used_signal.connect(self.handle_skill_used)
        self.llm_worker.start()
        
        # UI State Update
        self.pause_btn.setVisible(True)
        self.pause_btn.setText("⏸️ 暂停")
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.loop_hint.setVisible(True)

    def handle_llm_response(self, result):
        # Remove temporary thinking bubble
        if hasattr(self, 'temp_thinking_bubble'):
            self.temp_thinking_bubble.deleteLater()
            del self.temp_thinking_bubble

        # UI State Reset
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        self.loop_hint.setVisible(False)
        
        if "error" in result:
            self.task_monitor.set_status("idle")
            self.append_log(f"Error: {result['error']}")
            self.add_chat_bubble("System", f"Error: {result['error']}")
            self.send_btn.setEnabled(True)
            return

        reasoning = result.get("reasoning", "")
        content = result.get("content", "")
        role = result.get("role", "assistant")

        # 1. Update UI - Only show content, reasoning is already in TaskMonitor (via step_signal)
        # We pass thinking=None to hide it from the center chat
        self.add_chat_bubble("Agent", content, thinking=None)

        # 2. Update History (Important: Do not include reasoning_content in context for next turn)
        # According to DeepSeek docs, we just append content.
        self.messages.append({"role": role, "content": content})
        self.save_chat_history()

        # 3. Extract and Execute Code
        # Relaxed regex to catch ```python and ``` python
        code_match = re.search(r'```\s*python(.*?)```', content, re.DOTALL | re.IGNORECASE)
        if code_match:
            code_block = code_match.group(1).strip()
            # Show code source in Task Monitor
            self.task_monitor.add_event("即将执行的代码", code_block, "code_source")

            self.task_monitor.set_status("running", "正在执行代码...")
            self.append_log("System: 检测到代码块，准备执行...")
            self.code_worker = CodeWorker(code_block, self.workspace_dir)
            self.code_worker.output_signal.connect(self.task_monitor.add_code_output)
            self.code_worker.finished_signal.connect(self.handle_code_finished)
            self.code_worker.input_request_signal.connect(self.handle_code_input_request)
            
            # Show Stop Button for Code Execution
            self.stop_btn.setVisible(True)
            self.stop_btn.setEnabled(True)
            self.stop_btn.setText("⏹️ 停止代码")
            
            self.code_worker.start()
        else:
            self.task_monitor.set_status("idle")
            self.send_btn.setEnabled(True)

    def handle_code_finished(self):
        self.task_monitor.set_status("idle")
        self.task_monitor.add_event("系统", "代码执行完成", "success")
        self.stop_btn.setVisible(False)
        self.stop_btn.setText("⏹️ 停止") # Reset text
        self.send_btn.setEnabled(True)

    def handle_code_input_request(self, prompt):
        """Handle input() requests from python script"""
        if any(k in prompt.lower() for k in ["confirm", "yes/no", "是否"]):
             reply = QMessageBox.question(self, '需要确认', prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
             response = "yes" if reply == QMessageBox.Yes else "no"
        else:
             text, ok = QInputDialog.getText(self, "输入请求", prompt)
             response = text if ok else ""
        
        self.code_worker.provide_input(response)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
