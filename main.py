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
from PySide6.QtGui import QAction, QTextOption, QIcon, QFontMetrics
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox, QFileDialog, QScrollArea, QFrame, QDialog, QFormLayout, QCheckBox, QGroupBox, QInputDialog, QMenu, QTabWidget, QToolButton)
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
        import_btn = QPushButton("📦 导入新功能包")
        import_btn.clicked.connect(self.import_skill)
        bottom_layout.addWidget(import_btn)
        
        refresh_btn = QPushButton("🔄 刷新列表")
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
            deps_lbl = QLabel(f"📦 依赖: {deps_str}")
            deps_lbl.setStyleSheet("color: #1a73e8; font-size: 11px; margin-top: 4px;")
            v_layout.addWidget(deps_lbl)

        # Experience (Evolution)
        exp = skill.get('experience', [])
        if exp and isinstance(exp, list):
             exp_frame = QFrame()
             exp_frame.setStyleSheet("background-color: #f1f8e9; border-radius: 4px; padding: 4px; margin-top: 4px;")
             exp_layout = QVBoxLayout(exp_frame)
             exp_layout.setContentsMargins(4,4,4,4)
             exp_layout.setSpacing(2)
             exp_header = QLabel(f"📈 进化记录 ({len(exp)})")
             exp_header.setStyleSheet("font-weight: bold; color: #33691e; font-size: 11px;")
             exp_layout.addWidget(exp_header)
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
            icon_label.setText("❌")
            bg_color = "#fce8e6"
            text_color = "#c5221f"
            border_color = "#f6aea9"
        elif type == "success":
            icon_label.setText("✅")
            bg_color = "#e6f4ea"
            text_color = "#137333"
            border_color = "#ceead6"
        elif type == "warning":
            icon_label.setText("⚠️")
            bg_color = "#fef7e0"
            text_color = "#b06000"
            border_color = "#feefc3"
        else:
            icon_label.setText("ℹ️")
            bg_color = "#e8f0fe"
            text_color = "#1967d2"
            border_color = "#d2e3fc"
            
        layout.addWidget(icon_label)
        
        msg_label = QLabel(text)
        msg_label.setStyleSheet(f"color: {text_color}; font-weight: 500;")
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(msg_label)
        
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 16px;
                margin: 8px 40px;
            }}
        """)

class ChatBubble(QFrame):
    """自定义聊天气泡组件，支持展示 Thinking 过程 (可折叠)"""
    def __init__(self, role, text, thinking=None, duration=None):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWidth(0)
        
        # Main Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # User Bubble Design
        if role == "User":
            # Container for alignment
            container_layout = QHBoxLayout()
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addStretch() # Push to right
            
            # Bubble Frame
            bubble_frame = QFrame()
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #e7f8ff;
                    border-radius: 16px;
                    border-bottom-right-radius: 4px;
                    padding: 4px;
                }
            """)
            bubble_layout = QVBoxLayout(bubble_frame)
            bubble_layout.setContentsMargins(12, 12, 12, 12)
            bubble_layout.setSpacing(4)
            
            # Content
            content_label = QLabel(text)
            content_label.setWordWrap(True)
            content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            content_label.setStyleSheet("color: #000000; font-size: 14px; line-height: 1.5; border: none; background: transparent;")
            bubble_layout.addWidget(content_label)
            
            # Toolbar (Copy) - Removed as per user request
            # toolbar_layout = QHBoxLayout()
            # toolbar_layout.addStretch()
            
            # copy_btn = QPushButton("📄")
            # copy_btn.setCursor(Qt.PointingHandCursor)
            # copy_btn.setToolTip("复制内容")
            # copy_btn.setFixedSize(20, 20)
            # copy_btn.setStyleSheet("""
            #     QPushButton { 
            #         color: #999; 
            #         border: none; 
            #         background: transparent;
            #         font-size: 12px;
            #     } 
            #     QPushButton:hover { color: #333; }
            # """)
            # copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text))
            
            # toolbar_layout.addWidget(copy_btn)
            # bubble_layout.addLayout(toolbar_layout)

            container_layout.addWidget(bubble_frame)
            
            # Add some spacing on the left to prevent it from being too wide
            container_layout.setStretch(0, 1) # Stretch the left side
            container_layout.setStretch(1, 0) # Don't stretch the bubble
            
            layout.addLayout(container_layout)
            
        # Agent Bubble Design
        else: # Agent
            # 1. Thinking Section (DeepSeek Style)
            self.thinking_widget = QWidget()
            think_layout = QVBoxLayout(self.thinking_widget)
            think_layout.setContentsMargins(0, 0, 0, 0)
            think_layout.setSpacing(0)
            
            # Toggle Button
            self.think_toggle_btn = QPushButton("> 思考过程")
            self.think_toggle_btn.setCursor(Qt.PointingHandCursor)
            self.think_toggle_btn.setCheckable(True)
            self.think_toggle_btn.setChecked(False) # Default collapsed
            self.think_toggle_btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    background-color: #f6f6f6;
                    color: #666;
                    border: 1px solid #eee;
                    border-radius: 6px;
                    padding: 8px 12px;
                    font-size: 13px;
                    font-family: "Segoe UI", sans-serif;
                }
                QPushButton:hover { background-color: #eee; }
                QPushButton:checked { 
                    background-color: #f0f0f0; 
                    border-bottom-left-radius: 0; 
                    border-bottom-right-radius: 0;
                    border-bottom: none;
                }
            """)
            self.think_toggle_btn.toggled.connect(self.toggle_thinking)
            think_layout.addWidget(self.think_toggle_btn)

            # Container for Content + Tools
            self.think_container = QWidget()
            self.think_container.setVisible(False)
            self.think_container.setStyleSheet("""
                QWidget {
                    background: #fcfcfc;
                    border: 1px solid #eee;
                    border-top: none;
                    border-bottom-left-radius: 6px;
                    border-bottom-right-radius: 6px;
                }
            """)
            self.think_container_layout = QVBoxLayout(self.think_container)
            self.think_container_layout.setContentsMargins(12, 12, 12, 12)
            self.think_container_layout.setSpacing(12)

            # We don't create initial content here, it will be added dynamically
            
            think_layout.addWidget(self.think_container)
            layout.addWidget(self.thinking_widget)
            
            # Handle Initial State
            if thinking == "...":
                self.set_thinking_state(True)
            elif thinking:
                self.update_thinking(thinking, duration, is_final=True)
            else:
                self.thinking_widget.setVisible(False)

            # 2. Main Content
            self.content_edit = AutoResizingTextEdit()
            self.content_edit.setStyleSheet("background: transparent; border: none; margin-left: 2px;")
            layout.addWidget(self.content_edit)
            
            if text:
                self.set_main_content(text)

    def toggle_thinking(self, checked):
        self.think_container.setVisible(checked)
        # Update arrow
        arrow = "v" if checked else ">"
        text = self.think_toggle_btn.text()
        if text.startswith(">") or text.startswith("v"):
            text = text[1:]
        self.think_toggle_btn.setText(f"{arrow}{text}")
        
    def set_thinking_state(self, is_thinking):
        if is_thinking:
            self.think_toggle_btn.setText("v 正在思考...")
            self.think_toggle_btn.setChecked(True) # Auto expand when thinking starts
            self.update_thinking("正在分析需求...")
            self.thinking_widget.setVisible(True)

    def get_active_think_widget(self):
        """Get the last text widget in the thinking layout, or create one."""
        count = self.think_container_layout.count()
        if count > 0:
            last_item = self.think_container_layout.itemAt(count - 1)
            widget = last_item.widget()
            if isinstance(widget, AutoResizingTextEdit):
                return widget
        
        # Create new text widget
        new_widget = AutoResizingTextEdit()
        new_widget.setStyleSheet("background: transparent; border: none; color: #5f6368; font-size: 13px; line-height: 1.5;")
        self.think_container_layout.addWidget(new_widget)
        return new_widget
        
    def update_thinking(self, text=None, duration=None, is_final=False):
        if text is not None:
            widget = self.get_active_think_widget()
            widget.setPlainText(text)
            widget.adjustHeight()
        
        if duration:
            self.think_toggle_btn.setText(f"> 已思考 (用时 {duration:.1f} 秒)")
        
        if is_final:
            self.think_toggle_btn.setChecked(False) # Collapse when done
            
    def set_main_content(self, text):
        try:
            # GitHub-like CSS for Markdown
            style = """
            <style>
               body { 
                   font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                   line-height: 1.6; 
                   color: #24292f; 
                   margin: 0; 
                   font-size: 14px;
               }
               p { margin-top: 0; margin-bottom: 16px; }
               pre { 
                   background-color: #f6f8fa; 
                   padding: 16px; 
                   border-radius: 6px; 
                   border: 1px solid #d0d7de; 
                   white-space: pre-wrap; 
                   margin-bottom: 16px;
               }
               code { 
                   font-family: ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace; 
                   font-size: 85%; 
                   padding: 0.2em 0.4em; 
                   background-color: rgba(175, 184, 193, 0.2); 
                   border-radius: 6px; 
               }
               h1, h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; margin-top: 24px; }
               a { color: #0969da; text-decoration: none; }
               blockquote { 
                   border-left: 0.25em solid #d0d7de; 
                   color: #57606a; 
                   padding: 0 1em; 
                   margin: 0 0 16px 0; 
               }
               table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
               th, td { border: 1px solid #d0d7de; padding: 6px 13px; }
               th { background-color: #f6f8fa; }
            </style>
            """
            html_content = markdown.markdown(text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])
            self.content_edit.setHtml(style + html_content)
        except Exception:
            self.content_edit.setPlainText(text)
        self.content_edit.adjustHeight()
        
    def add_tool_card(self, card_widget):
        self.think_container_layout.addWidget(card_widget)
        # Ensure thinking is visible when tool is added
        if not self.think_toggle_btn.isChecked():
            self.think_toggle_btn.setChecked(True)

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

class ToolCallCard(QFrame):
    def __init__(self, tool_name, args, tool_id):
        super().__init__()
        self.tool_id = tool_id
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            ToolCallCard {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        icon_label = QLabel("🛠️") 
        icon_label.setStyleSheet("font-size: 14px;")
        
        name_label = QLabel(f"调用工具: {tool_name}")
        name_label.setStyleSheet("font-weight: bold; color: #1a73e8; font-size: 13px;")
        
        self.status_label = QLabel("Running...")
        self.status_label.setStyleSheet("color: #f1c40f; font-weight: bold; font-size: 11px;")
        
        self.toggle_btn = QToolButton()
        self.toggle_btn.setText("展开")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setToolTip("点击查看参数和结果")
        self.toggle_btn.setStyleSheet("border: none; background: transparent; font-weight: bold; color: #5f6368;")
        self.toggle_btn.clicked.connect(self.toggle_details)

        header_layout.addWidget(icon_label)
        header_layout.addWidget(name_label)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        header_layout.addWidget(self.toggle_btn)
        layout.addLayout(header_layout)
        
        # Details Container (Args + Result)
        self.details_container = QWidget()
        self.details_container.setVisible(False)
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)
        layout.addWidget(self.details_container)

        # Args
        if isinstance(args, str):
            try:
                args_obj = json.loads(args)
                args_text = json.dumps(args_obj, indent=2, ensure_ascii=False)
            except:
                args_text = args
        else:
            args_text = json.dumps(args, indent=2, ensure_ascii=False)
            
        self.args_label = AutoResizingTextEdit()
        self.args_label.setPlainText(args_text)
        self.args_label.setStyleSheet("""
            QTextEdit {
                color: #444; 
                font-family: 'Consolas', monospace; 
                font-size: 11px; 
                background-color: #f8f9fa; 
                padding: 8px; 
                border-radius: 4px;
            }
        """)
        details_layout.addWidget(self.args_label)
        
        # Result Area (Collapsible)
        self.result_container = QWidget()
        self.result_container.setVisible(False)
        res_layout = QVBoxLayout(self.result_container)
        res_layout.setContentsMargins(0, 8, 0, 0)
        
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #eeeeee;")
        res_layout.addWidget(line)
        
        res_title = QLabel("执行结果:")
        res_title.setStyleSheet("font-weight: bold; color: #333; font-size: 11px; margin-top: 4px;")
        res_layout.addWidget(res_title)
        
        self.result_label = AutoResizingTextEdit()
        self.result_label.setStyleSheet("color: #333; font-family: 'Consolas', monospace; font-size: 11px;")
        res_layout.addWidget(self.result_label)
        
        details_layout.addWidget(self.result_container)

    def toggle_details(self, checked):
        self.details_container.setVisible(checked)
        self.toggle_btn.setText("收起" if checked else "展开")

    def set_result(self, result_text):
        self.status_label.setText("Completed")
        self.status_label.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 11px;")
        
        display_text = result_text
        if len(display_text) > 2000:
            display_text = display_text[:2000] + "... (output truncated)"
            
        self.result_label.setPlainText(display_text)
        self.result_container.setVisible(True)

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
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                padding: 6px 24px 6px 12px;
                color: #24292f;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1a73e8;
                color: #ffffff;
            }
        """)
        
        # Initialize conversation history
        self.messages = []
        
        # Track Tool Cards
        self.tool_cards = {}
        
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

        # Right Sidebar (Task Monitor) - Removed
        # self.task_monitor = TaskMonitorWidget()
        # self.task_monitor.save_skill_signal.connect(self.handle_save_skill_request)
        # root_layout.addWidget(self.task_monitor)

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
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        hint_label = QLabel("如果不确定，可以先在下方输入问题问问 AI：")
        hint_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        # self.task_monitor.clear()
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
        # self.task_monitor.clear()
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
        
        # Optimize path display using QFontMetrics
        # Calculate available width: Window(~1000) - Sidebar(260) - Margins(64) - Buttons(~150) = ~526
        # Use 450px to be safe and ensure UI doesn't break
        font_metrics = QFontMetrics(self.ws_label.font())
        display_path = font_metrics.elidedText(directory, Qt.ElideMiddle, 450)
            
        self.ws_label.setText(f"当前工作区: {display_path}")
        self.ws_label.setToolTip(directory) # Show full path on hover
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

    def add_tool_card(self, data):
        card = ToolCallCard(data['name'], data['args'], data['id'])
        self.tool_cards[data['id']] = card
        
        # Add to the active thinking bubble if available
        if hasattr(self, 'temp_thinking_bubble') and self.temp_thinking_bubble:
            self.temp_thinking_bubble.add_tool_card(card)
        elif hasattr(self, 'last_agent_bubble') and self.last_agent_bubble:
             # Fallback to last bubble
             self.last_agent_bubble.add_tool_card(card)
        else:
            # Fallback to direct layout insertion (should rarely happen)
            wrapper = QWidget()
            layout = QHBoxLayout(wrapper)
            layout.setContentsMargins(48, 4, 16, 4)
            layout.addWidget(card)
            layout.addStretch()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrapper)
        
        QApplication.processEvents()

    def update_tool_card(self, data):
        tool_id = data['id']
        result = data['result']
        if tool_id in self.tool_cards:
            self.tool_cards[tool_id].set_result(result)

    def add_chat_bubble(self, role, text, thinking=None, duration=None):
        bubble = ChatBubble(role, text, thinking, duration)
        # 插入到倒数第二个位置（stretch之前）
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        # 滚动到底部
        QApplication.processEvents() 
        # (实际滚动逻辑略，PySide 自动处理较好)

    def add_system_toast(self, text, type="info"):
        toast = SystemToast(text, type)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, toast)
        QApplication.processEvents()

    def append_log(self, text):
        print(f"[Log] {text}")
        # if text.startswith("System:"):
        #      self.task_monitor.add_event("系统", text.replace("System: ", ""), "info")
        # elif text.startswith("Error:"):
        #      self.task_monitor.add_event("错误", text.replace("Error: ", ""), "error")
        # elif text.startswith("Agent:"):
        #      self.task_monitor.add_event("思考", text.replace("Agent: ", ""), "think")
        # elif text.startswith("Reasoning:"):
        #      # Display full reasoning in TaskMonitor
        #      self.task_monitor.add_event("深度思考", text.replace("Reasoning: ", ""), "think")
        # else:
        #      self.task_monitor.add_event("信息", text, "info")

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
        # self.task_monitor.set_status("thinking")
        self.append_log(f"Agent: 正在深度思考 (DeepSeek CoT)...")
        
        # Insert a temporary "Thinking" bubble
        self.temp_thinking_bubble = ChatBubble("agent", "", thinking="正在分析需求...")
        self.temp_thinking_bubble.set_thinking_state(True)
        self.chat_layout.insertWidget(self.chat_layout.count()-1, self.temp_thinking_bubble)
        QApplication.processEvents()

        self.llm_worker = LLMWorker(self.messages, self.config_manager, self.workspace_dir)
        self.llm_worker.finished_signal.connect(self.handle_llm_response)
        self.llm_worker.step_signal.connect(self.append_log) # Use the unified handler
        self.llm_worker.thinking_signal.connect(self.handle_thinking_signal) # Real-time thinking
        self.llm_worker.skill_used_signal.connect(self.handle_skill_used)
        self.llm_worker.tool_call_signal.connect(self.add_tool_card)
        self.llm_worker.tool_result_signal.connect(self.update_tool_card)
        self.llm_worker.start()
        
        # UI State Update
        self.pause_btn.setVisible(True)
        self.pause_btn.setText("⏸️ 暂停")
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.loop_hint.setVisible(True)

    def handle_thinking_signal(self, text):
        """实时更新思考过程"""
        if hasattr(self, 'temp_thinking_bubble') and self.temp_thinking_bubble:
            self.temp_thinking_bubble.update_thinking(text)
        elif hasattr(self, 'last_agent_bubble') and self.last_agent_bubble:
            self.last_agent_bubble.update_thinking(text)

    def handle_llm_response(self, result):
        # Retrieve the bubble to update
        if hasattr(self, 'temp_thinking_bubble') and self.temp_thinking_bubble:
            bubble = self.temp_thinking_bubble
            del self.temp_thinking_bubble
        else:
            bubble = ChatBubble("agent", "", thinking=result.get("reasoning"))
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        
        self.last_agent_bubble = bubble

        # UI State Reset
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        self.loop_hint.setVisible(False)
        
        if "error" in result:
            # self.task_monitor.set_status("idle")
            self.append_log(f"Error: {result['error']}")
            self.add_system_toast(f"Error: {result['error']}", "error")
            bubble.set_main_content(f"⚠️ Error: {result['error']}")
            self.send_btn.setEnabled(True)
            return

        reasoning = result.get("reasoning", "")
        content = result.get("content", "")
        role = result.get("role", "assistant")
        duration = result.get("duration", None)

        # 1. Update UI - Show content AND reasoning
        # Update duration and state only. Reasoning text is already set via streaming or __init__.
        bubble.update_thinking(duration=duration, is_final=True)
        bubble.set_main_content(content)

        # 2. Update History
        self.messages.append({
            "role": role, 
            "content": content,
            "reasoning": reasoning
        })
        self.save_chat_history()

        # 3. Extract and Execute Code
        # Relaxed regex to catch ```python and ``` python
        code_match = re.search(r'```\s*python(.*?)```', content, re.DOTALL | re.IGNORECASE)
        if code_match:
            code_block = code_match.group(1).strip()
            # self.task_monitor.add_event("即将执行的代码", code_block, "code_source")

            # self.task_monitor.set_status("running", "正在执行代码...")
            self.append_log("System: 检测到代码块，准备执行...")
            god_mode = self.config_manager.get_god_mode()
            
            if god_mode:
                 self.add_system_toast("⚠️ God Mode 已启用：正在执行高权限代码，请注意风险", "warning")

            self.code_worker = CodeWorker(code_block, self.workspace_dir, god_mode=god_mode)
            self.code_worker.output_signal.connect(self.handle_code_output)
            self.code_worker.finished_signal.connect(self.handle_code_finished)
            self.code_worker.input_request_signal.connect(self.handle_code_input_request)
            
            # Show Stop Button for Code Execution
            self.stop_btn.setVisible(True)
            self.stop_btn.setEnabled(True)
            self.stop_btn.setText("⏹️ 停止代码")
            
            self.code_worker.start()
        else:
            # self.task_monitor.set_status("idle")
            self.send_btn.setEnabled(True)

    def handle_code_output(self, text):
        if hasattr(self, 'last_agent_bubble') and self.last_agent_bubble:
            # Check if we already have a code output widget
            if not hasattr(self.last_agent_bubble, 'code_output_edit'):
                # Create one
                label = QLabel("执行结果:")
                label.setStyleSheet("font-weight: bold; color: #333; margin-top: 8px; margin-left: 4px;")
                self.last_agent_bubble.layout().addWidget(label)
                
                self.last_agent_bubble.code_output_edit = AutoResizingTextEdit()
                self.last_agent_bubble.code_output_edit.setStyleSheet("color: #444; font-family: Consolas; background: #f8f9fa; border: 1px solid #eee; padding: 8px; border-radius: 4px; margin-left: 4px;")
                self.last_agent_bubble.code_output_edit.setReadOnly(True)
                self.last_agent_bubble.layout().addWidget(self.last_agent_bubble.code_output_edit)
            
            self.last_agent_bubble.code_output_edit.append(text)
            self.last_agent_bubble.code_output_edit.adjustHeight()
            QApplication.processEvents()

    def handle_code_finished(self):
        # self.task_monitor.set_status("idle")
        # self.task_monitor.add_event("系统", "代码执行完成", "success")
        self.add_system_toast("代码执行完成", "success")
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
    # 1. Enable High DPI Scaling
    # Qt 6 automatically handles this, but setting the policy can help with fractional scaling (e.g. 150%)
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    
    # 2. Force Fusion Style for consistent look across Windows versions
    # This avoids issues where system theme (Dark/Light) breaks hardcoded colors
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
