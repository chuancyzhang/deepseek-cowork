import sys
import subprocess
import tempfile
import os
import time
import ast
import re
import json
import platform
import uuid
import glob
import markdown
import socket
from datetime import datetime
from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.agent import LLMWorker, CodeWorker, repair_tool_call_sequence
from core.skill_generator import SkillGenerator
from core.interaction import bridge
from core.env_utils import get_app_data_dir, get_base_dir, get_python_executable
from core.chat_storage import ChatStorage
from core.theme import apply_theme, DesignTokens
from core.daemon import DaemonClient, run_daemon, DEFAULT_HOST, DEFAULT_PORT, get_runtime_signature
from core.agent_manager import AGENT_LIVE_STATUSES, get_agent_manager_registry
from core.llm.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    SUPPORTED_DEEPSEEK_REASONING_EFFORTS,
    normalize_deepseek_reasoning_effort,
)
from core.plan_mode import (
    DEFAULT_PLAN_CONFIG,
    PLAN_MODE_DISABLED,
    PLAN_MODE_AWAITING_USER_INPUT,
    PLAN_MODE_EXPLORING,
    PLAN_MODE_READY_TO_PRESENT,
    PLAN_PROTOCOL_VERSION,
    RUN_MODE_EXECUTION,
    RUN_MODE_PLANNING,
    derive_plan_phase,
    json_copy,
    normalize_plan_config,
    normalize_plan_phase,
    normalize_pending_plan_questions,
    normalize_run_context,
)
import shutil
import traceback
import qtawesome as qta
from PySide6.QtGui import (QAction, QTextOption, QIcon, QFont, QFontMetrics, QPixmap, 
                          QDesktopServices, QGuiApplication, QColor, QPainter, 
                          QBrush, QPainterPath, QTextCursor, QTextCharFormat, QPen)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox, QFileDialog, QScrollArea, QFrame, QDialog, QFormLayout, QCheckBox, QGroupBox, QInputDialog, QMenu, QTabWidget, QToolButton, QFileSystemModel, QTreeView, QSplitter, QSplitterHandle, QStackedWidget, QSizePolicy, QGraphicsDropShadowEffect, QGridLayout, QComboBox, QSystemTrayIcon, QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer, QSize, QRect, QPropertyAnimation, QEasingCurve, QVariantAnimation

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

HISTORY_MIGRATION_VERSION = 2
CONTENT_FLUSH_INTERVAL_MS = 120
THINKING_FLUSH_INTERVAL_MS = 140
SCROLL_FLUSH_INTERVAL_MS = 24
SCROLL_BOTTOM_THRESHOLD_PX = 36
STREAM_RENDER_INTERVAL_SEC = 0.12
STREAM_PLAIN_TEXT_THRESHOLD = 2400


def set_stylesheet_if_changed(widget, stylesheet):
    if widget.property("_cached_stylesheet") == stylesheet:
        return
    widget.setStyleSheet(stylesheet)
    widget.setProperty("_cached_stylesheet", stylesheet)


def set_text_if_changed(widget, text):
    if widget.text() == text:
        return
    widget.setText(text)


def plan_phase_label(phase):
    mapping = {
        PLAN_MODE_DISABLED: "未启用",
        PLAN_MODE_EXPLORING: "探索中",
        PLAN_MODE_AWAITING_USER_INPUT: "等待输入",
        PLAN_MODE_READY_TO_PRESENT: "待呈现",
    }
    return mapping.get(normalize_plan_phase(phase), "未启用")


def readable_skill_name(skill):
    if not isinstance(skill, dict):
        return ""
    return skill.get("display_name") or skill.get("description_cn") or skill.get("name", "")


def readable_risk_level(value):
    text = (value or "").strip().lower()
    if text == "high":
        return ("高风险", DesignTokens.error_text)
    if text == "medium":
        return ("中风险", DesignTokens.warning_text)
    if text == "low":
        return ("低风险", DesignTokens.info_text)
    return ("常规", DesignTokens.text_secondary)


def session_status_text(status, im_provider=None):
    if im_provider == "feishu":
        return ("来自飞书", DesignTokens.info_text, DesignTokens.info_bg)
    mapping = {
        "running": ("进行中", DesignTokens.info_text, DesignTokens.info_bg),
        "completed": ("已完成", DesignTokens.success_text, DesignTokens.success_bg),
        "interrupted": ("中断", DesignTokens.warning_text, DesignTokens.warning_bg),
        "error": ("异常", DesignTokens.error_text, DesignTokens.error_bg),
        "draft": ("新任务", DesignTokens.text_secondary, DesignTokens.bg_secondary),
    }
    return mapping.get(status or "draft", ("新任务", DesignTokens.text_secondary, DesignTokens.bg_secondary))


def summarize_tool_action(tool_name, args):
    args = args or {}
    name = tool_name or "tool"
    path = ""
    for key in ("path", "file_path", "src", "source", "target", "dst", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            path = value.strip()
            break
    if name in {"read_file", "read_docx", "read_pptx", "read_excel", "read_pdf"}:
        return "查看文件", path or "读取内容"
    if name in {"write_file", "update_file", "write_docx", "write_excel", "create_pptx"}:
        return "更新文件", path or "写入结果"
    if name in {"rename_file"}:
        return "整理文件", path or "移动或重命名"
    if name in {"delete_file"}:
        return "删除文件", path or "删除项目"
    if name in {"list_files", "search_files", "glob", "grep", "search_codebase"}:
        return "扫描工作区", path or "查找相关文件"
    if name in {"bash", "run_command"}:
        cmd = args.get("command") or args.get("cmd") or ""
        return "执行命令", cmd[:48] if cmd else "运行系统命令"
    if name in {"request_user_approval"}:
        return "等待确认", "需要你决定下一步"
    if name in {"request_user_input"}:
        return "等待输入", "需要你补充信息"
    if name in {"publish_artifacts"}:
        return "交付产物", "准备交付文件或图片"
    return name.replace("_", " ").title(), path or "执行步骤"


def extract_related_paths(tool_name, args):
    if not isinstance(args, dict):
        return []
    paths = []
    for key in ("path", "file_path", "src", "source", "target", "dst", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    if tool_name in {"write_file", "update_file", "write_docx", "write_excel", "create_pptx", "rename_file", "delete_file"}:
        return paths
    return []

def readable_risk_level(value):
    text = (value or "").strip().lower()
    if text == "high":
        return ("High Risk", DesignTokens.error_text)
    if text == "medium":
        return ("Medium Risk", DesignTokens.warning_text)
    if text == "low":
        return ("Low Risk", DesignTokens.info_text)
    return ("Standard", DesignTokens.text_secondary)


def session_status_text(status, im_provider=None):
    if im_provider == "feishu":
        return ("Feishu", DesignTokens.info_text, DesignTokens.info_bg)
    mapping = {
        "running": ("In Progress", DesignTokens.info_text, DesignTokens.info_bg),
        "completed": ("Completed", DesignTokens.success_text, DesignTokens.success_bg),
        "interrupted": ("Interrupted", DesignTokens.warning_text, DesignTokens.warning_bg),
        "error": ("Error", DesignTokens.error_text, DesignTokens.error_bg),
        "draft": ("New", DesignTokens.text_secondary, DesignTokens.bg_secondary),
    }
    return mapping.get(status or "draft", ("New", DesignTokens.text_secondary, DesignTokens.bg_secondary))


def summarize_tool_action(tool_name, args):
    args = args or {}
    name = tool_name or "tool"
    path = ""
    for key in ("path", "file_path", "src", "source", "target", "dst", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            path = value.strip()
            break
    if name in {"read_file", "read_docx", "read_pptx", "read_excel", "read_pdf"}:
        return "Read File", path or "Read file contents"
    if name in {"write_file", "update_file", "write_docx", "write_excel", "create_pptx"}:
        return "Update File", path or "Write output to disk"
    if name in {"rename_file"}:
        return "Rename File", path or "Move or rename item"
    if name in {"delete_file"}:
        return "Delete File", path or "Remove item"
    if name in {"list_files", "search_files", "glob", "grep", "search_codebase"}:
        return "Scan Workspace", path or "Search related files"
    if name in {"bash", "run_command"}:
        cmd = args.get("command") or args.get("cmd") or ""
        return "Run Command", cmd[:48] if cmd else "Execute a shell command"
    if name in {"request_user_approval"}:
        return "Await Approval", "Need user confirmation for the next step"
    if name in {"request_user_input"}:
        return "Await Input", "Need user input before continuing"
    if name in {"publish_artifacts"}:
        return "Deliver Artifacts", "Prepare files or images for delivery"
    return name.replace("_", " ").title(), path or "Execution step"


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

class SafeApplication(QApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.main_window = None

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            traceback.print_exc()
            if self.main_window:
                try:
                    self.main_window.add_system_toast("发生异常，但程序将继续运行。", "error")
                except Exception:
                    pass
            return False

class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(520, 360)
        self.config_manager = config_manager
        self._main = parent
        
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        base_tab = QWidget()
        base_layout = QVBoxLayout(base_tab)
        form_layout = QFormLayout()

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI / DeepSeek / Compatible", "openai")
        self.provider_combo.addItem("Anthropic / Claude / Minimax", "anthropic")
        
        current_provider = self.config_manager.get("llm_provider", "openai")
        index = self.provider_combo.findData(current_provider)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        
        form_layout.addRow("LLM Provider:", self.provider_combo)

        # API Key
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.config_manager.get("api_key", ""))
        form_layout.addRow("API Key:", self.api_key_input)
        
        # API Key Guide
        guide_label = QLabel('API Key 获取方法：<br>① DeepSeek: <a href="https://platform.deepseek.com/">DeepSeek 开发者平台</a>')
        guide_label.setStyleSheet("color: #5f6368; font-size: 11px; margin-bottom: 8px;")
        guide_label.setOpenExternalLinks(True)
        guide_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        form_layout.addRow("", guide_label)
        
        # Base URL
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText(DEFAULT_DEEPSEEK_BASE_URL)
        self.base_url_input.setText(self.config_manager.get("base_url", DEFAULT_DEEPSEEK_BASE_URL))
        form_layout.addRow("API Base URL (可选):", self.base_url_input)

        # Model Name
        self.model_name_input = QLineEdit()
        self.model_name_input.setPlaceholderText(DEFAULT_DEEPSEEK_MODEL)
        self.model_name_input.setText(self.config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL))
        form_layout.addRow("Model Name:", self.model_name_input)

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
        

        base_layout.addLayout(form_layout)
        self.tabs.addTab(base_tab, "基础设置")

        im_tab = QWidget()
        im_layout = QVBoxLayout(im_tab)
        im_form = QFormLayout()

        self.feishu_app_id_input = QLineEdit()
        self.feishu_app_id_input.setText(self.config_manager.get("feishu_app_id", ""))
        im_form.addRow("飞书 App ID:", self.feishu_app_id_input)

        self.feishu_app_secret_input = QLineEdit()
        self.feishu_app_secret_input.setEchoMode(QLineEdit.Password)
        self.feishu_app_secret_input.setPlaceholderText("••••••••")
        self.feishu_app_secret_input.setText(self.config_manager.get("feishu_app_secret", ""))
        im_form.addRow("飞书 App Secret:", self.feishu_app_secret_input)

        im_layout.addLayout(im_form)
        gateway_bar = QHBoxLayout()
        gateway_info = QLabel("飞书长连接模式：无需配置 Webhook\n服务监听: 0.0.0.0:8001")
        gateway_info.setStyleSheet("color: #5f6368; font-size: 11px;")
        gateway_btn = QPushButton("启动网关")
        gateway_btn.setIcon(qta.icon('fa5s.play', color='#374151'))
        def start_gateway():
            try:
                self.config_manager.set("feishu_app_id", self.feishu_app_id_input.text().strip())
                self.config_manager.set("feishu_app_secret", self.feishu_app_secret_input.text().strip())
                if hasattr(self._main, "try_connect_daemon"):
                    self._main.try_connect_daemon(allow_start=True, retries=6)
                if hasattr(self._main, "start_gateway_process"):
                    self._main.start_gateway_process()
                QMessageBox.information(self, "统一消息网关", "已启动。\n飞书长连接模式已启用\n服务监听: 0.0.0.0:8001")
            except Exception:
                QMessageBox.warning(self, "统一消息网关", "启动失败，请检查环境和依赖。")
        gateway_btn.clicked.connect(start_gateway)
        gateway_bar.addWidget(gateway_info, 1)
        gateway_bar.addWidget(gateway_btn)
        im_layout.addLayout(gateway_bar)
        self.tabs.addTab(im_tab, "企业消息")

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
        # Save Provider
        self.config_manager.set("llm_provider", self.provider_combo.currentData())
        # Save API Key
        self.config_manager.set("api_key", self.api_key_input.text().strip())
        # Save Base URL
        base_url = self.base_url_input.text().strip()
        if not base_url:
            base_url = DEFAULT_DEEPSEEK_BASE_URL
        self.config_manager.set("base_url", base_url)
        # Save Model Name
        model_name = self.model_name_input.text().strip()
        if not model_name:
            model_name = DEFAULT_DEEPSEEK_MODEL
        self.config_manager.set("model_name", model_name)

        self.config_manager.set("default_workspace", self.default_ws_input.text().strip())
        self.config_manager.set_chat_history_dir(self.history_dir_input.text().strip())
        # Save God Mode
        self.config_manager.set_god_mode(self.god_mode_check.isChecked())

        self.config_manager.set("feishu_app_id", self.feishu_app_id_input.text().strip())
        self.config_manager.set("feishu_app_secret", self.feishu_app_secret_input.text().strip())
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

        h_layout.addLayout(v_layout, 1)
        h_layout.addStretch()
        
        # Controls
        controls_container = QWidget()
        controls_container.setFixedWidth(96)
        c_layout = QVBoxLayout(controls_container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
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
        
        c_layout.addWidget(toggle_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        h_layout.addWidget(controls_container, 0, Qt.AlignRight | Qt.AlignVCenter)
        
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

class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(720, 560)
        self.config_manager = config_manager
        self._main = parent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)

        group_style = (
            "QGroupBox { font-weight: 600; border: 1px solid #dbe3ee; border-radius: 14px; "
            "margin-top: 10px; padding: 18px 16px 16px 16px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }"
        )

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        base_tab = QWidget()
        base_layout = QVBoxLayout(base_tab)
        base_layout.setContentsMargins(12, 16, 12, 16)
        base_layout.setSpacing(18)

        model_group = QGroupBox("模型与连接")
        model_group.setStyleSheet(group_style)
        model_layout = QFormLayout(model_group)
        model_layout.setSpacing(12)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI 兼容服务（含 DeepSeek）", "openai")
        self.provider_combo.addItem("Anthropic / Claude 系列", "anthropic")
        current_provider = self.config_manager.get("llm_provider", "openai")
        index = self.provider_combo.findData(current_provider)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        model_layout.addRow("模型供应商", self.provider_combo)

        provider_hint = QLabel("常用场景直接选择默认供应商即可，接口地址与模型名称可在高级配置里调整。")
        provider_hint.setWordWrap(True)
        provider_hint.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        model_layout.addRow("", provider_hint)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("粘贴你的 API Key")
        self.api_key_input.setText(self.config_manager.get("api_key", ""))
        model_layout.addRow("API Key", self.api_key_input)

        guide_label = QLabel('获取方式：<a href="https://platform.deepseek.com/">DeepSeek 开发者平台</a>')
        guide_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        guide_label.setOpenExternalLinks(True)
        guide_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        model_layout.addRow("", guide_label)

        self.advanced_model_toggle = QCheckBox("显示高级模型配置")
        self.advanced_model_toggle.setChecked(True)
        model_layout.addRow("", self.advanced_model_toggle)

        self.advanced_model_container = QWidget()
        advanced_model_layout = QFormLayout(self.advanced_model_container)
        advanced_model_layout.setContentsMargins(0, 0, 0, 0)
        advanced_model_layout.setSpacing(12)
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText(DEFAULT_DEEPSEEK_BASE_URL)
        self.base_url_input.setText(self.config_manager.get("base_url", DEFAULT_DEEPSEEK_BASE_URL))
        advanced_model_layout.addRow("自定义接口地址", self.base_url_input)

        self.model_name_input = QLineEdit()
        self.model_name_input.setPlaceholderText(DEFAULT_DEEPSEEK_MODEL)
        self.model_name_input.setText(self.config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL))
        advanced_model_layout.addRow("模型名称", self.model_name_input)

        self.deepseek_thinking_check = QCheckBox("启用 Thinking 模式")
        self.deepseek_thinking_check.setChecked(
            bool(self.config_manager.get("deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED))
        )
        advanced_model_layout.addRow("DeepSeek 思考模式", self.deepseek_thinking_check)

        self.deepseek_reasoning_effort_combo = QComboBox()
        for effort in SUPPORTED_DEEPSEEK_REASONING_EFFORTS:
            self.deepseek_reasoning_effort_combo.addItem(effort, effort)
        current_effort = normalize_deepseek_reasoning_effort(
            self.config_manager.get("deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT)
        )
        effort_index = self.deepseek_reasoning_effort_combo.findData(current_effort)
        if effort_index >= 0:
            self.deepseek_reasoning_effort_combo.setCurrentIndex(effort_index)
        advanced_model_layout.addRow("DeepSeek 思考强度", self.deepseek_reasoning_effort_combo)

        deepseek_hint = QLabel("以上两项仅在使用 DeepSeek 接口时生效，其他 OpenAI 兼容服务不会下发这些参数。")
        deepseek_hint.setWordWrap(True)
        deepseek_hint.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        advanced_model_layout.addRow("", deepseek_hint)
        model_layout.addRow("", self.advanced_model_container)
        self.advanced_model_toggle.toggled.connect(self.advanced_model_container.setVisible)

        storage_group = QGroupBox("工作区与存储")
        storage_group.setStyleSheet(group_style)
        storage_layout = QFormLayout(storage_group)
        storage_layout.setSpacing(12)

        self.default_ws_input = QLineEdit()
        self.default_ws_input.setPlaceholderText("未设置")
        self.default_ws_input.setText(self.config_manager.get("default_workspace", ""))
        default_ws_container = QWidget()
        default_ws_layout = QHBoxLayout(default_ws_container)
        default_ws_layout.setContentsMargins(0, 0, 0, 0)
        default_ws_layout.setSpacing(8)
        default_ws_layout.addWidget(self.default_ws_input, 1)
        default_ws_btn = QPushButton("选择")
        default_ws_btn.setObjectName("SecondaryBtn")
        default_ws_btn.setFixedWidth(88)
        default_ws_layout.addWidget(default_ws_btn)
        storage_layout.addRow("默认工作区", default_ws_container)

        ws_desc = QLabel("默认工作区决定你首次打开应用时的任务范围。")
        ws_desc.setWordWrap(True)
        ws_desc.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        storage_layout.addRow("", ws_desc)

        def choose_default_workspace():
            directory = QFileDialog.getExistingDirectory(self, "选择默认工作区")
            if directory:
                self.default_ws_input.setText(directory)

        default_ws_btn.clicked.connect(choose_default_workspace)

        self.history_dir_input = QLineEdit()
        self.history_dir_input.setText(self.config_manager.get_chat_history_dir())
        history_dir_container = QWidget()
        history_dir_layout = QHBoxLayout(history_dir_container)
        history_dir_layout.setContentsMargins(0, 0, 0, 0)
        history_dir_layout.setSpacing(8)
        history_dir_layout.addWidget(self.history_dir_input, 1)
        history_dir_btn = QPushButton("选择")
        history_dir_btn.setObjectName("SecondaryBtn")
        history_dir_btn.setFixedWidth(88)
        history_dir_layout.addWidget(history_dir_btn)
        storage_layout.addRow("聊天记录目录", history_dir_container)

        history_desc = QLabel("聊天历史与长期记忆会保存在这个目录中。")
        history_desc.setWordWrap(True)
        history_desc.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        storage_layout.addRow("", history_desc)

        def choose_history_dir():
            directory = QFileDialog.getExistingDirectory(self, "选择聊天记录目录")
            if directory:
                self.history_dir_input.setText(directory)

        history_dir_btn.clicked.connect(choose_history_dir)

        advanced_group = QGroupBox("高级权限与企业消息")
        advanced_group.setStyleSheet(group_style)
        advanced_layout = QVBoxLayout(advanced_group)
        advanced_layout.setSpacing(14)

        permission_panel = QFrame()
        permission_panel.setStyleSheet(
            f"QFrame {{ background: {DesignTokens.warning_panel_bg}; border: 1px solid {DesignTokens.warning_panel_border}; border-radius: 14px; }}"
        )
        permission_layout = QVBoxLayout(permission_panel)
        permission_layout.setContentsMargins(14, 14, 14, 14)
        permission_title = QLabel("扩展权限模式")
        permission_title.setStyleSheet(f"font-weight: 700; color: {DesignTokens.warning_panel_text};")
        permission_desc = QLabel("开启后，助手可以突破工作区限制并执行更高风险的代码操作。仅在完全信任任务时使用。")
        permission_desc.setWordWrap(True)
        permission_desc.setStyleSheet(f"color: {DesignTokens.warning_panel_text}; font-size: 12px;")
        self.god_mode_check = QCheckBox("允许高风险操作")
        self.god_mode_check.setChecked(self.config_manager.get_god_mode())
        permission_layout.addWidget(permission_title)
        permission_layout.addWidget(permission_desc)
        permission_layout.addWidget(self.god_mode_check)
        advanced_layout.addWidget(permission_panel)

        base_layout.addWidget(model_group)
        base_layout.addWidget(storage_group)
        base_layout.addWidget(advanced_group)
        base_layout.addStretch()
        self.tabs.addTab(base_tab, "基础设置")

        im_tab = QWidget()
        im_layout = QVBoxLayout(im_tab)
        im_layout.setContentsMargins(12, 16, 12, 16)
        im_layout.setSpacing(18)

        im_header = QLabel("企业消息")
        im_header.setProperty("roleTitle", True)
        im_intro = QLabel("将助手接入飞书后，你可以直接在企业消息中下发任务，并复用同一套工作区约束。")
        im_intro.setProperty("roleSubtitle", True)
        im_intro.setWordWrap(True)
        im_layout.addWidget(im_header)
        im_layout.addWidget(im_intro)

        im_group = QGroupBox("飞书接入")
        im_group.setStyleSheet(group_style)
        im_form = QFormLayout(im_group)
        im_form.setSpacing(12)
        self.feishu_app_id_input = QLineEdit()
        self.feishu_app_id_input.setText(self.config_manager.get("feishu_app_id", ""))
        im_form.addRow("App ID", self.feishu_app_id_input)

        self.feishu_app_secret_input = QLineEdit()
        self.feishu_app_secret_input.setEchoMode(QLineEdit.Password)
        self.feishu_app_secret_input.setText(self.config_manager.get("feishu_app_secret", ""))
        im_form.addRow("App Secret", self.feishu_app_secret_input)
        im_layout.addWidget(im_group)

        gateway_bar = QHBoxLayout()
        gateway_info = QLabel("采用长连接模式，无需单独配置 Webhook。\n监听地址：0.0.0.0:8001")
        gateway_info.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        gateway_btn = QPushButton("启动企业消息网关")
        gateway_btn.setObjectName("PrimaryBtn")
        gateway_btn.setIcon(qta.icon('fa5s.play', color='white'))

        def start_gateway():
            try:
                self.config_manager.set("feishu_app_id", self.feishu_app_id_input.text().strip())
                self.config_manager.set("feishu_app_secret", self.feishu_app_secret_input.text().strip())
                if hasattr(self._main, "try_connect_daemon"):
                    self._main.try_connect_daemon(allow_start=True, retries=6)
                if hasattr(self._main, "start_gateway_process"):
                    self._main.start_gateway_process()
                QMessageBox.information(self, "企业消息网关", "已启动企业消息网关，飞书长连接接入已可使用。")
            except Exception:
                QMessageBox.warning(self, "企业消息网关", "启动失败，请检查环境与依赖是否完整。")

        gateway_btn.clicked.connect(start_gateway)
        gateway_bar.addWidget(gateway_info, 1)
        gateway_bar.addWidget(gateway_btn)
        im_layout.addLayout(gateway_bar)
        im_layout.addStretch()
        self.tabs.addTab(im_tab, "企业消息")

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("SecondaryBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def save_settings(self):
        self.config_manager.set("llm_provider", self.provider_combo.currentData())
        self.config_manager.set("api_key", self.api_key_input.text().strip())
        base_url = self.base_url_input.text().strip() or DEFAULT_DEEPSEEK_BASE_URL
        self.config_manager.set("base_url", base_url)
        model_name = self.model_name_input.text().strip() or DEFAULT_DEEPSEEK_MODEL
        self.config_manager.set("model_name", model_name)
        self.config_manager.set("deepseek_thinking_enabled", self.deepseek_thinking_check.isChecked())
        self.config_manager.set(
            "deepseek_reasoning_effort",
            self.deepseek_reasoning_effort_combo.currentData() or DEFAULT_DEEPSEEK_REASONING_EFFORT,
        )
        self.config_manager.set("default_workspace", self.default_ws_input.text().strip())
        self.config_manager.set_chat_history_dir(self.history_dir_input.text().strip())
        if self.god_mode_check.isChecked() and not self.config_manager.get_god_mode():
            reply = QMessageBox.question(
                self,
                "确认开启扩展权限",
                "扩展权限模式会突破工作区限制，并允许更高风险的执行操作。确定要开启吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.god_mode_check.setChecked(False)
        self.config_manager.set_god_mode(self.god_mode_check.isChecked())
        self.config_manager.set("feishu_app_id", self.feishu_app_id_input.text().strip())
        self.config_manager.set("feishu_app_secret", self.feishu_app_secret_input.text().strip())
        self.accept()


class SkillsCenterDialog(QDialog):
    def __init__(self, skill_manager, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("能力中心")
        self.resize(820, 620)
        self.skill_manager = skill_manager
        self.config_manager = config_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("能力中心")
        title.setProperty("roleTitle", True)
        subtitle = QLabel("查看助手当前可用的能力、适用场景与风险级别，并按需启用或关闭。")
        subtitle.setProperty("roleSubtitle", True)
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        self.import_btn = QPushButton("导入自定义能力")
        self.import_btn.setObjectName("SecondaryBtn")
        self.import_btn.setIcon(qta.icon('fa5s.box-open', color='#334155'))
        self.import_btn.clicked.connect(self.import_skill)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("SecondaryBtn")
        self.refresh_btn.setIcon(qta.icon('fa5s.sync', color='#334155'))
        self.refresh_btn.clicked.connect(self.manual_refresh)
        header.addWidget(self.import_btn)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tab_standard = QWidget()
        self.layout_standard = QVBoxLayout(self.tab_standard)
        self.layout_standard.setContentsMargins(0, 0, 0, 0)
        self.scroll_standard = QScrollArea()
        self.scroll_standard.setWidgetResizable(True)
        self.content_standard = QWidget()
        self.layout_content_standard = QVBoxLayout(self.content_standard)
        self.layout_content_standard.setContentsMargins(0, 0, 0, 0)
        self.layout_content_standard.setSpacing(14)
        self.layout_content_standard.addStretch()
        self.scroll_standard.setWidget(self.content_standard)
        self.layout_standard.addWidget(self.scroll_standard)
        self.tabs.addTab(self.tab_standard, "内置能力")

        self.tab_ai = QWidget()
        self.layout_ai = QVBoxLayout(self.tab_ai)
        self.layout_ai.setContentsMargins(0, 0, 0, 0)
        self.scroll_ai = QScrollArea()
        self.scroll_ai.setWidgetResizable(True)
        self.content_ai = QWidget()
        self.layout_content_ai = QVBoxLayout(self.content_ai)
        self.layout_content_ai.setContentsMargins(0, 0, 0, 0)
        self.layout_content_ai.setSpacing(14)
        self.layout_content_ai.addStretch()
        self.scroll_ai.setWidget(self.content_ai)
        self.layout_ai.addWidget(self.scroll_ai)
        self.tabs.addTab(self.tab_ai, "自定义能力")

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("PrimaryBtn")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self.refresh_list()

    def manual_refresh(self):
        self.skill_manager.load_skills()
        self.refresh_list()
        QMessageBox.information(self, "能力中心", "已重新扫描并加载全部能力。")

    def refresh_list(self):
        self._clear_layout(self.layout_content_standard)
        self._clear_layout(self.layout_content_ai)
        skills = self.skill_manager.get_all_skills()
        for skill in skills:
            is_ai = skill.get("type") == "ai_generated" or skill.get("created_by") == "ai"
            if is_ai:
                self.add_skill_card(skill, self.layout_content_ai)
            else:
                self.add_skill_card(skill, self.layout_content_standard)

    def _clear_layout(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_skill_card(self, skill, parent_layout):
        card = QFrame()
        card.setObjectName("SkillCard")
        card.setStyleSheet(
            f"QFrame#SkillCard {{ background: {DesignTokens.bg_card}; border: 1px solid {DesignTokens.border}; border-radius: 18px; }}"
        )
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(16)

        info_col = QVBoxLayout()
        info_col.setSpacing(8)
        title = QLabel(skill.get("display_name") or skill.get("name", ""))
        title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {DesignTokens.text_primary};")
        desc = QLabel(skill.get("user_description") or skill.get("description") or "暂无说明。")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 13px; color: {DesignTokens.text_secondary};")
        info_col.addWidget(title)
        info_col.addWidget(desc)

        use_cases = skill.get("use_cases") or []
        if use_cases:
            use_text = "、".join([str(item) for item in use_cases[:3]])
            use_label = QLabel(f"适用场景：{use_text}")
            use_label.setStyleSheet(f"font-size: 12px; color: {DesignTokens.text_secondary};")
            info_col.addWidget(use_label)

        risk_text, risk_color = readable_risk_level(skill.get("risk_level") or skill.get("security_level"))
        risk_label = QLabel(f"风险级别：{risk_text}")
        risk_label.setStyleSheet(f"font-size: 12px; color: {risk_color}; font-weight: 600;")
        info_col.addWidget(risk_label)

        tools = skill.get("tools") or []
        if tools:
            tool_label = QLabel("关联能力：" + "、".join([str(item) for item in tools[:4]]))
            tool_label.setStyleSheet(f"font-size: 12px; color: {DesignTokens.text_tertiary};")
            info_col.addWidget(tool_label)

        card_layout.addLayout(info_col, 1)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(10)
        controls_col.setAlignment(Qt.AlignRight | Qt.AlignTop)
        enabled = bool(skill.get("enabled"))
        status_chip = QLabel("已启用" if enabled else "已关闭")
        status_chip.setAlignment(Qt.AlignCenter)
        status_bg = DesignTokens.success_bg if enabled else DesignTokens.bg_secondary
        status_fg = DesignTokens.success_text if enabled else DesignTokens.text_secondary
        status_chip.setStyleSheet(
            f"background: {status_bg}; color: {status_fg}; border-radius: 12px; padding: 6px 12px; font-weight: 600;"
        )
        controls_col.addWidget(status_chip)

        toggle_btn = QPushButton("关闭" if enabled else "启用")
        toggle_btn.setObjectName("SecondaryBtn")
        toggle_btn.setFixedWidth(90)
        toggle_btn.clicked.connect(lambda checked=False, n=skill["name"], e=not enabled: self.toggle_skill(n, e))
        controls_col.addWidget(toggle_btn)

        if str(skill.get("risk_level") or skill.get("security_level") or "").lower() == "high":
            explain_btn = QPushButton("查看风险")
            explain_btn.setObjectName("GhostBtn")
            explain_btn.clicked.connect(
                lambda checked=False, s=skill: QMessageBox.information(
                    self,
                    "风险说明",
                    f"{s.get('display_name') or s.get('name')} 可能会访问或修改工作区中的重要内容，请在确认用途后再启用。",
                )
            )
            controls_col.addWidget(explain_btn)

        controls_col.addStretch()
        card_layout.addLayout(controls_col)
        parent_layout.insertWidget(parent_layout.count() - 1, card)

    def toggle_skill(self, name, enabled):
        self.config_manager.set_skill_enabled(name, enabled)
        self.refresh_list()

    def import_skill(self):
        path = QFileDialog.getExistingDirectory(self, "选择能力目录（包含 SKILL.md）")
        if path:
            success, msg = self.skill_manager.import_skill(path)
            if success:
                QMessageBox.information(self, "能力中心", msg)
                self.refresh_list()
            else:
                QMessageBox.warning(self, "能力中心", msg)


class DragOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {DesignTokens.bg_main};
                border: 3px dashed {DesignTokens.primary};
                border-radius: 24px;
            }}
        """)
        container.setFixedSize(400, 300)
        
        # Shadow for the container
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 10)
        container.setGraphicsEffect(shadow)
        
        v_layout = QVBoxLayout(container)
        v_layout.setAlignment(Qt.AlignCenter)
        v_layout.setSpacing(20)
        
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.folder-open', color=DesignTokens.primary).pixmap(80, 80))
        icon.setAlignment(Qt.AlignCenter)
        
        label = QLabel("松开鼠标以切换工作区")
        label.setStyleSheet(f"color: {DesignTokens.primary}; font-size: 20px; font-weight: bold;")
        label.setAlignment(Qt.AlignCenter)
        
        sub_label = QLabel("或者拖入文件以添加到输入框")
        sub_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 14px;")
        sub_label.setAlignment(Qt.AlignCenter)
        
        v_layout.addWidget(icon)
        v_layout.addWidget(label)
        v_layout.addWidget(sub_label)
        
        layout.addWidget(container)
        
        # Semi-transparent background
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.4);")

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

        selected = self.selectedText() or ""
        selected_len = len(selected)
        status = QAction(f"已选中 {selected_len} 字符", self)
        status.setEnabled(False)
        menu.addAction(status)
        menu.addSeparator()

        action_copy = QAction("复制选中内容", self)
        action_copy.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        action_copy.setShortcut("Ctrl+C")
        action_copy.setShortcutVisibleInContextMenu(True)
        action_copy.triggered.connect(lambda: QApplication.clipboard().setText(selected))
        action_copy.setEnabled(self.hasSelectedText())
        menu.addAction(action_copy)

        action_copy_all = QAction("复制全部内容", self)
        action_copy_all.setIcon(qta.icon('fa5s.clone', color='#4b5563'))
        action_copy_all.triggered.connect(lambda: QApplication.clipboard().setText(self.text() or ""))
        action_copy_all.setEnabled(bool((self.text() or "").strip()))
        menu.addAction(action_copy_all)

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

        selected = self.textCursor().selectedText()
        selected_len = len(selected or "")
        status = QAction(f"已选中 {selected_len} 字符", self)
        status.setEnabled(False)
        menu.addAction(status)
        menu.addSeparator()

        action_copy = QAction("复制选中内容", self)
        action_copy.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        action_copy.setShortcut("Ctrl+C")
        action_copy.setShortcutVisibleInContextMenu(True)
        action_copy.triggered.connect(self.copy)
        action_copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_copy)

        action_copy_all = QAction("复制全部内容", self)
        action_copy_all.setIcon(qta.icon('fa5s.clone', color='#4b5563'))
        action_copy_all.triggered.connect(lambda: QApplication.clipboard().setText(self.toPlainText() or ""))
        action_copy_all.setEnabled(bool((self.toPlainText() or "").strip()))
        menu.addAction(action_copy_all)

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
        self._height_adjust_pending = False
        self.textChanged.connect(self.scheduleAdjustHeight)
        self.setStyleSheet("background: transparent;")
        
        # Set word wrap mode to break anywhere if needed (for long strings)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

    def scheduleAdjustHeight(self):
        if self._height_adjust_pending:
            return
        self._height_adjust_pending = True
        QTimer.singleShot(0, self.adjustHeight)

    def adjustHeight(self):
        """
        根据文档内容调整文本框高度
        添加最大高度限制防止初始渲染时高度异常
        """
        self._height_adjust_pending = False
        doc_height = self.document().size().height()
        margins = self.contentsMargins()
        height = int(doc_height + margins.top() + margins.bottom())
        # 确保最小高度避免不可见，同时限制最大高度防止初始异常
        height = max(height, 24)
        height = min(height, 2000)  # 限制最大高度为2000像素
        if self.height() != height:
            self.setFixedHeight(height)

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
        self.setFixedHeight(40) # Initial height
        self.min_height = 40
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
                event.accept()
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

class DaemonRequestWorker(QThread):
    finished_signal = Signal(object, str)

    def __init__(self, client, session_id, content, workspace_dir=None, run_context=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.session_id = session_id
        self.content = content
        self.workspace_dir = workspace_dir
        self.run_context = run_context or {}
        self._aborted = False
        self._sock = None

    def abort(self):
        self._aborted = True

    def run(self):
        try:
            resp = self.client.send_message(
                self.session_id,
                self.content,
                self.workspace_dir,
                run_context=self.run_context,
            )
        except Exception:
            resp = None
        if self._aborted:
            return
        if not resp or resp.get("status") != "ok":
            error_text = resp.get("error") if isinstance(resp, dict) else "Daemon offline"
            result = {"error": error_text}
        else:
            result = resp.get("result") or {"error": "No response"}
        if self._aborted:
            return
        self.finished_signal.emit(result, self.session_id)


class DaemonStreamWorker(QThread):
    finished_signal = Signal(object, str)
    thinking_signal = Signal(str)
    content_signal = Signal(str)
    tool_call_signal = Signal(dict)
    tool_result_signal = Signal(dict)
    observability_signal = Signal(dict)
    agent_state_signal = Signal(dict)
    output_signal = Signal(str)
    interaction_signal = Signal(dict)

    def __init__(self, client, session_id, content, workspace_dir=None, run_context=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.session_id = session_id
        self.content = content
        self.workspace_dir = workspace_dir
        self.run_context = run_context or {}
        self._aborted = False
        self._sock = None

    def _abort_remote_session(self):
        try:
            self.client.stop_session(self.session_id)
        except Exception:
            pass

    def abort(self):
        self._aborted = True
        self._abort_remote_session()
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass

    def run(self):
        try:
            with socket.create_connection((self.client.host, self.client.port), timeout=self.client.send_timeout) as sock:
                self._sock = sock
                payload = {
                    "action": "send_message_stream",
                    "session_id": self.session_id,
                    "content": self.content,
                    "workspace_dir": self.workspace_dir,
                    "run_context": self.run_context,
                }
                sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                reader = sock.makefile("r", encoding="utf-8")
                for line in reader:
                    if self._aborted:
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("type") == "thinking":
                        self.thinking_signal.emit(msg.get("delta", ""))
                    elif msg.get("type") == "content":
                        self.content_signal.emit(msg.get("delta", ""))
                    elif msg.get("type") == "tool_call":
                        data = msg.get("data") or {}
                        self.tool_call_signal.emit(data)
                    elif msg.get("type") == "tool_result":
                        data = msg.get("data") or {}
                        self.tool_result_signal.emit(data)
                    elif msg.get("type") == "observability":
                        data = msg.get("data") or {}
                        if isinstance(data, dict):
                            self.observability_signal.emit(data)
                    elif msg.get("type") == "agent_state":
                        data = msg.get("data") or {}
                        if isinstance(data, dict):
                            self.agent_state_signal.emit(data)
                    elif msg.get("type") == "interaction_request":
                        data = msg.get("data") or {}
                        if isinstance(data, dict) and data.get("request_id"):
                            self.interaction_signal.emit(data)
                    elif msg.get("type") == "final":
                        result = msg.get("result") or {"error": "No response"}
                        if isinstance(result, dict):
                            result["_streamed"] = True
                        self.finished_signal.emit(result, self.session_id)
                        return
                    elif msg.get("type") == "error":
                        result = {"error": msg.get("error") or "Daemon error", "_streamed": True}
                        self.finished_signal.emit(result, self.session_id)
                        return
                    elif msg.get("status") == "error":
                        result = {"error": msg.get("error") or "Daemon error", "_streamed": True}
                        self.finished_signal.emit(result, self.session_id)
                        return
                    elif msg.get("status") == "ok" and "result" in msg:
                        result = msg.get("result") or {"error": "No response"}
                        if isinstance(result, dict):
                            result["_streamed"] = True
                        self.finished_signal.emit(result, self.session_id)
                        return
                if not self._aborted:
                    self.finished_signal.emit({"error": "Daemon stream closed", "_streamed": True}, self.session_id)
        except Exception as e:
            if not self._aborted:
                text = str(e)
                if "timed out" in text.lower() or "timeout" in text.lower():
                    self._abort_remote_session()
                    text = "Confirmation timed out. Conversation interrupted."
                self.finished_signal.emit({"error": text, "_streamed": True}, self.session_id)
        finally:
            self._sock = None

class HistoryTitleButton(QPushButton):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._full_title = title or ""
        self._display_title = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(self._full_title)
        self._apply_elide()

    def set_full_title(self, title):
        self._full_title = title or ""
        self.setToolTip(self._full_title)
        self._apply_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        metrics = QFontMetrics(self.font())
        available = max(24, self.width() - 28)
        display = metrics.elidedText(self._full_title, Qt.ElideRight, available)
        if display != self._display_title:
            self._display_title = display
            super().setText(display)

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
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(24) # Increase spacing
        
        self.actions_data = [
            ("📁 整理文件", "按类型自动分类", "帮我把当前目录下的文件按类型分类整理"),
            ("🖼️ 处理图片", "批量重命名/压缩", "帮我把所有图片重命名为日期格式"),
            ("🔍 代码搜索", "在项目中查找内容", "搜索当前项目中关于 'TODO' 的代码"),
            ("📊 生成报告", "分析目录结构", "分析当前目录结构并生成一份报告")
        ]
        
        self.action_cards = []
        for text, desc, prompt in self.actions_data:
            btn = self.create_action_card(text, desc, prompt)
            self.action_cards.append(btn)
            
        layout.addStretch()
        layout.addWidget(icon)
        layout.addSpacing(24)
        layout.addWidget(title)
        layout.addSpacing(40)
        layout.addWidget(self.grid_widget)
        layout.addStretch()
        
        # Initial layout
        self.reflow_cards()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow_cards()
        
    def reflow_cards(self):
        # Calculate columns based on width
        # Card min width ~260, spacing 24
        w = self.width()
        if w > 1100:
            cols = 4
        elif w > 600:
            cols = 2
        else:
            cols = 1
            
        # Prevent infinite resize loops by only reflowing when column count changes
        if hasattr(self, 'current_cols') and self.current_cols == cols:
            return
            
        self.current_cols = cols
        
        # Clear grid but keep widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            # Don't delete widget, just remove from layout
            if item.widget():
                item.widget().setParent(None)
            
        # Re-add to grid
        for i, btn in enumerate(self.action_cards):
            self.grid_layout.addWidget(btn, i // cols, i % cols)
            
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
            # 不添加addStretch，让容器高度只由Avatar决定，避免气泡被撑高
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
            self.think_toggle_btn.setIcon(qta.icon('fa5s.lightbulb', color=DesignTokens.accent_tool))
            self.think_toggle_btn.setCursor(Qt.PointingHandCursor)
            self.think_toggle_btn.setCheckable(True)
            self.think_toggle_btn.setChecked(False)
            self.think_toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    background-color: {DesignTokens.bg_main};
                    color: {DesignTokens.text_secondary};
                    border: 1px solid {DesignTokens.border};
                    border-radius: 12px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: 500;
                    margin-bottom: 0px; /* Reduced to connect with container */
                }}
                QPushButton:hover {{ background-color: {DesignTokens.bg_secondary}; color: {DesignTokens.text_primary}; border-color: {DesignTokens.border}; }}
                QPushButton:checked {{ 
                    background-color: {DesignTokens.bg_secondary}; 
                    color: {DesignTokens.text_primary}; 
                    border-color: {DesignTokens.border}; 
                    border-bottom-left-radius: 0; 
                    border-bottom-right-radius: 0; 
                }}
            """)
            self.think_toggle_btn.toggled.connect(self.toggle_thinking)
            think_layout.addWidget(self.think_toggle_btn)

            # Container for Thinking Stream
            self.think_container = QWidget()
            self.think_container.setVisible(False)
            self.think_container.setStyleSheet(f"""
                QWidget {{
                    background: {DesignTokens.bg_secondary};
                    border: 1px solid {DesignTokens.border};
                    border-left: 3px solid {DesignTokens.accent_ai};
                    border-top: none;
                    margin-top: -1px;
                    margin-left: 0px;
                    border-bottom-left-radius: 12px;
                    border-bottom-right-radius: 12px;
                }}
            """)
            self.think_container_layout = QVBoxLayout(self.think_container)
            self.think_container_layout.setContentsMargins(12, 12, 12, 12)
            self.think_container_layout.setSpacing(8)
            self.think_duration = 0.0 # Store duration
            self.think_start_time = None
            self.think_timer = QTimer(self)
            self.think_timer.setInterval(100) # Update every 100ms
            self.think_timer.timeout.connect(self._on_think_tick)
            
            self._start_new_think_segment = False
            self._last_thinking_segment_text = ""
            self._strip_prefix = ""
            
            think_layout.addWidget(self.think_container)
            col_layout.addWidget(self.thinking_widget)
            
            # 2. Main Content
            self.content_edit = AutoResizingTextEdit()
            self.content_edit.setStyleSheet("background: transparent; border: none; padding: 0;")
            col_layout.addWidget(self.content_edit)
            self.main_content_text = ""
            self._pending_main_content_text = ""
            self._pending_main_content_parts = None
            self._pending_main_content_final = False
            self._rendered_main_content_text = None
            self._rendered_main_content_final = False
            self._rendered_main_content_mode = None
            self._last_main_content_render_ts = 0.0
            self._main_content_render_interval = STREAM_RENDER_INTERVAL_SEC
            self._main_content_render_timer = QTimer(self)
            self._main_content_render_timer.setSingleShot(True)
            self._main_content_render_timer.timeout.connect(self._flush_pending_main_content_render)
            self.copy_result_btn = QPushButton("复制结果")
            self.copy_result_btn.setCursor(Qt.PointingHandCursor)
            self.copy_result_btn.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
            self.copy_result_btn.setVisible(False)
            self.copy_result_btn.setFixedHeight(26)
            self.copy_result_btn.setStyleSheet(f"""
                QPushButton {{
                    border: 1px solid {DesignTokens.border};
                    border-radius: 6px;
                    padding: 2px 10px;
                    background: {DesignTokens.bg_secondary};
                    color: {DesignTokens.text_secondary};
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    color: {DesignTokens.primary};
                    border-color: {DesignTokens.primary};
                    background: {DesignTokens.bg_main};
                }}
            """)
            self.copy_result_btn.clicked.connect(self.copy_main_content)
            copy_row = QHBoxLayout()
            copy_row.setContentsMargins(0, 0, 0, 0)
            copy_row.addWidget(self.copy_result_btn, 0, Qt.AlignLeft)
            copy_row.addStretch()
            col_layout.addLayout(copy_row)
            
            # 3. Sub-Agent Indicators
            self.sub_agent_indicators = QWidget()
            self.sub_agent_indicators.setVisible(False)
            self.sub_agent_indicators_layout = QHBoxLayout(self.sub_agent_indicators)
            self.sub_agent_indicators_layout.setContentsMargins(0, 0, 0, 0)
            self.sub_agent_indicators_layout.setSpacing(8)
            self.sub_agent_indicators_layout.setAlignment(Qt.AlignLeft)
            col_layout.addWidget(self.sub_agent_indicators)
            
            # 4. Sub-Agent Logs (Hidden Drawer)
            self.sub_agent_logs = QStackedWidget()
            self.sub_agent_logs.setVisible(False)
            self.sub_agent_logs.setStyleSheet(f"""
                QStackedWidget {{
                    background: {DesignTokens.bg_main};
                    border: 1px solid {DesignTokens.border};
                    border-radius: 8px;
                }}
            """)
            col_layout.addWidget(self.sub_agent_logs)
            
            self.active_agent_logs = {} # agent_id -> QTextEdit
            
            # Handle Initial State
            if thinking == "...":
                self.set_thinking_state(True)
            elif thinking:
                self.update_thinking(thinking, duration, is_final=True)
            else:
                self.thinking_widget.setVisible(False)
                
            if text:
                self.set_main_content(text, final=True)
                
            main_layout.addWidget(content_col)
            # main_layout.addStretch() # Removed to allow content to take full width

    def _on_think_tick(self):
        if self.think_start_time is None: return
        
        elapsed = time.time() - self.think_start_time
        current_total = self.think_duration + elapsed
        
        self.think_toggle_btn.setText(f" 深度思考 ({current_total:.1f}s)")

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
        base_text = " 深度思考"
        
        # Use stored duration if available
        if hasattr(self, 'think_duration') and self.think_duration > 0:
             base_text = f" 深度思考 ({self.think_duration:.1f}s)"
        elif "(" in text:
             # Fallback to parsing if duration not stored yet (legacy bubbles)
             try:
                 parts = text.split("(")
                 duration_part = "(" + parts[1]
                 base_text = f" 深度思考 {duration_part}"
             except: pass
             
        if checked:
             self.think_toggle_btn.setText(base_text) # Maybe add arrow if needed, but styling shows state
        else:
             self.think_toggle_btn.setText(base_text)

    # --- Sub-Agent PiP Methods ---
    def add_sub_agent_indicator(self, agent_id, status="pending"):
        if not hasattr(self, 'agent_indicators'):
            self.agent_indicators = {}
            
        if agent_id in self.agent_indicators:
            # Already exists, just update status
            self.update_sub_agent_status_icon(agent_id, status)
            return

        # Create Indicator
        indicator = QPushButton()
        indicator.setFixedSize(24, 24)
        indicator.setCursor(Qt.PointingHandCursor)
        indicator.setToolTip(f"Agent: {agent_id} ({status})")
        
        # Style based on status
        color = self._get_status_color(status)
        set_stylesheet_if_changed(indicator, f"""
            QPushButton {{
                background-color: {DesignTokens.bg_secondary};
                border: 1px solid {color};
                border-radius: 12px;
                color: {color};
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {color};
                color: white;
            }}
        """)
        
        # Simple initial or dot
        initial = agent_id[0].upper() if agent_id else "?"
        indicator.setText(initial)
        
        indicator.clicked.connect(lambda: self._toggle_sub_agent_log(agent_id))
        
        self.sub_agent_indicators_layout.addWidget(indicator)
        
        if not self.sub_agent_indicators.isVisible():
            self.sub_agent_indicators.setVisible(True)
            
        # Create Log Viewer (Hidden)
        self._create_log_viewer(agent_id)
        
        self.agent_indicators[agent_id] = indicator

    def update_sub_agent_log(self, agent_id, content, status):
        # Ensure exists
        self.add_sub_agent_indicator(agent_id, status)
        
        # Update Log Content
        if agent_id in self.active_agent_logs:
            text_edit = self.active_agent_logs[agent_id]
            
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            
            fmt = QTextCharFormat()
            if status == "thinking":
                fmt.setForeground(QColor(DesignTokens.accent_ai))
                fmt.setFontItalic(True)
            elif status == "provider_log":
                fmt.setForeground(QColor("#0f766e"))
                fmt.setFontWeight(QFont.Bold)
            elif status == "provider_error":
                fmt.setForeground(QColor("#b91c1c"))
                fmt.setFontWeight(QFont.Bold)
            elif status == "tool_use":
                fmt.setForeground(QColor(DesignTokens.accent_tool))
            elif status == "completed":
                fmt.setForeground(QColor(DesignTokens.success_text))
            elif status == "error":
                fmt.setForeground(QColor(DesignTokens.error_text))
            else:
                fmt.setForeground(QColor(DesignTokens.text_primary))
                
            cursor.insertText(content, fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            
        # Update Status Icon
        self.update_sub_agent_status_icon(agent_id, status)

    def update_sub_agent_status_icon(self, agent_id, status):
        if hasattr(self, 'agent_indicators') and agent_id in self.agent_indicators:
            indicator = self.agent_indicators[agent_id]
            color = self._get_status_color(status)
            set_stylesheet_if_changed(indicator, f"""
                QPushButton {{
                    background-color: {DesignTokens.bg_secondary};
                    border: 1px solid {color};
                    border-radius: 12px;
                    color: {color};
                    font-size: 10px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    color: white;
                }}
            """)
            indicator.setToolTip(f"Agent: {agent_id} ({status})")

    def _get_status_color(self, status):
        if status in ["active", "running", "thinking", "waiting_input", "pending"]:
            return DesignTokens.accent_ai
        if status == "completed": return DesignTokens.success_text
        if status in ["error", "failed", "failed_recovered", "killed"]:
            return DesignTokens.error_text
        if status == "closed":
            return DesignTokens.text_secondary
        if status == "tool_use": return DesignTokens.accent_tool
        return DesignTokens.text_tertiary

    def _create_log_viewer(self, agent_id):
        text_edit = AutoResizingTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                border: none;
                padding: 8px;
                background: transparent;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                color: {DesignTokens.text_secondary};
            }}
        """)
        
        self.sub_agent_logs.addWidget(text_edit)
        self.active_agent_logs[agent_id] = text_edit

    def _toggle_sub_agent_log(self, agent_id):
        if agent_id not in self.active_agent_logs: return
        
        target_log = self.active_agent_logs[agent_id]
        
        if not self.sub_agent_logs.isVisible():
            self.sub_agent_logs.setCurrentWidget(target_log)
            self.sub_agent_logs.setVisible(True)
        else:
            if self.sub_agent_logs.currentWidget() == target_log:
                self.sub_agent_logs.setVisible(False)
            else:
                self.sub_agent_logs.setCurrentWidget(target_log)
                target_log.ensureCursorVisible() # Ensure scrolled to bottom
        
    def set_thinking_state(self, is_thinking):
        if is_thinking:
            if not self.think_timer.isActive():
                self.think_start_time = time.time()
                self.think_timer.start()
                
            self.think_toggle_btn.setText(f" 深度思考 ({self.think_duration:.1f}s)")
            self.think_toggle_btn.setChecked(True)
            self.thinking_widget.setVisible(True)
        else:
            if self.think_timer.isActive():
                self.think_timer.stop()
                if self.think_start_time:
                    self.think_duration += time.time() - self.think_start_time
                    self.think_start_time = None

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
        if text is not None or duration is not None:
            self.thinking_widget.setVisible(True)
        if text is not None:
            # Simple streaming append for now
            widget = self.get_active_think_widget()
            current = widget.text()
            widget.setText(current + text)
        
        if duration:
            self.think_duration = duration
        
        if is_final:
            if self.think_timer.isActive():
                self.think_timer.stop()
                self.think_start_time = None
                
            self.think_toggle_btn.setText(f" 深度思考 ({self.think_duration:.1f}s)")
            self.think_toggle_btn.setChecked(False) # Collapse by default when done

    def stop_thinking_timers(self):
        if self.think_timer.isActive():
            self.think_timer.stop()
            if self.think_start_time:
                self.think_duration += time.time() - self.think_start_time
                self.think_start_time = None
        timer = getattr(self, "_thinking_replay_timer", None)
        if timer and timer.isActive():
            timer.stop()
        self.think_toggle_btn.setText(f" 深度思考 ({self.think_duration:.1f}s)")
        self.think_toggle_btn.setChecked(False)
            
    def set_main_content(self, text, content_parts=None, final=False):
        """设置对话气泡的主要内容，并合并高频流式渲染。"""
        text = text or ""
        self.main_content_text = text
        self._pending_main_content_text = text
        self._pending_main_content_parts = content_parts
        self._pending_main_content_final = bool(final)
        self.copy_result_btn.setVisible(bool(text.strip()))

        already_rendered = (
            self._rendered_main_content_text == text
            and (self._rendered_main_content_final or not final)
        )
        if already_rendered:
            return

        if final:
            if self._main_content_render_timer.isActive():
                self._main_content_render_timer.stop()
            self._flush_pending_main_content_render()
            return

        now = time.time()
        elapsed = now - self._last_main_content_render_ts
        if elapsed >= self._main_content_render_interval:
            self._flush_pending_main_content_render()
            return

        if not self._main_content_render_timer.isActive():
            delay_ms = max(1, int((self._main_content_render_interval - elapsed) * 1000))
            self._main_content_render_timer.start(delay_ms)

    def _flush_pending_main_content_render(self):
        self._render_main_content(
            self._pending_main_content_text,
            content_parts=self._pending_main_content_parts,
            final=self._pending_main_content_final,
        )

    def _render_main_content(self, text, content_parts=None, final=False):
        text = text or ""
        already_rendered = (
            self._rendered_main_content_text == text
            and (self._rendered_main_content_final or not final)
        )
        if already_rendered:
            return

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
            self.content_edit.setUpdatesEnabled(False)
            if not final and len(text) >= STREAM_PLAIN_TEXT_THRESHOLD:
                self._render_plain_stream_content(text)
                render_mode = "plain"
            else:
                html_content = markdown.markdown(text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])
                self.content_edit.setHtml(style + html_content)
                render_mode = "html"
        except Exception:
            self.content_edit.setUpdatesEnabled(False)
            self.content_edit.setPlainText(text)
            render_mode = "plain"
        finally:
            self.content_edit.setUpdatesEnabled(True)

        self._rendered_main_content_text = text
        self._rendered_main_content_final = bool(final)
        self._rendered_main_content_mode = render_mode
        self._last_main_content_render_ts = time.time()
        self.content_edit.scheduleAdjustHeight()

    def _render_plain_stream_content(self, text):
        """Avoid full Markdown conversion while a long response is still streaming."""
        previous = self._rendered_main_content_text or ""
        if self._rendered_main_content_mode == "plain" and text.startswith(previous):
            delta = text[len(previous):]
            if delta:
                cursor = self.content_edit.textCursor()
                cursor.movePosition(QTextCursor.End)
                cursor.insertText(delta)
                self.content_edit.setTextCursor(cursor)
            return
        self.content_edit.setPlainText(text)

    def copy_main_content(self):
        text = (self.main_content_text or "").strip() or self.content_edit.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.copy_result_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_result_btn.setText("复制结果"))
        
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
    clicked = Signal(str, str, str, dict) # tool_id, args, result, meta

    def __init__(self, tool_name, args, tool_id, meta=None):
        super().__init__()
        self.tool_id = tool_id
        self.args = args
        self.result = ""
        self.result_obj = None
        self.is_finished = False
        self.meta = meta or {}
        self.tool_name = tool_name
        self.is_selected = False
        
        self.setFocusPolicy(Qt.StrongFocus)
        
        self.setFrameShape(QFrame.NoFrame)
        # Timeline "Step" Style
        self.setStyleSheet("""
            ToolCallCard {
                background-color: transparent;
                border: none;
                margin: 0;
                padding-left: 10px; /* Space for timeline line if we want to draw it externally, or just indent */
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # --- Main Row Container (The "Timeline Node") ---
        self.main_row = QFrame()
        self.main_row.setCursor(Qt.PointingHandCursor)
        self.main_row.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
                border-radius: 6px;
                padding: 4px;
            }}
            QFrame:hover {{
                background-color: {DesignTokens.bg_secondary};
            }}
        """)
        # Make the whole card clickable
        self.main_row.mousePressEvent = self.on_card_clicked
        
        row_layout = QHBoxLayout(self.main_row)
        row_layout.setContentsMargins(4, 4, 4, 4)
        row_layout.setSpacing(12)
        
        # 1. Icon Area (Timeline Dot)
        tool_icons = {
            "list_files": "fa5s.folder", "read_file": "fa5s.book-open", "write_file": "fa5s.pen-alt",
            "update_file": "fa5s.pen", "delete_file": "fa5s.trash-alt", "run_command": "fa5s.terminal",
            "bash": "fa5s.terminal",
            "open_preview": "fa5s.compass", "search_codebase": "fa5s.search", "grep": "fa5s.filter",
            "glob": "fa5s.globe", "web_search": "fa5s.globe-americas", "get_diagnostics": "fa5s.stethoscope",
        }
        icon_name = tool_icons.get(tool_name, "fa5s.tools")
        
        # Icon with base
        self.icon_label = QLabel()
        self.icon_label.setPixmap(qta.icon(icon_name, color=DesignTokens.accent_tool).pixmap(14, 14))
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet(f"""
            background-color: {DesignTokens.bg_secondary};
            border: 1px solid {DesignTokens.border};
            border-radius: 12px; 
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
        if len(short_args) > 80:
            short_args = short_args[:80] + "..."
        args_preview = QLabel(short_args)
        args_preview.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 11px; border: none; font-family: 'Consolas', monospace;")
        
        text_layout.addWidget(name_label)
        text_layout.addWidget(args_preview)
        
        # 3. Right Side Controls
        self.status_icon = QLabel() # Default running
        self.status_icon.setPixmap(qta.icon('fa5s.spinner', color=DesignTokens.text_secondary, animation=qta.Spin(self.status_icon)).pixmap(14, 14))
        self.status_icon.setStyleSheet("border: none; background: transparent;")
        
        self.view_btn = QPushButton("详情") # Minimalist text
        self.view_btn.setCursor(Qt.PointingHandCursor)
        self.view_btn.setFixedWidth(36)
        self.view_btn.setToolTip("查看详情")
        self.view_btn.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 4px;
                color: {DesignTokens.text_tertiary};
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {DesignTokens.primary};
                background: {DesignTokens.bg_secondary};
            }}
        """)
        self.view_btn.clicked.connect(lambda: self.clicked.emit(self.tool_id, str(self.args), str(self.result), self.meta))

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
        agent_name = state.get("agent_name") or ""
        status = state.get("status")
        task = state.get("task", "")
        
        if not agent_id: return
        
        if not self.sub_agents_container.isVisible():
            self.sub_agents_container.setVisible(True)
            
        if agent_id not in self.sub_agent_widgets:
            # Create new row for agent
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            head_layout = QHBoxLayout()
            head_layout.setContentsMargins(0, 0, 0, 0)
            head_layout.setSpacing(6)
            
            icon = QLabel()
            icon.setPixmap(qta.icon('fa5s.robot', color='#6b7280').pixmap(12, 12))
            
            display_name = agent_name or agent_id
            if agent_name and agent_name != agent_id:
                display_name = f"{agent_name} ({agent_id[:8]})"
            name = QLabel(display_name)
            name.setStyleSheet("font-weight: bold; color: #4b5563; font-size: 11px;")
            
            status_label = QLabel(status)
            status_label.setStyleSheet("color: #6b7280; font-size: 11px;")

            detail_label = QLabel("")
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet("color: #6b7280; font-size: 10px;")
            
            head_layout.addWidget(icon)
            head_layout.addWidget(name)
            head_layout.addWidget(status_label, 1) # Expand
            row_layout.addLayout(head_layout)
            row_layout.addWidget(detail_label)
            
            self.sub_agents_layout.addWidget(row_widget)
            self.sub_agent_widgets[agent_id] = {
                "widget": row_widget,
                "status_label": status_label,
                "name_label": name,
                "detail_label": detail_label,
                "detail_text": "",
            }
        
        # Update status
        widgets = self.sub_agent_widgets[agent_id]
        if agent_name:
            display_name = agent_name if agent_name == agent_id else f"{agent_name} ({agent_id[:8]})"
            widgets["name_label"].setText(display_name)
        status_text = f"{status}"
        detail_text = ""
        
        # Default style
        style = "color: #6b7280; font-size: 11px;"
        detail_style = "color: #6b7280; font-size: 10px;"
        detail_cache = widgets.get("detail_text", "")

        def _trim(text, limit=180):
            text = (text or "").replace("\r", " ").replace("\n", " ").strip()
            if len(text) <= limit:
                return text
            return text[: limit - 3] + "..."
        
        if status == "pending":
            status_text = f"Pending: {task[:30]}..." if task else "Pending"
            detail_text = _trim(task)
        elif status == "completed":
            status_text = "Completed"
            style = "color: #10b981; font-size: 11px; font-weight: bold;"
            detail_text = _trim(state.get("content") or state.get("last_result") or "")
        elif status in {"active", "running"}:
             status_text = "Running..."
             style = "color: #3b82f6; font-size: 11px;"
             detail_text = _trim(task or detail_cache)
        elif status == "waiting_input":
            status_text = "Waiting input..."
            style = "color: #0ea5e9; font-size: 11px;"
            detail_text = "等待新的 send_input..."
        elif status == "thinking":
            status_text = "Thinking..." 
            style = "color: #6366f1; font-size: 11px; font-style: italic;"
            chunk = _trim(state.get("reasoning_delta") or "")
            if chunk:
                detail_text = _trim((detail_cache + " " + chunk).strip(), limit=200)
                detail_style = "color: #6366f1; font-size: 10px; font-style: italic;"
            else:
                detail_text = detail_cache
        elif status == "content":
            status_text = "Writing..."
            style = "color: #16a34a; font-size: 11px;"
            chunk = _trim(state.get("content_delta") or "")
            if chunk:
                detail_text = _trim((detail_cache + " " + chunk).strip(), limit=220)
                detail_style = "color: #166534; font-size: 10px;"
            else:
                detail_text = detail_cache
        elif status == "provider_log":
            status_text = "Provider..."
            style = "color: #0f766e; font-size: 11px; font-weight: bold;"
            detail_text = _trim(state.get("provider_message") or "")
            detail_style = "color: #0f766e; font-size: 10px;"
        elif status == "provider_error":
            status_text = "Provider error"
            style = "color: #b91c1c; font-size: 11px; font-weight: bold;"
            detail_text = _trim(state.get("provider_message") or state.get("error") or "")
            detail_style = "color: #b91c1c; font-size: 10px;"
        elif status == "tool_use":
            # task contains "Tool: <name>"
            status_text = f"Action: {task}"
            style = "color: #f59e0b; font-size: 11px; font-weight: bold;"
            detail_text = _trim(task)
            detail_style = "color: #f59e0b; font-size: 10px;"
        elif status == "log":
            status_text = "Running..."
            style = "color: #3b82f6; font-size: 11px;"
            chunk = _trim(state.get("log_content") or "")
            if chunk:
                detail_text = _trim((detail_cache + " " + chunk).strip(), limit=200)
            else:
                detail_text = detail_cache
        elif status in {"failed", "failed_recovered"}:
            status_text = "Failed"
            style = "color: #ef4444; font-size: 11px; font-weight: bold;"
            detail_text = _trim(state.get("error") or state.get("content") or "")
            detail_style = "color: #ef4444; font-size: 10px;"
        elif status == "closed":
            status_text = "Closed"
            style = "color: #6b7280; font-size: 11px;"
            detail_text = _trim(state.get("error") or "已关闭")
        elif status == "killed":
            status_text = "Killed"
            style = "color: #dc2626; font-size: 11px; font-weight: bold;"
            detail_text = _trim(state.get("error") or "已强制终止")
            detail_style = "color: #dc2626; font-size: 10px;"
        
        set_text_if_changed(widgets["status_label"], status_text)
        set_stylesheet_if_changed(widgets["status_label"], style)
        widgets["detail_text"] = detail_text or detail_cache
        set_text_if_changed(widgets["detail_label"], widgets["detail_text"])
        set_stylesheet_if_changed(widgets["detail_label"], detail_style)

    def focusInEvent(self, event):
        if not self.is_selected:
            set_stylesheet_if_changed(self.main_row, f"""
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
        self.clicked.emit(self.tool_id, str(self.args), str(self.result), self.meta)

    def set_selected(self, selected):
        self.is_selected = selected
        if selected:
            # Selected: Blue Border + Light Blue BG
            set_stylesheet_if_changed(self.main_row, f"""
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
            set_stylesheet_if_changed(self.main_row, f"""
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

    def set_result(self, result_text, result_obj=None):
        self.status_icon.setPixmap(qta.icon('fa5s.check-circle', color=DesignTokens.success_accent).pixmap(14, 14))
        self.result = result_text
        self.result_obj = result_obj
        self.is_finished = True
        
        # Update style to show success (Green left border)
        if not self.is_selected:
            set_stylesheet_if_changed(self.main_row, f"""
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

    def reset(self):
        self.tabs.clear()
        self.agents = {}

    def update_log(self, agent_id, content, status, agent_name=""):
        if agent_id not in self.agents:
            self._create_agent_tab(agent_id, agent_name=agent_name)
        elif agent_name:
            self._set_agent_tab_title(agent_id, agent_name)
            
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
        elif status == "content" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)

            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#166534"))
            fmt.setFontItalic(False)
            fmt.setFontWeight(QFont.Normal)
            fmt.setFontPointSize(11)

            cursor.insertText(content, fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()

            self._update_tab_status(agent_id, "running")
            
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
        elif status == "provider_log" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            if not text_edit.toPlainText().endswith("\n") and len(text_edit.toPlainText()) > 0:
                cursor.insertText("\n")

            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#0f766e"))
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontPointSize(10)

            cursor.insertText(f"[{ts}] {content}\n", fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
        elif status == "provider_error" and content:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            if not text_edit.toPlainText().endswith("\n") and len(text_edit.toPlainText()) > 0:
                cursor.insertText("\n")

            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#b91c1c"))
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontPointSize(10)

            cursor.insertText(f"[{ts}] {content}\n", fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            self._update_tab_status(agent_id, "failed")
            
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
            
        elif status in {"active", "running"}:
            self._update_tab_status(agent_id, "running")

        elif status == "waiting_input":
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#0ea5e9"))
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontPointSize(11)
            cursor.insertText(f"\n[{ts}] ⏸ Waiting input\n", fmt)
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
            self._update_tab_status(agent_id, "running")

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
             final_text = (content or "").strip()
             if final_text:
                 body_fmt = QTextCharFormat()
                 body_fmt.setForeground(QColor("#166534"))
                 body_fmt.setFontPointSize(11)
                 cursor.insertText(final_text + "\n", body_fmt)
             text_edit.setTextCursor(cursor)
             text_edit.ensureCursorVisible()
             
             self._update_tab_status(agent_id, "completed")
        elif status in {"failed", "failed_recovered", "killed"}:
             cursor = text_edit.textCursor()
             cursor.movePosition(QTextCursor.End)
             fmt = QTextCharFormat()
             fmt.setForeground(QColor("#ef4444"))
             fmt.setFontWeight(QFont.Bold)
             fmt.setFontPointSize(11)
             detail = (content or "").strip()
             if detail:
                 cursor.insertText(f"\n❌ Failed at {ts}: {detail}\n", fmt)
             else:
                 cursor.insertText(f"\n❌ Failed at {ts}\n", fmt)
             text_edit.setTextCursor(cursor)
             text_edit.ensureCursorVisible()
             self._update_tab_status(agent_id, "failed")
        elif status == "closed":
             cursor = text_edit.textCursor()
             cursor.movePosition(QTextCursor.End)
             fmt = QTextCharFormat()
             fmt.setForeground(QColor("#6b7280"))
             fmt.setFontPointSize(11)
             cursor.insertText(f"\n■ Closed at {ts}\n", fmt)
             text_edit.setTextCursor(cursor)
             text_edit.ensureCursorVisible()
             self._update_tab_status(agent_id, "closed")

    def _update_tab_status(self, agent_id, state):
        tab_bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            if tab_bar.tabData(i) == agent_id:
                icon = None
                if state == "running":
                    icon = qta.icon('fa5s.play-circle', color='#3b82f6')
                elif state == "thinking":
                    icon = qta.icon('fa5s.brain', color='#8b5cf6')
                elif state == "tool":
                    icon = qta.icon('fa5s.tools', color='#f59e0b')
                elif state == "completed":
                    icon = qta.icon('fa5s.check-circle', color='#10b981')
                elif state == "failed":
                    icon = qta.icon('fa5s.exclamation-circle', color='#ef4444')
                elif state == "closed":
                    icon = qta.icon('fa5s.stop-circle', color='#6b7280')
                
                if icon:
                    self.tabs.setTabIcon(i, icon)
                break

    def _set_agent_tab_title(self, agent_id, agent_name=""):
        tab_bar = self.tabs.tabBar()
        title = agent_id if not agent_name else f"{agent_name}"
        for i in range(self.tabs.count()):
            if tab_bar.tabData(i) == agent_id:
                self.tabs.setTabText(i, title)
                return

    def _create_agent_tab(self, agent_id, agent_name=""):
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
        
        title = agent_id if not agent_name else f"{agent_name}"
        index = self.tabs.addTab(tab, title)
        self.tabs.tabBar().setTabData(index, agent_id)
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
        self.step_records = []
        self.pending_tool_results = {}
        self.current_content_buffer = ""
        self.current_thinking_buffer = ""
        self.temp_thinking_bubble = None
        self.last_agent_bubble = None
        self.llm_worker = None
        self.daemon_running = False
        self.daemon_worker = None
        self.code_worker = None
        self.chat_layout = chat_layout
        self.active_skills_label = active_skills_label
        self.session_widget = session_widget
        self.chat_scroll = chat_scroll
        self.empty_state = None
        self.displayed_count = 0
        self.load_more_btn = None
        self.auto_loading_history = False
        self.content_flush_timer = None
        self.thinking_flush_timer = None
        self.pending_thinking_delta = ""
        self.run_phase = "待开始"
        self.right_panel_mode = "files"
        self.right_panel_visible = False
        self.session_status = "draft"
        self.has_file_changes = False
        self.changed_files = []
        self.auto_scroll_enabled = True
        self.scroll_flush_timer = None
        self.pending_scroll_force = False
        self.active_turn_id = 0
        self.completed_turn_id = 0
        self.persisted_agents = []
        self.sub_agent_events = []
        self.observability_events = []
        self.system_prompt_text = ""
        self.system_prompt_appends = []
        self.plan_mode_enabled = False
        self.plan_config = json_copy(DEFAULT_PLAN_CONFIG, dict(DEFAULT_PLAN_CONFIG))
        self.plan_phase = PLAN_MODE_DISABLED
        self.plan_protocol_version = PLAN_PROTOCOL_VERSION
        self.plan_mode_state = PLAN_MODE_EXPLORING
        self.plan_document = ""
        self.pending_plan_questions = []

class SmartSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)

    def mouseDoubleClickEvent(self, event):
        # Reset to default sizes on double click
        if self.splitter():
            self.splitter().on_handle_double_clicked(self)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.splitter():
            self.splitter().check_auto_collapse()

class SmartSplitter(QSplitter):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(1) # Visual width is 1px via CSS, but we keep this consistent
        
        # Default Stylesheet for Handles
        base_style = """
            QSplitter::handle { 
                background-color: #e5e7eb; 
                margin: 0 4px; 
            } 
            QSplitter::handle:hover { 
                background-color: #3b82f6; 
            }
        """
        if orientation == Qt.Horizontal:
            # Horizontal handles have vertical margin/padding in some contexts, but here margin 0 4px is for horizontal spacing?
            # Actually for horizontal splitter, handle is vertical bar.
            # margin: 0 4px means top/bottom 0, left/right 4px? No.
            # In Qt SS, margin is usually external.
            # Let's use the user provided CSS which was known good.
            self.setStyleSheet("""
                QSplitter::handle:horizontal { 
                    background-color: #e5e7eb; 
                    width: 1px; 
                    margin: 0 4px; 
                } 
                QSplitter::handle:horizontal:hover { 
                    background-color: #3b82f6; 
                }
                QSplitter::handle:vertical { 
                    background-color: #e5e7eb; 
                    height: 1px; 
                    margin: 4px 0; 
                } 
                QSplitter::handle:vertical:hover { 
                    background-color: #3b82f6; 
                }
            """)
        else:
            self.setStyleSheet("""
                QSplitter::handle:vertical { 
                    background-color: #e5e7eb; 
                    height: 1px; 
                    margin: 4px 0; 
                } 
                QSplitter::handle:vertical:hover { 
                    background-color: #3b82f6; 
                }
            """)

    def createHandle(self):
        return SmartSplitterHandle(self.orientation(), self)

    def on_handle_double_clicked(self, handle):
        # Find which handle was clicked
        idx = -1
        for i in range(1, self.count()):
            if self.handle(i) == handle:
                idx = i
                break
        
        if idx == -1:
            return

        sizes = self.sizes()
        if self.orientation() == Qt.Horizontal:
            # Assuming 3-column layout: [Sidebar, Main, RightSidebar]
            # Handle 1: Between Sidebar (0) and Main (1)
            if idx == 1: 
                target_width = 260
                if len(sizes) > 1:
                    current_w = sizes[0]
                    diff = target_width - current_w
                    sizes[0] = target_width
                    sizes[1] -= diff
            # Handle 2: Between Main (1) and RightSidebar (2)
            elif idx == 2:
                target_width = 280
                if len(sizes) > 2:
                    current_w = sizes[2]
                    diff = target_width - current_w
                    sizes[2] = target_width
                    sizes[1] -= diff
        else:
            # Vertical Splitter (Workspace)
            # Default ratio 2:1
            total = sum(sizes)
            target_h1 = int(total * 0.66)
            target_h2 = total - target_h1
            sizes = [target_h1, target_h2]
            
        self.setSizes(sizes)

    def check_auto_collapse(self):
        if self.orientation() == Qt.Horizontal:
            sizes = self.sizes()
            # Left sidebar (index 0)
            if len(sizes) > 0 and sizes[0] < 50 and sizes[0] > 0:
                sizes[1] += sizes[0]
                sizes[0] = 0
                self.setSizes(sizes)
            
            # Right sidebar (index 2)
            if len(sizes) > 2:
                if sizes[2] < 50 and sizes[2] > 0:
                    sizes[1] += sizes[2]
                    sizes[2] = 0
                    self.setSizes(sizes)

def resolve_app_icon_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, 'images', 'logo.ico'),
        os.path.join(base_dir, 'images', 'logo.png'),
        os.path.join(base_dir, '_internal', 'images', 'logo.ico'),
        os.path.join(base_dir, '_internal', 'images', 'logo.png'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepSeek Cowork")
        
        # Set Window Icon
        icon_path = resolve_app_icon_path()
        
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)

        self.resize(1280, 720)
        self.setAcceptDrops(True)
        self.workspace_dir = None
        
        # Apply Clean Light Theme manually for optimized components
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {DesignTokens.bg_app}; }}
            QLabel[roleTitle="true"] {{ font-size: 18px; font-weight: 700; color: {DesignTokens.text_primary}; }}
            QLabel[roleSubtitle="true"] {{ font-size: 13px; color: {DesignTokens.text_secondary}; }}
            QTextEdit#MainInput {{
                padding: 12px 16px;
                border-radius: 24px;
                border: 1px solid {DesignTokens.border};
                background: {DesignTokens.bg_main};
                font-size: 14px;
                color: {DesignTokens.text_primary};
            }}
            QTextEdit#MainInput:focus {{
                border: 1px solid {DesignTokens.primary};
                background: {DesignTokens.bg_main};
            }}
            QScrollArea {{ border: none; background: transparent; }}
            QTabWidget::pane {{ border: none; }}
            QTabBar::tab {{
                background: transparent;
                padding: 8px 16px;
                margin-right: 4px;
                border-radius: 6px;
                color: {DesignTokens.text_secondary};
            }}
            QTabBar::tab:selected {{
                background: {DesignTokens.primary_soft};
                color: {DesignTokens.primary};
                font-weight: bold;
            }}

            /* Global Scrollbar Beautification */
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 6px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {DesignTokens.border};
                min-height: 20px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {DesignTokens.border_strong};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                border: none;
                background: transparent;
                height: 6px;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {DesignTokens.border};
                min-width: 20px;
                border-radius: 3px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {DesignTokens.border_strong};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
        """)
        
        self.sessions = {}
        self.current_session_id = None
        self.messages = []
        self.tool_cards = {}
        self.pending_tool_results = {}
        self.current_content_buffer = ""
        self.current_thinking_buffer = ""
        self.temp_thinking_bubble = None
        self.last_agent_bubble = None
        self.llm_worker = None
        self.code_worker = None
        self.active_run_session_id = None
        self.active_code_session_id = None
        self.chat_layout = None
        self.active_skills_label = None
        self.current_selected_tool_id = None
        self._last_submit_text = ""
        self._last_submit_ts = 0.0
        self._detached_workers = []
        
        self.config_manager = ConfigManager()
        self.skill_manager = SkillManager(None, self.config_manager)
        self.skill_generator = SkillGenerator(self.config_manager)
        self.daemon_host = DEFAULT_HOST
        self.daemon_port = self.config_manager.get("daemon_port", DEFAULT_PORT)
        self.daemon_client = None
        self.daemon_available = False
        self.daemon_process = None
        self.daemon_runtime_signature = get_runtime_signature()
        self.gateway_process = None
        self.gateway_log_file = None
        self.tray_icon = None
        self.daemon_timer = None
        
        # Animation Throttling
        self.last_message_time = 0
        self.last_ui_update_time = 0

        # Connect to Interaction Bridge
        bridge.interaction_requested.connect(self.handle_interaction_request)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        
        self.main_splitter = SmartSplitter(Qt.Horizontal)
        root_layout.addWidget(self.main_splitter)

        # --- Sidebar ---
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        # sidebar.setMinimumWidth(200) # Removed to allow collapsing
        # sidebar.setStyleSheet("background-color: #f9fafb; border-right: 1px solid #e5e7eb;")
        sidebar.setStyleSheet(f"background-color: {DesignTokens.bg_sidebar}; border-right: 1px solid {DesignTokens.border};")
        
        # Lower sidebar weight: Removed shadow
        # sidebar.setGraphicsEffect(None) 

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 24, 16, 24)
        sidebar_layout.setSpacing(16)

        app_title = QLabel("DeepSeek Cowork")
        app_title.setProperty("roleTitle", True)
        sidebar_layout.addWidget(app_title)
        
        app_subtitle = QLabel("智能文件助手")
        app_subtitle.setText("文件协作助手")
        app_subtitle.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(app_subtitle)

        new_chat_btn = QPushButton(" 新建对话")
        new_chat_btn.setText(" 新任务")
        new_chat_btn.setIcon(qta.icon('fa5s.plus', color='#ffffff'))
        new_chat_btn.setCursor(Qt.PointingHandCursor)
        new_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #2f6fed; 
                color: white; 
                border-radius: 14px; 
                padding: 12px 16px;
                font-weight: 700;
                border: none;
            }
            QPushButton:hover { background-color: #245fce; }
        """)
        new_chat_btn.clicked.connect(self.new_conversation)
        sidebar_layout.addWidget(new_chat_btn)

        # History List
        history_label = QLabel("历史会话")
        history_label.setText("最近任务")
        history_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px; font-weight: 600; margin-top: 12px;")
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
        sidebar_footer_label.setText("系统入口")
        sidebar_footer_label.setProperty("roleSubtitle", True)
        sidebar_layout.addWidget(sidebar_footer_label)
        
        sidebar_btn_style = """
            QPushButton { text-align: left; padding: 8px; border: none; color: #4b5563; background: transparent; border-radius: 6px; }
            QPushButton:hover { background-color: #e5e7eb; color: #111827; }
        """
        
        sidebar_settings_btn = QPushButton(" 系统设置")
        sidebar_settings_btn.setText(" 系统设置")
        sidebar_settings_btn.setIcon(qta.icon('fa5s.cog', color='#4b5563'))
        sidebar_settings_btn.setCursor(Qt.PointingHandCursor)
        sidebar_settings_btn.setStyleSheet(sidebar_btn_style)
        sidebar_settings_btn.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(sidebar_settings_btn)
        
        sidebar_skills_btn = QPushButton(" 功能中心")
        sidebar_skills_btn.setText(" 能力中心")
        sidebar_skills_btn.setIcon(qta.icon('fa5s.puzzle-piece', color='#4b5563'))
        sidebar_skills_btn.setCursor(Qt.PointingHandCursor)
        sidebar_skills_btn.setStyleSheet(sidebar_btn_style)
        sidebar_skills_btn.clicked.connect(self.open_skills_center)
        sidebar_layout.addWidget(sidebar_skills_btn)

        self.main_splitter.addWidget(sidebar)

        # --- Main Content ---
        main_container = QWidget()
        main_container.setObjectName("MainContainer")
        main_container.setMinimumWidth(400) # Protect main content
        self.main_splitter.addWidget(main_container)

        # Right Sidebar (Context Drawer)
        self.right_sidebar = QWidget()
        self.right_sidebar.setObjectName("RightSidebar")
        self.right_sidebar.setStyleSheet(f"background-color: {DesignTokens.bg_main}; border-left: 1px solid {DesignTokens.border};")
        self.right_sidebar.setVisible(False)
        
        right_layout = QVBoxLayout(self.right_sidebar)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        
        right_header = QFrame()
        right_header.setStyleSheet(f"background: {DesignTokens.bg_card}; border-bottom: 1px solid {DesignTokens.border};")
        right_header_layout = QHBoxLayout(right_header)
        right_header_layout.setContentsMargins(16, 14, 16, 14)
        self.right_title_label = QLabel("任务上下文")
        self.right_title_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {DesignTokens.text_primary};")
        self.right_desc_label = QLabel("查看文件、变更与执行步骤")
        self.right_desc_label.setStyleSheet(f"font-size: 12px; color: {DesignTokens.text_secondary};")
        right_title_box = QVBoxLayout()
        right_title_box.setContentsMargins(0, 0, 0, 0)
        right_title_box.setSpacing(2)
        right_title_box.addWidget(self.right_title_label)
        right_title_box.addWidget(self.right_desc_label)
        right_header_layout.addLayout(right_title_box, 1)
        right_layout.addWidget(right_header)

        # Right Sidebar Tabs
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
        
        self.right_inner_splitter = SmartSplitter(Qt.Vertical)
        
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
        
        self.right_inner_splitter.addWidget(self.file_tree)
        
        # Preview Area in Workspace Tab
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        
        r_preview_header = QLabel("  内容预览")
        r_preview_header.setStyleSheet("font-weight: 600; color: #4b5563; padding: 8px 12px; border-top: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb; background: #f9fafb;")
        preview_layout.addWidget(r_preview_header)
        
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
        
        preview_layout.addWidget(self.preview_stack)
        
        self.right_inner_splitter.addWidget(preview_container)
        self.right_inner_splitter.setStretchFactor(0, 2)
        self.right_inner_splitter.setStretchFactor(1, 1)
        
        ws_tab_layout.addWidget(self.right_inner_splitter)
        
        self.right_tabs.addTab(self.workspace_tab, "工作区文件")
        
        self.change_tab = QWidget()
        change_layout = QVBoxLayout(self.change_tab)
        change_layout.setContentsMargins(12, 12, 12, 12)
        change_layout.setSpacing(10)
        self.change_intro_label = QLabel("本次任务涉及的文件变更会在这里汇总显示。")
        self.change_intro_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        change_layout.addWidget(self.change_intro_label)
        self.change_list = QListWidget()
        self.change_list.setStyleSheet(f"border: 1px solid {DesignTokens.border}; border-radius: 12px; background: {DesignTokens.bg_card};")
        change_layout.addWidget(self.change_list, 1)
        self.right_tabs.addTab(self.change_tab, "变更")

        self.plan_tab = QWidget()
        plan_layout = QVBoxLayout(self.plan_tab)
        plan_layout.setContentsMargins(12, 12, 12, 12)
        plan_layout.setSpacing(10)
        self.plan_status_label = QLabel("状态：未启用")
        self.plan_status_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        plan_layout.addWidget(self.plan_status_label)
        self.plan_title_value = QLabel("暂无计划")
        self.plan_title_value.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {DesignTokens.text_primary};")
        self.plan_title_value.setWordWrap(True)
        plan_layout.addWidget(self.plan_title_value)
        self.plan_summary_value = QLabel("开启计划模式后，AI 会先给出可讨论的执行计划。")
        self.plan_summary_value.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        self.plan_summary_value.setWordWrap(True)
        plan_layout.addWidget(self.plan_summary_value)
        self.plan_pending_label = QLabel("待回答问题：暂无")
        self.plan_pending_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        self.plan_pending_label.setWordWrap(True)
        plan_layout.addWidget(self.plan_pending_label)
        self.plan_document_view = ReadOnlyTextEdit()
        self.plan_document_view.setStyleSheet(
            f"border: 1px solid {DesignTokens.border}; border-radius: 12px; background: {DesignTokens.bg_card}; color: {DesignTokens.text_primary}; padding: 8px;"
        )
        self.plan_document_view.setPlaceholderText("等待 <proposed_plan> 计划文档...")
        plan_layout.addWidget(self.plan_document_view, 1)
        self.right_tabs.addTab(self.plan_tab, "计划")

        # Tab 2: Observability
        self.tool_details_tab = QWidget()
        td_layout = QVBoxLayout(self.tool_details_tab)
        td_layout.setContentsMargins(12, 12, 12, 12)
        td_layout.setSpacing(12)
        self.step_intro_label = QLabel("本轮任务的系统提示词、工具调用与工具返回会在这里实时显示。")
        self.step_intro_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        td_layout.addWidget(self.step_intro_label)

        obs_prompt_label = QLabel("系统提示词")
        obs_prompt_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #374151;")
        td_layout.addWidget(obs_prompt_label)

        self.observability_prompt_edit = ReadOnlyTextEdit()
        self.observability_prompt_edit.setPlaceholderText("等待本轮系统提示词...")
        self.observability_prompt_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #f9fafb;
                color: #374151;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        td_layout.addWidget(self.observability_prompt_edit, 2)

        obs_log_label = QLabel("工具调用与返回")
        obs_log_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #374151;")
        td_layout.addWidget(obs_log_label)

        self.observability_log_edit = ReadOnlyTextEdit()
        self.observability_log_edit.setPlaceholderText("工具调用和返回内容会按时间顺序显示...")
        self.observability_log_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #ffffff;
                color: #374151;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        td_layout.addWidget(self.observability_log_edit, 3)

        self.step_list = QListWidget()
        self.step_list.setStyleSheet(f"border: 1px solid {DesignTokens.border}; border-radius: 12px; background: {DesignTokens.bg_card};")
        self.step_list.itemClicked.connect(self.on_step_item_clicked)
        self.step_list.setVisible(False)
        td_layout.addWidget(self.step_list)
        
        td_header = QLabel("工具调用详情")
        td_header.setStyleSheet("font-size: 14px; font-weight: bold; color: #111827;")
        td_header.setVisible(False)
        td_layout.addWidget(td_header)
        
        # Tool ID / Name
        self.td_info_label = QLabel("选择左侧工具卡片查看详情")
        self.td_info_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.td_info_label.setVisible(False)
        td_layout.addWidget(self.td_info_label)
        
        self.td_meta_label = QLabel("")
        self.td_meta_label.setStyleSheet("color: #6b7280; font-size: 11px; margin-bottom: 4px;")
        self.td_meta_label.setVisible(False)
        td_layout.addWidget(self.td_meta_label)
        
        # Args
        td_args_label = QLabel("Arguments:")
        td_args_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; margin-top: 8px;")
        td_args_label.setVisible(False)
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
        self.td_args_edit.setVisible(False)
        td_layout.addWidget(self.td_args_edit)
        
        td_result_header = QWidget()
        td_result_header_layout = QHBoxLayout(td_result_header)
        td_result_header_layout.setContentsMargins(0, 0, 0, 0)
        td_result_header_layout.setSpacing(8)
        td_result_label = QLabel("Result:")
        td_result_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; margin-top: 8px;")
        td_result_header_layout.addWidget(td_result_label)
        td_result_header_layout.addStretch()
        self.td_copy_result_btn = QPushButton("复制结果")
        self.td_copy_result_btn.setCursor(Qt.PointingHandCursor)
        self.td_copy_result_btn.setIcon(qta.icon('fa5s.copy', color='#4b5563'))
        self.td_copy_result_btn.setFixedHeight(26)
        self.td_copy_result_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background: #f9fafb;
                color: #6b7280;
                padding: 2px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                color: #2563eb;
                border-color: #2563eb;
                background: #ffffff;
            }
        """)
        self.td_copy_result_btn.clicked.connect(self.copy_tool_result)
        td_result_header_layout.addWidget(self.td_copy_result_btn)
        td_result_header.setVisible(False)
        td_layout.addWidget(td_result_header)
        
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
        self.td_result_edit.setVisible(False)
        td_layout.addWidget(self.td_result_edit)
        
        self.right_tabs.addTab(self.tool_details_tab, "观测")

        # Tab 4: Sub-Agent Monitor
        self.sub_agent_tab = QWidget()
        sub_agent_layout = QVBoxLayout(self.sub_agent_tab)
        sub_agent_layout.setContentsMargins(12, 12, 12, 12)
        sub_agent_layout.setSpacing(8)
        self.sub_agent_intro_label = QLabel("子 Agent 的实时状态和执行日志（仅 UI 展示，不写入主对话上下文）。")
        self.sub_agent_intro_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
        sub_agent_layout.addWidget(self.sub_agent_intro_label)
        self.sub_agent_monitor = SubAgentMonitor()
        sub_agent_layout.addWidget(self.sub_agent_monitor, 1)
        self.right_tabs.addTab(self.sub_agent_tab, "子Agent")

        # Sub-Agent Monitor (legacy independent window entry)
        self.sub_agent_monitor_window = None
        
        right_layout.addWidget(self.right_tabs)
        try:
            self.right_tabs.setTabText(0, "文件")
            self.right_tabs.setTabText(1, "变更")
            self.right_tabs.setTabText(2, "计划")
            self.right_tabs.setTabText(3, "观测")
            self.right_tabs.setTabText(4, "子Agent")
        except Exception:
            pass
        
        self.main_splitter.addWidget(self.right_sidebar)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        
        # Set Initial Sizes (Sidebar: 260, Main: Flexible, Right: 280)
        self.main_splitter.setSizes([260, 800, 280])

        # Main Layout Construction
        layout = QVBoxLayout(main_container)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(20)

        # Top Bar
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        title_label = QLabel("你好，需要我为你做些什么？")
        title_label.setText("在工作区里完成一个任务")
        title_label.setProperty("roleTitle", True)
        subtitle_label = QLabel("选择工作区，描述你的需求，我会帮你完成文件操作。")
        subtitle_label.setText("先确认当前工作区，再描述你要处理的文件、报告或整理任务。")
        subtitle_label.setProperty("roleSubtitle", True)
        title_box.addWidget(title_label)
        title_box.addWidget(subtitle_label)
        top_bar.addLayout(title_box)
        top_bar.addStretch()
        
        # Workspace Selector
        ws_container = QFrame()
        ws_container.setStyleSheet(f"background: {DesignTokens.bg_secondary}; border: 1px solid {DesignTokens.border}; border-radius: 16px; padding: 4px;")
        ws_layout = QHBoxLayout(ws_container)
        ws_layout.setContentsMargins(12, 10, 12, 10)
        ws_layout.setSpacing(10)
        
        self.ws_label = QLabel("当前文件夹: 未选择")
        self.ws_label.setText("当前工作区：未选择")
        self.ws_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-weight: 600;")
        self.security_badge = QLabel("安全范围：仅工作区")
        self.security_badge.setStyleSheet(f"background: {DesignTokens.success_bg}; color: {DesignTokens.success_text}; border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;")
        self.security_badge.hide()
        self.model_badge = QLabel("模型：连接中")
        self.model_badge.setStyleSheet(f"background: {DesignTokens.primary_soft}; color: {DesignTokens.primary}; border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;")
        self.model_badge.hide()
        self.phase_badge = QLabel("状态：待开始")
        self.phase_badge.setStyleSheet(f"background: {DesignTokens.bg_main}; color: {DesignTokens.text_secondary}; border: 1px solid {DesignTokens.border}; border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;")
        self.phase_badge.hide()
        
        self.recent_btn = QPushButton()
        self.recent_btn.setIcon(qta.icon('fa5s.history', color='#6b7280'))
        self.recent_btn.setToolTip("最近使用的文件夹")
        self.recent_btn.setFixedWidth(32)
        self.recent_btn.setCursor(Qt.PointingHandCursor)
        self.recent_btn.setStyleSheet("border: none; background: transparent;")
        self.recent_btn.clicked.connect(self.show_recent_menu)
        self.recent_btn.hide()
        
        self.ws_btn = QPushButton(" 切换")
        self.ws_btn.setText(" 切换工作区")
        self.ws_btn.setIcon(qta.icon('fa5s.folder-open', color='#374151'))
        self.ws_btn.setCursor(Qt.PointingHandCursor)
        self.ws_btn.setStyleSheet(f"background: {DesignTokens.bg_main}; border: 1px solid {DesignTokens.border}; border-radius: 12px; padding: 6px 14px; color: {DesignTokens.text_primary};")
        self.ws_btn.clicked.connect(self.select_workspace)
        
        ws_layout.addWidget(self.ws_label)
        ws_layout.addStretch()
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
        self.input_field.setPlaceholderText("描述你要完成的任务，例如：整理本周截图并生成周报摘要")
        self.input_field.returnPressed.connect(self.handle_send)

        self.plan_mode_check = QCheckBox("计划模式")
        self.plan_mode_check.setCursor(Qt.PointingHandCursor)
        self.plan_mode_check.toggled.connect(self.on_plan_mode_toggled)

        self.pause_btn = QPushButton()
        self.pause_btn.setIcon(qta.icon('fa5s.pause', color='#4b5563'))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setVisible(False)
        self.pause_btn.setObjectName("SecondaryBtn")
        self.pause_btn.setFixedHeight(38)
        
        self.action_btn = QPushButton("发送")
        self.action_btn.setText("开始")
        self.action_btn.setIcon(qta.icon('fa5s.paper-plane', color='white'))
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setFixedSize(96, 40)
        self.action_btn.setAutoDefault(False)
        self.action_btn.setDefault(False)
        self.action_btn.setStyleSheet(f"background-color: {DesignTokens.primary}; color: white; border-radius: 20px; font-weight: 700; border: none;")
        self.action_btn.clicked.connect(self.on_action_clicked)
        
        self.loop_hint = QPushButton(" 循环中")
        self.loop_hint.setText(" 处理中")
        self.loop_hint.setIcon(qta.icon('fa5s.exclamation-circle', color='#ef4444'))
        self.loop_hint.setFlat(True)
        self.loop_hint.setStyleSheet(f"color: {DesignTokens.warning_text}; font-size: 11px; margin-right: 8px; border: none; text-align: left;")
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
        bottom_controls.addWidget(self.plan_mode_check)
        bottom_controls.addWidget(self.pause_btn)
        bottom_controls.addWidget(self.loop_hint)
        bottom_controls.addWidget(self.action_btn)

        layout.addLayout(bottom_controls)

        # Init Data
        self.data_dir = get_app_data_dir()
        self.chat_history_dir = self.config_manager.get_chat_history_dir()
        os.makedirs(self.chat_history_dir, exist_ok=True)
        self.chat_storage = ChatStorage(os.path.join(self.chat_history_dir, "chat_history.sqlite"))
        
        self.create_new_session()
        self.refresh_history_list()
        self.load_default_workspace()
        
        # Initialize Drag Overlay
        self.drag_overlay = DragOverlay(self)
        self.drag_overlay.resize(self.size())
        
        # Update UI state based on workspace
        self.update_ui_state_for_workspace()
        self.setup_daemon_client()
        self.start_daemon_monitor()
        self.setup_tray()

    def process_ui_events(self, force=False):
        import time
        now = time.time()
        if force or (now - self.last_ui_update_time > 0.05):
            QApplication.processEvents()
            self.last_ui_update_time = now

    def update_ui_state_for_workspace(self):
        if self.workspace_dir:
            self.input_field.setEnabled(True)
            self.input_field.setPlaceholderText("例如：把这个文件夹里的图片按日期分类")
            self.action_btn.setEnabled(True)
            self.ws_label.setStyleSheet(f"color: {DesignTokens.success_text}; font-weight: 600;")
        else:
            # Keep input enabled but change placeholder to guide user
            self.input_field.setPlaceholderText("📁 先选择或拖拽一个文件夹到这里...")
            # Disable send button
            self.action_btn.setEnabled(False)
            self.ws_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-weight: 500;")

        self.refresh_context_badges()

    def update_ui_state_for_workspace(self):
        if self.workspace_dir:
            self.input_field.setEnabled(True)
            self.input_field.setPlaceholderText("描述你要完成的任务，例如：整理本周截图并生成周报摘要")
            self.action_btn.setEnabled(True)
            self.ws_label.setStyleSheet(f"color: {DesignTokens.success_text}; font-weight: 600;")
        else:
            self.input_field.setEnabled(True)
            self.input_field.setPlaceholderText("先选择一个工作区，再开始描述你要处理的任务")
            self.action_btn.setEnabled(False)
            self.ws_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-weight: 500;")
        self.refresh_context_badges()

    def refresh_context_badges(self, session_id=None):
        state = self.get_session(session_id)
        provider_map = {
            "openai": "OpenAI Compatible",
            "anthropic": "Anthropic",
        }
        provider = self.config_manager.get("llm_provider", "openai")
        model_name = self.config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        provider_text = provider_map.get(provider, provider)
        connection_text = "Daemon Ready" if getattr(self, "daemon_available", False) else "Local Agent"
        self.model_badge.setText(f"{provider_text} | {model_name} | {connection_text}")

        if self.config_manager.get_god_mode():
            self.security_badge.setText("Extended Access")
            self.security_badge.setStyleSheet(
                f"background: {DesignTokens.warning_bg}; color: {DesignTokens.warning_text}; "
                f"border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;"
            )
        else:
            self.security_badge.setText("Workspace Only")
            self.security_badge.setStyleSheet(
                f"background: {DesignTokens.success_bg}; color: {DesignTokens.success_text}; "
                f"border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;"
            )

        phase = "Idle"
        if state:
            phase = getattr(state, "run_phase", "Idle") or "Idle"
        self.phase_badge.setText(f"Phase: {phase}")

        has_workspace = bool(self.workspace_dir)
        has_plan_context = bool(
            state
            and (
                state.plan_mode_enabled
                or bool(getattr(state, "plan_document", ""))
                or bool(getattr(state, "pending_plan_questions", []))
            )
        )
        has_observability_context = bool(
            state
            and (
                getattr(state, "observability_events", [])
                or getattr(state, "system_prompt_text", "")
                or getattr(state, "system_prompt_appends", [])
            )
        )
        has_context = bool(state and (state.step_records or state.has_file_changes or has_plan_context or has_observability_context))
        self.right_panel_visible = has_workspace or has_context
        self.right_sidebar.setVisible(self.right_panel_visible)
        if state and state.session_id == self.current_session_id:
            self.refresh_plan_controls(state.session_id)
            self.refresh_plan_view(state.session_id)

    def _extract_proposed_plan(self, content):
        text = str(content or "")
        matches = re.findall(r"<proposed_plan>\s*(.*?)\s*</proposed_plan>", text, flags=re.IGNORECASE | re.DOTALL)
        if len(matches) != 1:
            return ""
        return (matches[0] or "").strip()

    def _extract_pending_plan_questions_from_args(self, args_obj):
        if not isinstance(args_obj, dict):
            return []
        return normalize_pending_plan_questions(args_obj.get("questions"))

    def _session_plan_meta(self, state):
        if not state:
            return {}
        return {
            "plan_mode_enabled": bool(getattr(state, "plan_mode_enabled", False)),
            "plan_config": normalize_plan_config(getattr(state, "plan_config", DEFAULT_PLAN_CONFIG)),
            "plan_phase": normalize_plan_phase(
                getattr(state, "plan_phase", ""),
                default=derive_plan_phase(
                    getattr(state, "plan_mode_enabled", False),
                    getattr(state, "plan_mode_state", PLAN_MODE_EXPLORING),
                    getattr(state, "plan_document", ""),
                ),
            ),
            "plan_protocol_version": int(getattr(state, "plan_protocol_version", PLAN_PROTOCOL_VERSION) or PLAN_PROTOCOL_VERSION),
            "plan_mode_state": normalize_plan_phase(
                getattr(state, "plan_mode_state", PLAN_MODE_EXPLORING),
                default=PLAN_MODE_EXPLORING,
            ),
            "plan_document": str(getattr(state, "plan_document", "") or "").strip(),
            "pending_plan_questions": normalize_pending_plan_questions(
                getattr(state, "pending_plan_questions", [])
            ),
        }

    def refresh_plan_controls(self, session_id=None):
        state = self.get_session(session_id)
        if not state or state.session_id != self.current_session_id:
            return
        plan_enabled = bool(state.plan_mode_enabled)
        blocked_check = self.plan_mode_check.blockSignals(True)
        self.plan_mode_check.setChecked(plan_enabled)
        self.plan_mode_check.blockSignals(blocked_check)
        self.plan_mode_check.setEnabled(
            (not ((state.llm_worker and state.llm_worker.isRunning()) or getattr(state, "daemon_running", False)))
        )
        self.plan_mode_check.setText("计划模式")

    def refresh_plan_view(self, session_id=None):
        state = self.get_session(session_id)
        if not state or state.session_id != self.current_session_id:
            return
        self.plan_status_label.setText(f"状态：{plan_phase_label(state.plan_phase)}")
        pending_questions = normalize_pending_plan_questions(getattr(state, "pending_plan_questions", []))
        if pending_questions:
            headers = []
            for item in pending_questions[:3]:
                header = str(item.get("header") or item.get("id") or "").strip()
                if header:
                    headers.append(header)
            suffix = " ..." if len(pending_questions) > 3 else ""
            self.plan_pending_label.setText(f"待回答问题：{len(pending_questions)} 个（{', '.join(headers)}{suffix}）")
        else:
            self.plan_pending_label.setText("待回答问题：暂无")
        plan_document = str(getattr(state, "plan_document", "") or "").strip()
        if not plan_document:
            self.plan_title_value.setText("暂无计划")
            self.plan_summary_value.setText("开启计划模式后，AI 将通过 <proposed_plan> 交付计划文档。")
            self.plan_document_view.setPlainText("")
            return
        lines = [line.strip() for line in plan_document.splitlines() if line.strip()]
        title = "计划文档"
        summary = "计划已更新。"
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip() or title
                continue
            summary = line
            break
        self.plan_title_value.setText(title)
        self.plan_summary_value.setText(summary)
        self.plan_document_view.setPlainText(plan_document)

    def set_session_phase(self, phase, session_id=None):
        state = self.get_session(session_id)
        if not state:
            return
        state.run_phase = phase
        if state.session_id == self.current_session_id:
            self.refresh_context_badges(state.session_id)

    def set_session_status(self, status, session_id=None, save=False):
        state = self.get_session(session_id)
        if not state:
            return
        state.session_status = status
        if save and state.messages:
            self.save_chat_history(session_id=state.session_id)
        if state.session_id == self.current_session_id:
            self.refresh_context_badges(state.session_id)
            self.refresh_history_list()

    def refresh_change_list(self, session_id=None):
        state = self.get_session(session_id)
        if not state:
            return
        if session_id is None or state.session_id == self.current_session_id:
            self.change_list.clear()
            if not state.changed_files:
                item = QListWidgetItem("No file changes yet in this task.")
                item.setFlags(Qt.NoItemFlags)
                self.change_list.addItem(item)
            else:
                seen = set()
                for entry in state.changed_files:
                    if not isinstance(entry, dict):
                        continue
                    path = entry.get("path") or ""
                    if path in seen:
                        continue
                    seen.add(path)
                    change_type = entry.get("type") or "updated"
                    label = path
                    if self.workspace_dir and path.startswith(self.workspace_dir):
                        label = os.path.relpath(path, self.workspace_dir)
                    summary = entry.get("summary") or change_type
                    item = QListWidgetItem(f"{change_type} | {label}\n{summary}")
                    item.setData(Qt.UserRole, entry)
                    self.change_list.addItem(item)
            self.change_intro_label.setText(
                "Files touched by the current task." if state.changed_files else "File changes will appear here."
            )
            self.refresh_context_badges(state.session_id)

    def refresh_step_list(self, session_id=None):
        state = self.get_session(session_id)
        if not state:
            return
        if session_id is None or state.session_id == self.current_session_id:
            self.step_list.clear()
            if not state.step_records:
                item = QListWidgetItem("Execution steps will appear here.")
                item.setFlags(Qt.NoItemFlags)
                self.step_list.addItem(item)
            else:
                for record in state.step_records:
                    status = record.get("status") or "running"
                    title = record.get("display_title") or record.get("tool_name") or "Step"
                    summary = record.get("summary") or "Waiting for more output"
                    plan_step_title = record.get("plan_step_title") or ""
                    duration = record.get("duration")
                    duration_text = f" | {duration:.1f}s" if isinstance(duration, (int, float)) else ""
                    line2 = summary
                    if plan_step_title:
                        line2 = f"[计划] {plan_step_title} | {summary}"
                    item = QListWidgetItem(f"{title} | {status}{duration_text}\n{line2}")
                    item.setData(Qt.UserRole, record.get("tool_id"))
                    self.step_list.addItem(item)
                self.step_intro_label.setText(
                    f"本轮已记录 {len(state.step_records)} 个工具步骤；完整参数与返回见下方观测日志。"
                )
            self.refresh_context_badges(state.session_id)

    def _observability_pretty_json(self, value):
        try:
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("{") or text.startswith("["):
                    return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
                return value
            return json.dumps(value, indent=2, ensure_ascii=False)
        except Exception:
            return str(value)

    def _observability_time_text(self, ts):
        try:
            if ts is None or ts == "":
                return datetime.now().strftime("%H:%M:%S")
            return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
        except Exception:
            return datetime.now().strftime("%H:%M:%S")

    def _format_observability_event(self, event):
        if not isinstance(event, dict):
            return ""
        event_type = event.get("type") or ""
        stamp = self._observability_time_text(event.get("timestamp"))
        if event_type == "tool_call":
            return (
                f"[{stamp}] CALL {event.get('name') or 'unknown_tool'} ({event.get('id') or ''})\n"
                f"args:\n{self._observability_pretty_json(event.get('args') or {})}"
            )
        if event_type == "tool_result":
            meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
            duration = meta.get("duration")
            duration_text = f" duration={float(duration):.2f}s" if isinstance(duration, (int, float)) else ""
            result_value = event.get("result_obj") if event.get("result_obj") is not None else event.get("result", "")
            return (
                f"[{stamp}] RESULT {event.get('name') or 'unknown_tool'} ({event.get('id') or ''}){duration_text}\n"
                f"result:\n{self._observability_pretty_json(result_value)}"
            )
        if event_type == "system_prompt_append":
            source = event.get("source") or "system"
            return f"[{stamp}] SYSTEM APPEND {source}\n{event.get('content') or ''}"
        if event_type == "system_prompt":
            return f"[{stamp}] SYSTEM PROMPT loaded"
        return f"[{stamp}] {event_type or 'event'}\n{self._observability_pretty_json(event)}"

    def refresh_observability_view(self, session_id=None):
        state = self.get_session(session_id)
        if not state or state.session_id != self.current_session_id:
            return
        prompt_text = getattr(state, "system_prompt_text", "") or ""
        prompt_parts = []
        if prompt_text:
            prompt_parts.append(prompt_text)
        for index, item in enumerate(getattr(state, "system_prompt_appends", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            source = item.get("source") or "system"
            content = item.get("content") or ""
            if content:
                prompt_parts.append(f"\n\n# 追加系统消息 {index}: {source}\n{content}")
        self.observability_prompt_edit.setPlainText("".join(prompt_parts))

        log_parts = []
        for event in getattr(state, "observability_events", []) or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system_prompt":
                continue
            formatted = self._format_observability_event(event)
            if formatted:
                log_parts.append(formatted)
        self.observability_log_edit.setPlainText("\n\n---\n\n".join(log_parts))
        for edit in (self.observability_prompt_edit, self.observability_log_edit):
            cursor = edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            edit.setTextCursor(cursor)

    def handle_observability_event(self, data, session_id=None):
        if not isinstance(data, dict):
            return
        state = self.get_session(session_id)
        if not state:
            return
        event = dict(data)
        event.setdefault("timestamp", time.time())
        event_type = event.get("type") or ""
        if event_type == "system_prompt":
            state.system_prompt_text = event.get("content") or ""
        elif event_type == "system_prompt_append":
            state.system_prompt_appends.append(event)
        state.observability_events.append(event)
        if len(state.observability_events) > 500:
            state.observability_events = state.observability_events[-500:]
        if state.session_id == self.current_session_id:
            self.right_sidebar.setVisible(True)
            self.right_tabs.setCurrentIndex(3)
            self.refresh_observability_view(state.session_id)
            self.refresh_context_badges(state.session_id)

    def on_step_item_clicked(self, item):
        tool_id = item.data(Qt.UserRole)
        if not tool_id:
            return
        state = self.get_current_session()
        if not state or tool_id not in state.tool_cards:
            return
        card = state.tool_cards[tool_id]
        self.show_tool_details(tool_id, card.args, card.result, meta=card.meta, switch_tab=True)

    def setup_daemon_client(self):
        self.daemon_client = DaemonClient(self.daemon_host, self.daemon_port)
        self.try_connect_daemon(allow_start=True, retries=6)

    def start_daemon_monitor(self):
        self.daemon_timer = QTimer(self)
        self.daemon_timer.setInterval(5000)
        self.daemon_timer.timeout.connect(self.ensure_daemon_connection)
        self.daemon_timer.start()

    def ensure_daemon_connection(self):
        self.try_connect_daemon(allow_start=True, retries=0)

    def _daemon_signature_matches(self, payload):
        if not isinstance(payload, dict):
            return False
        remote = str(payload.get("signature") or "").strip()
        local = str(getattr(self, "daemon_runtime_signature", "") or "").strip()
        return bool(remote and local and remote == local)

    def try_connect_daemon(self, allow_start=False, retries=0):
        if not self.daemon_client:
            self.daemon_client = DaemonClient(self.daemon_host, self.daemon_port)
        ping_payload = self.daemon_client.ping()
        connected = bool(ping_payload)
        if connected and not self._daemon_signature_matches(ping_payload):
            try:
                self.daemon_client.shutdown()
            except Exception:
                pass
            time.sleep(0.2)
            connected = False
        if not connected and allow_start:
            self.start_daemon_process()
            for _ in range(max(retries, 0)):
                time.sleep(0.2)
                retry_ping = self.daemon_client.ping()
                if retry_ping and self._daemon_signature_matches(retry_ping):
                    connected = True
                    break
        self.daemon_available = connected
        self.refresh_context_badges()

    def start_daemon_process(self):
        try:
            if self.daemon_process and self.daemon_process.poll() is None:
                return
            python_exe = sys.executable
            script_path = os.path.abspath(__file__)
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            self.daemon_process = subprocess.Popen(
                [python_exe, script_path, "--daemon", f"--daemon-port={self.daemon_port}"],
                cwd=os.path.dirname(script_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
        except Exception:
            self.daemon_process = None

    def start_gateway_process(self):
        try:
            if self.gateway_process and self.gateway_process.poll() is None:
                return
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            env = os.environ.copy()
            if getattr(sys, 'frozen', False):
                cmd = [sys.executable, "--im-gateway"]
            else:
                python_exe = get_python_executable()
                script_path = os.path.abspath(__file__)
                cmd = [python_exe, script_path, "--im-gateway"]
            if self.gateway_log_file:
                try:
                    self.gateway_log_file.close()
                except Exception:
                    pass
                self.gateway_log_file = None
            log_path = os.path.join(get_app_data_dir(), "im_gateway.log")
            self.gateway_log_file = open(log_path, "a", encoding="utf-8")
            self.gateway_process = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.DEVNULL,
                stderr=self.gateway_log_file,
                creationflags=creationflags,
                env=env
            )
        except Exception:
            self.gateway_process = None

    def stop_gateway_process(self):
        if not self.gateway_process:
            return
        self._terminate_process(self.gateway_process)
        self.gateway_process = None
        if self.gateway_log_file:
            try:
                self.gateway_log_file.close()
            except Exception:
                pass
            self.gateway_log_file = None

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.windowIcon()
        if icon.isNull():
            icon = QIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        menu = QMenu()
        toggle_action = QAction("显示/隐藏", self)
        toggle_action.triggered.connect(self.toggle_window_visibility)
        status_action = QAction("查看状态", self)
        status_action.triggered.connect(self.show_tray_status)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(toggle_action)
        menu.addAction(status_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window_visibility()

    def toggle_window_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()

    def get_daemon_status_text(self):
        if not self.daemon_client:
            return "守护进程未初始化"
        self.try_connect_daemon(allow_start=False, retries=0)
        status = self.daemon_client.status()
        if not status or status.get("status") != "ok":
            return "守护进程未连接"
        state_text = "已休眠" if status.get("suspended") else "运行中"
        sessions = status.get("sessions", 0)
        return f"守护进程: {state_text} | 会话缓存: {sessions}"

    def show_tray_status(self):
        self.try_connect_daemon(allow_start=True, retries=2)
        status = self.get_daemon_status_text()
        if self.tray_icon:
            self.tray_icon.showMessage("状态", status, QSystemTrayIcon.Information, 3000)
        else:
            QMessageBox.information(self, "状态", status)

    def quit_app(self):
        self.shutdown_workers()
        if self.daemon_client:
            self.daemon_client.shutdown()
        self.stop_daemon_process()
        self.stop_gateway_process()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()

    def stop_daemon_process(self):
        if not self.daemon_process:
            return
        self._terminate_process(self.daemon_process)
        self.daemon_process = None

    def shutdown_workers(self):
        for session_id, state in list(self.sessions.items()):
            self._stop_live_subagents(state, force=True)
            if self.daemon_client and state.daemon_running and state.session_id:
                try:
                    self.daemon_client.stop_session(state.session_id)
                except Exception:
                    pass
            if state.daemon_worker and state.daemon_worker.isRunning():
                self._disconnect_worker_signals(state.daemon_worker)
                state.daemon_worker.abort()
                state.daemon_worker.wait(1000)
            if state.llm_worker and state.llm_worker.isRunning():
                self._disconnect_worker_signals(state.llm_worker)
                state.llm_worker.stop()
                state.llm_worker.wait(1000)
            if state.code_worker and state.code_worker.isRunning():
                self._disconnect_worker_signals(state.code_worker)
                state.code_worker.stop()
                state.code_worker.wait(1000)
            state.daemon_running = False
            state.daemon_worker = None
            state.llm_worker = None
            state.code_worker = None
        if self.daemon_timer:
            self.daemon_timer.stop()

    def _terminate_process(self, proc):
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=2)
        except Exception:
            pass
        if proc.poll() is None and platform.system() == "Windows":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def closeEvent(self, event):
        if self.tray_icon:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage("DeepSeek Cowork", "已最小化到托盘", QSystemTrayIcon.Information, 2000)
        else:
            self.shutdown_workers()
            self.stop_daemon_process()
            self.stop_gateway_process()
            event.accept()
            
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'drag_overlay'):
            self.drag_overlay.resize(self.size())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            if hasattr(self, 'drag_overlay'):
                self.drag_overlay.show()
                self.drag_overlay.raise_()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        if hasattr(self, 'drag_overlay'):
            self.drag_overlay.hide()

    def dropEvent(self, event):
        if hasattr(self, 'drag_overlay'):
            self.drag_overlay.hide()
            
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if not urls: return
            
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                # Switch workspace
                self.load_workspace(path)
            else:
                # Add file path to input or load parent workspace if none
                if self.workspace_dir:
                    current_text = self.input_field.toPlainText()
                    if current_text:
                        self.input_field.setText(current_text + " " + path)
                    else:
                        self.input_field.setText(path)
                else:
                    # No workspace selected, load parent dir
                    parent_dir = os.path.dirname(path)
                    self.load_workspace(parent_dir)
                    self.input_field.setText(path)
            
            event.acceptProposedAction()

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
        state.pending_tool_results = self.pending_tool_results
        state.current_content_buffer = self.current_content_buffer
        state.current_thinking_buffer = self.current_thinking_buffer

    def set_current_session(self, session_id):
        state = self.sessions.get(session_id)
        if not state: return
        self.current_session_id = session_id
        self.messages = state.messages
        self.tool_cards = state.tool_cards
        self.pending_tool_results = getattr(state, "pending_tool_results", {})
        self.current_content_buffer = state.current_content_buffer
        self.current_thinking_buffer = getattr(state, "current_thinking_buffer", "")
        self.temp_thinking_bubble = state.temp_thinking_bubble
        self.last_agent_bubble = state.last_agent_bubble
        self.llm_worker = state.llm_worker
        self.code_worker = state.code_worker
        self.chat_layout = state.chat_layout
        self.active_skills_label = state.active_skills_label
        self.refresh_change_list(session_id)
        self.refresh_step_list(session_id)
        self.refresh_observability_view(session_id)
        self.refresh_context_badges(session_id)
        self._render_sub_agent_monitor_for_state(state)

    def normalize_session_ui(self, state):
        if not state: return
        running = state.llm_worker and state.llm_worker.isRunning()
        paused = running and state.llm_worker.is_paused
        running_code = state.code_worker and state.code_worker.isRunning()
        running_daemon = getattr(state, "daemon_running", False)
        
        if running or running_code or running_daemon:
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

    def normalize_session_ui(self, state):
        if not state:
            return
        running = state.llm_worker and state.llm_worker.isRunning()
        paused = running and state.llm_worker.is_paused
        running_code = state.code_worker and state.code_worker.isRunning()
        running_daemon = getattr(state, "daemon_running", False)

        if running or running_code or running_daemon:
            self.action_btn.setText("停止")
            self.action_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.action_btn.setStyleSheet(
                f"background-color: {DesignTokens.error_text}; color: white; border-radius: 20px; font-weight: 700; border: none;"
            )
            self.action_btn.setEnabled(True)
            self.input_field.setEnabled(False)
            self.pause_btn.setVisible(bool(running))
            self.loop_hint.setVisible(bool(running_daemon or running_code))
            if paused:
                self.pause_btn.setIcon(qta.icon('fa5s.play', color=DesignTokens.success_text))
                self.pause_btn.setToolTip("继续")
            else:
                self.pause_btn.setIcon(qta.icon('fa5s.pause', color=DesignTokens.text_secondary))
                self.pause_btn.setToolTip("暂停")
        else:
            idle_text = "开始规划" if state.plan_mode_enabled else "开始"
            self.action_btn.setText(idle_text)
            self.action_btn.setIcon(qta.icon('fa5s.paper-plane', color='white'))
            self.action_btn.setStyleSheet(
                f"background-color: {DesignTokens.primary}; color: white; border-radius: 20px; font-weight: 700; border: none;"
            )
            self.action_btn.setEnabled(bool(self.workspace_dir))
            self.input_field.setEnabled(bool(self.workspace_dir))
            self.pause_btn.setVisible(False)
            self.loop_hint.setVisible(False)
        self.refresh_context_badges(state.session_id)
        self.refresh_observability_view(state.session_id)

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
            self._stop_live_subagents(state, force=True)
            if state.daemon_worker:
                self._disconnect_worker_signals(state.daemon_worker)
                try:
                    state.daemon_worker.abort()
                except Exception:
                    pass
            if state.llm_worker:
                self._disconnect_worker_signals(state.llm_worker)
                try:
                    state.llm_worker.stop()
                except Exception:
                    pass
            if state.code_worker:
                self._disconnect_worker_signals(state.code_worker)
                try:
                    state.code_worker.stop()
                except Exception:
                    pass
            del self.sessions[session_id]
        self.session_tabs.removeTab(index)
        if self.session_tabs.count() == 0: self.create_new_session()

    def on_session_tab_changed(self, index):
        self.sync_current_session_state()
        session_id = self.get_session_id_for_tab(index)
        if session_id:
            self.set_current_session(session_id)
            self.refresh_history_list()
            self.refresh_change_list(session_id)
            self.refresh_step_list(session_id)
            current_state = self.get_current_session()
            self.normalize_session_ui(current_state)
            self._render_sub_agent_monitor_for_state(current_state)

    def clear_chat_layout(self, chat_layout):
        while chat_layout.count():
            item = chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None: widget.deleteLater()
        chat_layout.addStretch()

    def _compute_session_title(self, messages):
        title = "新对话"
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content") or ""
                if content: title = content[:15] + "..." if len(content) > 15 else content
                break
        return title

    def update_session_tab_title(self, session_id):
        state = self.sessions.get(session_id)
        if not state: return
        title = self._compute_session_title(state.messages)
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
        state.content_flush_timer = QTimer(self)
        state.content_flush_timer.setSingleShot(True)
        state.content_flush_timer.setInterval(CONTENT_FLUSH_INTERVAL_MS)
        state.content_flush_timer.timeout.connect(lambda sid=session_id: self.flush_session_content(sid))
        state.thinking_flush_timer = QTimer(self)
        state.thinking_flush_timer.setSingleShot(True)
        state.thinking_flush_timer.setInterval(THINKING_FLUSH_INTERVAL_MS)
        state.thinking_flush_timer.timeout.connect(lambda sid=session_id: self.flush_session_thinking(sid))
        state.scroll_flush_timer = QTimer(self)
        state.scroll_flush_timer.setSingleShot(True)
        state.scroll_flush_timer.setInterval(SCROLL_FLUSH_INTERVAL_MS)
        state.scroll_flush_timer.timeout.connect(lambda sid=session_id: self.flush_session_scroll(sid))
        chat_scroll.verticalScrollBar().valueChanged.connect(lambda value, sid=session_id: self.on_chat_scroll_value_changed(value, sid))
        state.empty_state = empty_state
        self.sessions[session_id] = state
        self.session_tabs.setCurrentIndex(tab_index)
        self.set_current_session(session_id)
        self.refresh_change_list(session_id)
        self.refresh_step_list(session_id)
        self.refresh_plan_view(session_id)
        self.refresh_context_badges(session_id)
        return session_id

    def show_interaction_dialog(self, request):
        request = dict(request or {})
        kind = (request.get("kind") or "approval").strip().lower()
        title = (request.get("title") or "需要你的输入").strip()
        message = request.get("message") or ""
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        options = request.get("options") if isinstance(request.get("options"), list) else []
        questions = request.get("questions") if isinstance(request.get("questions"), list) else []
        questions = normalize_pending_plan_questions(questions)
        allow_free_text = bool(request.get("allow_free_text"))

        dialog = QDialog(self)
        dialog.setWindowTitle(title or "需要你的输入")
        dialog.resize(520, 420)
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

        details = (metadata.get("details") or "").strip()
        if details:
            details_label = QLabel(details)
            details_label.setWordWrap(True)
            details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 12px;")
            content_layout.addWidget(details_label)
        content_layout.addStretch()
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)

        hint_text = "请完成这一步操作。"
        if kind == "approval":
            severity = (metadata.get("severity") or "medium").strip().lower()
            hint_text = {
                "high": "这是高风险操作，请明确选择继续或取消。",
                "low": "请确认是否继续执行。",
            }.get(severity, "请确认是否继续执行。")
        elif kind == "choice":
            hint_text = "请选择一个选项，必要时可以补充自由文本。"
        elif kind == "multi_choice":
            hint_text = "请选择一个或多个选项，必要时可以补充自由文本。"
        elif kind == "questionnaire":
            hint_text = "请逐题选择，必要时可以补充文本。"
        elif kind == "text":
            hint_text = "请输入你要补充给 AI 的信息。"
        hint_label = QLabel(hint_text)
        hint_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: 13px;")
        layout.addWidget(hint_label)

        input_field = QLineEdit()
        input_field.setPlaceholderText("输入补充内容…")
        input_field.setVisible(kind == "text" or ((kind in {"choice", "multi_choice"}) and allow_free_text))
        layout.addWidget(input_field)

        option_combo = None
        option_checks = []
        question_controls = []
        if kind == "choice" and options:
            option_combo = QComboBox()
            option_combo.addItem("请选择…", "")
            for idx, option in enumerate(options, start=1):
                label_text = (option.get("label") or option.get("value") or f"Option {idx}").strip()
                value = (option.get("value") or label_text).strip()
                option_combo.addItem(f"{idx}. {label_text}", value)
            layout.addWidget(option_combo)
        elif kind == "multi_choice" and options:
            option_group = QGroupBox("选项")
            option_layout = QVBoxLayout(option_group)
            for idx, option in enumerate(options, start=1):
                label_text = (option.get("label") or option.get("value") or f"Option {idx}").strip()
                value = (option.get("value") or label_text).strip()
                description = (option.get("description") or "").strip()
                checkbox = QCheckBox(f"{idx}. {label_text}" + (f" - {description}" if description else ""))
                checkbox.setProperty("option_value", value)
                option_checks.append(checkbox)
                option_layout.addWidget(checkbox)
            layout.addWidget(option_group)
        elif kind == "questionnaire" and questions:
            question_scroll = QScrollArea()
            question_scroll.setWidgetResizable(True)
            question_scroll.setFrameShape(QFrame.NoFrame)
            question_wrap = QWidget()
            question_layout = QVBoxLayout(question_wrap)
            question_layout.setContentsMargins(0, 0, 0, 0)
            question_layout.setSpacing(12)
            for question_item in questions:
                q_group = QGroupBox(str(question_item.get("header") or "问题"))
                q_layout = QVBoxLayout(q_group)
                q_text = QLabel(str(question_item.get("question") or ""))
                q_text.setWordWrap(True)
                q_text.setStyleSheet(f"color: {DesignTokens.text_primary};")
                q_layout.addWidget(q_text)
                q_combo = QComboBox()
                q_combo.addItem("请选择…", "")
                for idx, q_option in enumerate(question_item.get("options") or [], start=1):
                    q_label = (q_option.get("label") or "").strip()
                    q_value = (q_option.get("value") or q_label).strip()
                    if not q_label:
                        continue
                    q_combo.addItem(f"{idx}. {q_label}", q_value)
                q_layout.addWidget(q_combo)
                q_input = QLineEdit()
                q_input.setPlaceholderText("可选：补充说明")
                q_input.setVisible(bool(allow_free_text))
                q_layout.addWidget(q_input)
                question_controls.append(
                    {
                        "id": question_item.get("id"),
                        "combo": q_combo,
                        "input": q_input,
                    }
                )
                question_layout.addWidget(q_group)
            question_layout.addStretch()
            question_scroll.setWidget(question_wrap)
            layout.addWidget(question_scroll, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        submit_btn = QPushButton("提交")
        yes_btn = QPushButton("继续")
        no_btn = QPushButton("取消")
        submit_btn.setStyleSheet(f"background: {DesignTokens.primary}; color: white; border: none; border-radius: 6px; padding: 6px 14px;")
        yes_btn.setStyleSheet(f"background: {DesignTokens.bg_secondary}; color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border}; border-radius: 6px; padding: 6px 14px;")
        no_btn.setStyleSheet(f"background: transparent; color: {DesignTokens.text_secondary}; border: 1px solid {DesignTokens.border}; border-radius: 6px; padding: 6px 14px;")
        if kind == "approval":
            button_layout.addWidget(yes_btn)
            button_layout.addWidget(no_btn)
        else:
            button_layout.addWidget(submit_btn)
            button_layout.addWidget(no_btn)
        layout.addLayout(button_layout)
        decision = {"value": False}

        def selected_values():
            values = []
            if option_combo is not None:
                combo_value = (option_combo.currentData() or "").strip()
                if combo_value:
                    values.append(combo_value)
            for checkbox in option_checks:
                if checkbox.isChecked():
                    option_value = (checkbox.property("option_value") or "").strip()
                    if option_value:
                        values.append(option_value)
            return values

        def on_submit():
            text = input_field.text().strip()
            picked = selected_values()
            if kind == "text":
                if not text:
                    QMessageBox.information(dialog, "提示", "请先输入内容。")
                    input_field.setFocus()
                    return
                decision["value"] = text
                dialog.accept()
                return
            if kind == "choice":
                if picked:
                    decision["value"] = picked[0]
                    dialog.accept()
                    return
                if allow_free_text and text:
                    decision["value"] = text
                    dialog.accept()
                    return
                QMessageBox.information(dialog, "提示", "请先选择一个选项或输入内容。")
                return
            if kind == "multi_choice":
                if picked:
                    if allow_free_text and text:
                        picked = picked + [text]
                    decision["value"] = picked
                    dialog.accept()
                    return
                if allow_free_text and text:
                    decision["value"] = [text]
                    dialog.accept()
                    return
                QMessageBox.information(dialog, "提示", "请先选择至少一个选项或输入内容。")
                return
            if kind == "questionnaire":
                answers = {}
                for control in question_controls:
                    question_id = str(control.get("id") or "").strip()
                    combo_widget = control.get("combo")
                    input_widget = control.get("input")
                    selected = ""
                    text_value = ""
                    if combo_widget is not None:
                        selected = str(combo_widget.currentData() or "").strip()
                    if input_widget is not None:
                        text_value = input_widget.text().strip()
                    if not selected and not text_value:
                        continue
                    answers[question_id] = {
                        "selected_options": [selected] if selected else [],
                        "text": text_value,
                        "raw_value": selected or text_value,
                    }
                if not answers:
                    QMessageBox.information(dialog, "提示", "请至少回答一个问题。")
                    return
                decision["value"] = answers
                dialog.accept()
                return

        def on_yes():
            decision["value"] = True
            dialog.accept()

        def on_no():
            decision["value"] = False
            dialog.reject()

        submit_btn.clicked.connect(on_submit)
        yes_btn.clicked.connect(on_yes)
        no_btn.clicked.connect(on_no)
        input_field.returnPressed.connect(on_submit)
        if input_field.isVisible():
            input_field.setFocus()
        dialog.exec()
        return decision["value"]

    def handle_interaction_request(self, request):
        state = self.get_current_session()
        if state:
            self.set_session_phase("Awaiting input", state.session_id)
        result = self.show_interaction_dialog(request)
        bridge.resolve_request((request or {}).get("request_id"), result)
    
    def handle_daemon_interaction_request(self, request, session_id=None):
        self.set_session_phase("Awaiting input", session_id)
        result = self.show_interaction_dialog(request)
        if self.daemon_client:
            self.daemon_client.respond_interaction((request or {}).get("request_id"), result)

    def refresh_history_list(self):
        self.history_container.setUpdatesEnabled(False)
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        
        conversations = self.chat_storage.list_conversations()
        conversation_ids = {c["id"] for c in conversations}

        for conv in conversations:
            session_id = conv["id"]
            title = conv["title"] or "新对话"
            if conv.get("im_provider") == "feishu":
                ts = conv.get("updated_at")
                if ts:
                    title = f"飞书对话 {datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}"
                else:
                    title = "飞书对话"
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            btn = HistoryTitleButton(title)
            btn.setCursor(Qt.PointingHandCursor)
            if session_id == self.current_session_id:
                 btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: #eff6ff; color: #1d4ed8; font-weight: 600;")
            else:
                 btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: transparent; color: #4b5563;")
            btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))
            del_btn = QPushButton()
            del_btn.setIcon(qta.icon('fa5s.trash-alt', color='#ef4444'))
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setFixedSize(28, 28)
            del_btn.setStyleSheet("border: none; background: transparent;")
            del_btn.clicked.connect(lambda checked=False, sid=session_id: self.delete_session(sid))
            row_layout.addWidget(btn, 1)
            row_layout.addWidget(del_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
            self.history_layout.addWidget(row)

        history_dir = self.chat_history_dir
        if os.path.exists(history_dir):
            files = glob.glob(os.path.join(history_dir, 'chat_history_*.json'))
            files.sort(key=os.path.getmtime, reverse=True)

            for file_path in files:
                try:
                    filename = os.path.basename(file_path)
                    session_id = filename.replace('chat_history_', '').replace('.json', '')
                    if session_id in conversation_ids:
                        continue
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if not data: continue
                        title = self._compute_session_title(data)
                        row = QWidget()
                        row_layout = QHBoxLayout(row)
                        row_layout.setContentsMargins(0, 0, 0, 0)
                        row_layout.setSpacing(8)
                        btn = HistoryTitleButton(title)
                        btn.setCursor(Qt.PointingHandCursor)
                        if session_id == self.current_session_id:
                             btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: #eff6ff; color: #1d4ed8; font-weight: 600;")
                        else:
                             btn.setStyleSheet("text-align: left; padding: 10px; border: none; border-radius: 8px; background-color: transparent; color: #4b5563;")
                        btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))
                        del_btn = QPushButton()
                        del_btn.setIcon(qta.icon('fa5s.trash-alt', color='#ef4444'))
                        del_btn.setCursor(Qt.PointingHandCursor)
                        del_btn.setFixedSize(28, 28)
                        del_btn.setStyleSheet("border: none; background: transparent;")
                        del_btn.clicked.connect(lambda checked=False, sid=session_id: self.delete_session(sid))
                        row_layout.addWidget(btn, 1)
                        row_layout.addWidget(del_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
                        self.history_layout.addWidget(row)
                except Exception as e:
                    continue
        self.history_layout.addStretch()
        self.history_container.setUpdatesEnabled(True)
        self.history_container.update()

    def refresh_history_list(self):
        self.history_container.setUpdatesEnabled(False)
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        conversations = self.chat_storage.list_conversations()
        conversation_ids = {c["id"] for c in conversations}

        for conv in conversations:
            meta = conv.get("meta") or {}
            if meta.get("archived"):
                continue

            session_id = conv["id"]
            title = conv["title"] or "新任务"
            if conv.get("im_provider") == "feishu":
                ts = conv.get("updated_at")
                title = f"飞书会话 {datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}" if ts else "飞书会话"

            status_text, status_color, status_bg = session_status_text(
                conv.get("status") or "draft",
                conv.get("im_provider"),
            )
            updated_at = conv.get("updated_at")
            time_text = datetime.fromtimestamp(updated_at).strftime("%m-%d %H:%M") if updated_at else ""

            row = QFrame()
            row.setObjectName("HistoryRow")
            row.setStyleSheet(
                f"QFrame#HistoryRow {{ background: transparent; border-radius: 12px; }}"
                f"QFrame#HistoryRow:hover {{ background: {DesignTokens.bg_secondary}; }}"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 4, 6, 4)
            row_layout.setSpacing(8)

            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(4)

            btn = HistoryTitleButton(title)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "text-align: left; padding: 6px 8px; border: none; background: transparent; "
                f"color: {DesignTokens.text_primary}; font-weight: 600;"
            )
            if session_id == self.current_session_id:
                row.setStyleSheet(
                    f"QFrame#HistoryRow {{ background: {DesignTokens.primary_soft}; border-radius: 12px; }}"
                )
            btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))

            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(8, 0, 8, 0)
            meta_row.setSpacing(6)

            status_label = QLabel(status_text)
            status_label.setStyleSheet(
                f"background: {status_bg}; color: {status_color}; border-radius: 10px; "
                "padding: 2px 8px; font-size: 11px; font-weight: 600;"
            )
            time_label = QLabel(time_text)
            time_label.setStyleSheet(f"color: {DesignTokens.text_tertiary}; font-size: 11px;")

            meta_row.addWidget(status_label)
            meta_row.addWidget(time_label)
            meta_row.addStretch()

            content_layout.addWidget(btn)
            content_layout.addLayout(meta_row)

            menu_btn = QToolButton()
            menu_btn.setText("⋯")
            menu_btn.setCursor(Qt.PointingHandCursor)
            menu_btn.setAutoRaise(True)
            menu_btn.setStyleSheet(
                f"QToolButton {{ border: none; color: {DesignTokens.text_secondary}; padding: 4px 8px; }}"
                f"QToolButton:hover {{ background: {DesignTokens.bg_card}; border-radius: 10px; }}"
            )
            menu_btn.clicked.connect(lambda checked=False, sid=session_id, btn_ref=menu_btn: self.show_session_menu(sid, btn_ref))

            row_layout.addWidget(content, 1)
            row_layout.addWidget(menu_btn, 0, Qt.AlignTop)
            self.history_layout.addWidget(row)

        history_dir = self.chat_history_dir
        if os.path.exists(history_dir):
            files = glob.glob(os.path.join(history_dir, 'chat_history_*.json'))
            files.sort(key=os.path.getmtime, reverse=True)

            for file_path in files:
                try:
                    filename = os.path.basename(file_path)
                    session_id = filename.replace('chat_history_', '').replace('.json', '')
                    if session_id in conversation_ids:
                        continue
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if not data:
                            continue
                        title = self._compute_session_title(data)
                        row = QFrame()
                        row_layout = QHBoxLayout(row)
                        row_layout.setContentsMargins(6, 4, 6, 4)
                        btn = HistoryTitleButton(title)
                        btn.setCursor(Qt.PointingHandCursor)
                        btn.setStyleSheet(
                            "text-align: left; padding: 10px 8px; border: none; background: transparent; "
                            f"color: {DesignTokens.text_primary}; font-weight: 600;"
                        )
                        btn.clicked.connect(lambda checked=False, sid=session_id: self.load_session(sid))
                        row_layout.addWidget(btn, 1)
                        self.history_layout.addWidget(row)
                except Exception:
                    continue
        self.history_layout.addStretch()
        self.history_container.setUpdatesEnabled(True)
        self.history_container.update()

    def show_session_menu(self, session_id, anchor):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)
        rename_action = QAction("重命名", self)
        archive_action = QAction("归档", self)
        delete_action = QAction("删除", self)
        delete_action.setIcon(qta.icon('fa5s.trash-alt', color=DesignTokens.error_text))

        rename_action.triggered.connect(lambda: self.rename_session(session_id))
        archive_action.triggered.connect(lambda: self.archive_session(session_id))
        delete_action.triggered.connect(lambda: self.delete_session(session_id))

        menu.addAction(rename_action)
        menu.addAction(archive_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def rename_session(self, session_id):
        record = self.chat_storage.get_conversation_record(session_id) or {}
        current_title = record.get("title") or "新任务"
        new_title, ok = QInputDialog.getText(self, "重命名任务", "新的任务标题", text=current_title)
        new_title = (new_title or "").strip()
        if not ok or not new_title:
            return
        meta = record.get("meta") or {}
        status = record.get("status") or "draft"
        self.chat_storage.upsert_conversation(session_id, title=new_title, status=status, meta=meta)
        state = self.sessions.get(session_id)
        if state:
            index = self.session_tabs.indexOf(state.session_widget)
            if index >= 0:
                self.session_tabs.setTabText(index, new_title)
        self.refresh_history_list()

    def archive_session(self, session_id):
        record = self.chat_storage.get_conversation_record(session_id) or {}
        meta = record.get("meta") or {}
        meta["archived"] = True
        self.chat_storage.upsert_conversation(
            session_id,
            title=record.get("title") or "新任务",
            status=record.get("status") or "completed",
            meta=meta,
        )
        self.refresh_history_list()

    def on_chat_scroll_value_changed(self, value, session_id):
        state = self.get_session(session_id)
        if not state:
            return
        vbar = state.chat_scroll.verticalScrollBar() if getattr(state, "chat_scroll", None) else None
        if vbar is not None:
            distance_to_bottom = max(0, vbar.maximum() - value)
            if distance_to_bottom <= SCROLL_BOTTOM_THRESHOLD_PX:
                state.auto_scroll_enabled = True
            elif not state.auto_loading_history:
                state.auto_scroll_enabled = False
        if value > 8:
            return
        if state.auto_loading_history:
            return
        if state.displayed_count >= len(state.messages):
            return
        self.load_more_history(session_id=session_id)

    def request_session_scroll_to_bottom(self, session_id=None, force=False):
        state = self.get_session(session_id)
        if not state:
            return
        if force:
            state.auto_scroll_enabled = True
            state.pending_scroll_force = True
        if not force and not state.auto_scroll_enabled:
            return
        timer = getattr(state, "scroll_flush_timer", None)
        if timer:
            if not timer.isActive():
                timer.start()
        else:
            self.flush_session_scroll(state.session_id)

    def _finalize_session_scroll(self, session_id, force=False):
        state = self.get_session(session_id)
        if not state or not getattr(state, "chat_scroll", None):
            return
        if not force and not state.auto_scroll_enabled:
            return
        vbar = state.chat_scroll.verticalScrollBar()
        vbar.setValue(vbar.maximum())

    def flush_session_scroll(self, session_id):
        state = self.get_session(session_id)
        if not state or not getattr(state, "chat_scroll", None):
            return
        force = bool(getattr(state, "pending_scroll_force", False))
        state.pending_scroll_force = False
        if not force and not state.auto_scroll_enabled:
            return
        QTimer.singleShot(0, lambda sid=session_id, must=force: self._finalize_session_scroll(sid, must))

    def load_more_history(self, session_id=None):
        state = self.get_session(session_id)
        if state is None:
            state = self.get_current_session()
        if not state: return
        if state.auto_loading_history:
            return
        
        PAGE_SIZE = 20
        total = len(state.messages)
        remaining = total - state.displayed_count
        if remaining <= 0: return
        state.auto_loading_history = True
        try:
            count_to_load = min(PAGE_SIZE, remaining)
            start_idx = total - state.displayed_count - count_to_load
            end_idx = total - state.displayed_count
            
            msgs_to_load = state.messages[start_idx:end_idx]
            
            vbar = state.chat_scroll.verticalScrollBar()
            old_max = vbar.maximum()
            old_val = vbar.value()
            
            self.render_message_batch(msgs_to_load, state.session_id, insert_index=0, animate=False)
            
            self.process_ui_events(force=True)
            new_max = vbar.maximum()
            if old_val <= 5:
                vbar.setValue(0)
            else:
                vbar.setValue(old_val + (new_max - old_max))
            
            state.displayed_count += count_to_load
        finally:
            state.auto_loading_history = False

    def delete_session(self, session_id):
        confirm = QMessageBox.question(self, "确认删除", "确定要删除该会话吗？")
        if confirm != QMessageBox.Yes:
            return
        state = self.sessions.get(session_id)
        if state:
            index = self.session_tabs.indexOf(state.session_widget)
            if index >= 0:
                self.close_session_tab(index)
        try:
            if self.chat_storage.has_conversation(session_id):
                self.chat_storage.delete_conversation(session_id)
        except Exception:
            pass
        try:
            history_path = os.path.join(self.chat_history_dir, f'chat_history_{session_id}.json')
            if os.path.exists(history_path):
                os.remove(history_path)
        except Exception:
            pass
        self.refresh_history_list()

    def render_message_batch(self, messages, session_id, insert_index=None, animate=True):
        state = self.get_session(session_id)
        if not state: return
        
        current_idx = insert_index
        backup_last_agent = state.last_agent_bubble
        state.last_agent_bubble = None 
        active_agent_bubble = None
        pending_content_parts = []
        pending_struct_parts = []
        tool_meta_by_id = {}
        for full_msg in state.messages or []:
            if full_msg.get("role") != "assistant":
                continue
            for tc in full_msg.get("tool_calls") or []:
                call_id = tc.get("id")
                func = tc.get("function") or {}
                if not call_id:
                    continue
                args = func.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                tool_meta_by_id[call_id] = {
                    "name": func.get("name") or "unknown_tool",
                    "args": args if args is not None else {}
                }

        def finalize_active_bubble():
            nonlocal active_agent_bubble, pending_content_parts, pending_struct_parts
            if not active_agent_bubble:
                return
            
            final_content = ""
            if pending_content_parts:
                final_content = "\n\n".join(pending_content_parts)
                active_agent_bubble.set_main_content(final_content, content_parts=pending_struct_parts, final=True)
            elif pending_struct_parts:
                text_parts = []
                for part in pending_struct_parts:
                    if isinstance(part, dict) and (part.get("type") or "").strip().lower() == "text":
                        text_value = part.get("text") or ""
                        if text_value.strip():
                            text_parts.append(text_value.strip())
                final_content = "\n\n".join(text_parts)
                active_agent_bubble.set_main_content(final_content, content_parts=pending_struct_parts, final=True)
            
            # Intelligent Fallback: Check if content is empty
            # If so, check if tools were executed
            current_text = active_agent_bubble.content_edit.toPlainText().strip() if hasattr(active_agent_bubble, "content_edit") else ""
            if not final_content and not current_text:
                has_tools = False
                if hasattr(active_agent_bubble, 'think_container_layout'):
                     has_tools = active_agent_bubble.think_container_layout.count() > 0
                
                if has_tools:
                    active_agent_bubble.set_main_content("任务已处理完成，请查看上方思考过程", final=True)

            active_agent_bubble.update_thinking(duration=None, is_final=True)
            active_agent_bubble = None
            pending_content_parts = []
            pending_struct_parts = []
        
        for msg in messages:
            role = msg.get('role')
            content = msg.get('content')
            reasoning = msg.get('reasoning')
            
            if role == 'user':
                finalize_active_bubble()
                self.add_chat_bubble('User', content, index=current_idx, animate=animate)
                if current_idx is not None: current_idx += 1
                state.last_agent_bubble = None
                
            elif role == 'assistant':
                if not active_agent_bubble and (content or reasoning or msg.get('tool_calls')):
                    active_agent_bubble = self.add_chat_bubble('Agent', "", thinking=None, index=current_idx, animate=animate)
                    if current_idx is not None: current_idx += 1
                    state.last_agent_bubble = active_agent_bubble
                if reasoning:
                    active_agent_bubble.update_thinking(reasoning)
                if content:
                    pending_content_parts.append(content)
                if isinstance(msg.get("content_parts"), list):
                    pending_struct_parts.extend(msg.get("content_parts") or [])
                
                tool_calls = msg.get('tool_calls')
                if tool_calls:
                    for tc in tool_calls:
                        t_id = tc.get('id')
                        func = tc.get('function', {})
                        t_name = func.get('name')
                        t_args = func.get('arguments')
                        if isinstance(t_args, str):
                            try:
                                t_args = json.loads(t_args)
                            except Exception:
                                pass
                        self.add_tool_card({
                            'id': t_id,
                            'name': t_name,
                            'args': t_args
                        }, session_id=session_id, index=current_idx, animate=animate)
                        if not active_agent_bubble and current_idx is not None:
                            current_idx += 1

            elif role == 'tool':
                t_id = msg.get('tool_call_id')
                t_result = content
                if t_id:
                    if t_id not in state.tool_cards and t_id in tool_meta_by_id:
                        meta = tool_meta_by_id[t_id]
                        self.add_tool_card({
                            'id': t_id,
                            'name': meta.get('name') or 'unknown_tool',
                            'args': meta.get('args') if meta.get('args') is not None else {}
                        }, session_id=session_id, index=current_idx, animate=animate)
                        if current_idx is not None:
                            current_idx += 1
                    if t_id in state.tool_cards:
                        self.update_tool_card({
                            'id': t_id,
                            'result': t_result,
                            'meta': msg.get('meta')
                        }, session_id=session_id)
                    
        finalize_active_bubble()
        if insert_index is not None:
             state.last_agent_bubble = backup_last_agent

    def _normalize_and_persist_session_messages(self, session_id, messages, force_persist=False, existing_meta=None):
        source_messages = messages if isinstance(messages, list) else []
        meta = existing_meta if isinstance(existing_meta, dict) else {}
        try:
            current_version = int(meta.get("history_migration_version") or 0)
        except Exception:
            current_version = 0
        if not force_persist and current_version >= HISTORY_MIGRATION_VERSION:
            return source_messages
        try:
            normalized_messages = repair_tool_call_sequence(source_messages)
        except Exception:
            normalized_messages = source_messages
        deduped_messages = []
        for msg in normalized_messages:
            if (
                deduped_messages
                and isinstance(msg, dict)
                and isinstance(deduped_messages[-1], dict)
                and msg.get("role") == "user"
                and deduped_messages[-1].get("role") == "user"
                and (msg.get("content") or "") == (deduped_messages[-1].get("content") or "")
            ):
                continue
            deduped_messages.append(msg)
        normalized_messages = deduped_messages

        changed = False
        try:
            changed = json.dumps(source_messages, ensure_ascii=False, sort_keys=True) != json.dumps(normalized_messages, ensure_ascii=False, sort_keys=True)
        except Exception:
            changed = normalized_messages != source_messages

        if force_persist or changed or current_version < HISTORY_MIGRATION_VERSION:
            title = self._compute_session_title(normalized_messages)
            merged_meta = dict(meta)
            if self.workspace_dir:
                merged_meta["workspace_dir"] = self.workspace_dir
            merged_meta["history_migration_version"] = HISTORY_MIGRATION_VERSION
            if session_id in self.sessions:
                merged_meta.update(self._session_plan_meta(self.sessions.get(session_id)))
            try:
                self.chat_storage.save_conversation(session_id, normalized_messages, title=title, meta=merged_meta)
            except Exception as e:
                print(f"Error migrating session messages: {e}")

        return normalized_messages

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
        state.pending_tool_results = {}
        state.current_content_buffer = ""
        state.current_thinking_buffer = ""
        state.pending_thinking_delta = ""
        state.temp_thinking_bubble = None
        state.last_agent_bubble = None
        state.llm_worker = None
        state.auto_scroll_enabled = True
        state.pending_scroll_force = False
        state.active_turn_id = 0
        state.completed_turn_id = 0
        state.active_skills_label.setText("本次会话使用的功能: ")
        state.displayed_count = 0
        state.load_more_btn = None
        state.plan_mode_enabled = False
        state.plan_config = json_copy(DEFAULT_PLAN_CONFIG, dict(DEFAULT_PLAN_CONFIG))
        state.plan_phase = PLAN_MODE_DISABLED
        state.plan_protocol_version = PLAN_PROTOCOL_VERSION
        state.plan_mode_state = PLAN_MODE_EXPLORING
        state.plan_document = ""
        state.pending_plan_questions = []

        loaded_from_json = False
        conversation_meta = {}
        conversation_record = None
        try:
            conversation_meta = self.chat_storage.get_conversation_meta(session_id)
        except Exception:
            conversation_meta = {}
        try:
            conversation_record = self.chat_storage.get_conversation_record(session_id)
        except Exception:
            conversation_record = None
        state.session_status = (
            (conversation_record or {}).get("status")
            or conversation_meta.get("session_status")
            or "draft"
        )
        state.run_phase = conversation_meta.get("run_phase") or "Idle"
        state.has_file_changes = bool(conversation_meta.get("has_file_changes"))
        state.plan_mode_enabled = bool(conversation_meta.get("plan_mode_enabled"))
        state.plan_config = normalize_plan_config(conversation_meta.get("plan_config"))
        state.plan_protocol_version = int(conversation_meta.get("plan_protocol_version") or PLAN_PROTOCOL_VERSION)
        state.plan_mode_state = normalize_plan_phase(
            conversation_meta.get("plan_mode_state"),
            default=PLAN_MODE_EXPLORING,
        )
        state.plan_document = str(conversation_meta.get("plan_document") or "").strip()
        state.pending_plan_questions = normalize_pending_plan_questions(
            conversation_meta.get("pending_plan_questions")
        )
        state.plan_phase = normalize_plan_phase(
            conversation_meta.get("plan_phase"),
            default=derive_plan_phase(
                state.plan_mode_enabled,
                state.plan_mode_state,
                state.plan_document,
            ),
        )
        state.changed_files = []
        state.step_records = []
        state.persisted_agents = []
        state.sub_agent_events = []
        state.messages = self.chat_storage.get_messages(session_id)
        try:
            state.persisted_agents = self.chat_storage.list_agents(session_id)
        except Exception:
            state.persisted_agents = []
        for item in state.persisted_agents or []:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("id") or ""
            if not agent_id:
                continue
            status = item.get("status") or "closed"
            summary = item.get("last_result") or item.get("last_error") or ""
            state.sub_agent_events.append(
                {
                    "agent_id": agent_id,
                    "agent_name": item.get("name") or "",
                    "status": status,
                    "content": summary,
                    "ts": int(item.get("updated_at") or time.time()),
                }
            )
        if not state.messages:
            history_path = os.path.join(self.chat_history_dir, f'chat_history_{session_id}.json')
            if os.path.exists(history_path):
                try:
                    with open(history_path, 'r', encoding='utf-8') as f:
                        state.messages = json.load(f)
                    loaded_from_json = True
                except Exception as e:
                    print(f"Error loading session: {e}")

        if state.messages:
            state.messages = self._normalize_and_persist_session_messages(
                session_id,
                state.messages,
                force_persist=loaded_from_json,
                existing_meta=conversation_meta
            )

        if state.messages:
            PAGE_SIZE = 20
            total = len(state.messages)
            start_idx = max(0, total - PAGE_SIZE)
            
            display_msgs = state.messages[start_idx:]
            state.displayed_count = len(display_msgs)
            
            self.render_message_batch(display_msgs, session_id, animate=False)
        
        # Restore Empty State if no messages
        if len(state.messages) == 0:
            empty_state = EmptyStateWidget(self)
            state.chat_layout.insertWidget(0, empty_state)
            state.empty_state = empty_state

        self.update_session_tab_title(session_id)
        self.refresh_history_list()
        self.refresh_change_list(session_id)
        self.refresh_step_list(session_id)
        self.refresh_plan_view(session_id)
        self.normalize_session_ui(self.get_current_session())
        if session_id == self.current_session_id:
            self._render_sub_agent_monitor_for_state(state)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # Check if it's a folder
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                if hasattr(self, 'drag_overlay'):
                    self.drag_overlay.show()
                    self.drag_overlay.raise_()
                event.acceptProposedAction()
    
    def dragLeaveEvent(self, event):
        if hasattr(self, 'drag_overlay'):
            self.drag_overlay.hide()
        super().dragLeaveEvent(event)
            
    def dropEvent(self, event):
        if hasattr(self, 'drag_overlay'):
            self.drag_overlay.hide()
            
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

    def save_chat_history(self, session_id=None):
        state = self.get_session(session_id)
        if not state:
            return
        has_plan_state = bool(
            state.plan_mode_enabled
            or bool(getattr(state, "plan_document", ""))
            or bool(getattr(state, "pending_plan_questions", []))
        )
        if not state.messages and not has_plan_state:
            return
        title = self._compute_session_title(state.messages) if state.messages else "新任务"
        meta = {}
        try:
            meta = self.chat_storage.get_conversation_meta(state.session_id)
        except Exception:
            meta = {}
        if self.workspace_dir:
            meta["workspace_dir"] = self.workspace_dir
        meta["run_phase"] = getattr(state, "run_phase", "Idle")
        meta["session_status"] = getattr(state, "session_status", "draft")
        meta["has_file_changes"] = bool(getattr(state, "has_file_changes", False))
        meta.update(self._session_plan_meta(state))
        try:
            self.chat_storage.save_conversation(
                state.session_id,
                state.messages,
                title=title,
                status=getattr(state, "session_status", "draft"),
                meta=meta,
            )
        except Exception:
            pass

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
        self.config_manager.set("default_workspace", directory)
        self.update_recent_workspaces(directory)
        self.update_ui_state_for_workspace()
        
        if hasattr(self, 'file_model'):
            self.file_model.setRootPath(directory)
            self.file_tree.setRootIndex(self.file_model.index(directory))
            self.right_sidebar.setVisible(True)
            self.right_tabs.setCurrentIndex(0)

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

    def copy_tool_result(self):
        text = self.td_result_edit.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.td_copy_result_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.td_copy_result_btn.setText("复制结果"))

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
        self.refresh_context_badges()
        self.update_ui_state_for_workspace()

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

    def _disconnect_worker_signals(self, worker):
        if not worker:
            return
        for signal_name in (
            "finished_signal",
            "thinking_signal",
            "content_signal",
            "tool_call_signal",
            "tool_result_signal",
            "observability_signal",
            "output_signal",
            "interaction_signal",
            "agent_state_signal",
            "step_signal",
            "skill_used_signal",
            "input_request_signal",
        ):
            signal = getattr(worker, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect()
            except Exception:
                pass

    def _keep_detached_worker(self, worker):
        if not worker:
            return
        self._detached_workers.append(worker)

        def _cleanup():
            try:
                if worker in self._detached_workers:
                    self._detached_workers.remove(worker)
            except Exception:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass

        try:
            worker.finished.connect(_cleanup)
        except Exception:
            QTimer.singleShot(3000, _cleanup)

    def _stop_live_subagents(self, state, force=True):
        manager = None
        reason = "当前任务已停止，子 Agent 被终止。" if force else "当前任务已结束，子 Agent 已关闭。"
        try:
            if state.llm_worker and getattr(state.llm_worker, "agent_manager", None):
                manager = state.llm_worker.agent_manager
            elif state.session_id:
                manager = get_agent_manager_registry().get_session_manager(
                    state.session_id,
                    chat_storage=self.chat_storage,
                    config_manager=self.config_manager,
                    workspace_dir=self.workspace_dir,
                )
        except Exception:
            manager = None
        if not manager:
            return
        try:
            live_agents = manager.list_agent_summaries(status_filter=list(AGENT_LIVE_STATUSES))
        except Exception:
            live_agents = []
        for item in live_agents:
            agent_id = item.get("id")
            if not agent_id:
                continue
            try:
                manager.close_agent(agent_id, force=bool(force), reason=reason)
            except Exception:
                continue

    def stop_agent(self):
        state = self.get_current_session()
        if not state: return
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.stop_thinking_timers()
        if state.last_agent_bubble and state.last_agent_bubble is not state.temp_thinking_bubble:
            state.last_agent_bubble.stop_thinking_timers()
        if not state.daemon_running:
            self._stop_live_subagents(state, force=True)
        if self.daemon_client and state.daemon_running and state.session_id:
            try:
                self.daemon_client.stop_session(state.session_id)
            except Exception:
                pass
        if state.daemon_worker:
            daemon_worker = state.daemon_worker
            self._disconnect_worker_signals(daemon_worker)
            try:
                daemon_worker.abort()
            except Exception:
                pass
            daemon_worker.wait(1200)
            if daemon_worker.isRunning():
                self._keep_detached_worker(daemon_worker)
        if state.llm_worker:
            llm_worker = state.llm_worker
            self._disconnect_worker_signals(llm_worker)
            try:
                llm_worker.stop()
            except Exception:
                pass
            llm_worker.wait(1200)
            if llm_worker.isRunning():
                self._keep_detached_worker(llm_worker)
        if state.code_worker:
            code_worker = state.code_worker
            self._disconnect_worker_signals(code_worker)
            try:
                code_worker.stop()
            except Exception:
                pass
            code_worker.wait(1200)
            if code_worker.isRunning():
                self._keep_detached_worker(code_worker)
        state.active_turn_id += 1
        state.completed_turn_id = max(state.completed_turn_id, state.active_turn_id)
        state.daemon_worker = None
        state.daemon_running = False
        state.llm_worker = None
        state.code_worker = None
        self.code_worker = None
        partial_content = (state.current_content_buffer or "").strip()
        partial_thinking = (state.current_thinking_buffer or "").strip()
        if partial_content or partial_thinking:
            existing_content = ""
            if state.messages and isinstance(state.messages[-1], dict) and state.messages[-1].get("role") == "assistant":
                existing_content = (state.messages[-1].get("content") or "").strip()
            stop_text = "⚠️ 任务已停止（保留未完成内容）"
            if partial_content:
                stop_text = f"{partial_content}\n\n{stop_text}"
            if existing_content != stop_text:
                state.messages.append({
                    "role": "assistant",
                    "content": stop_text,
                    "reasoning": partial_thinking,
                    "content_parts": [{"type": "text", "text": stop_text}]
                })
                self.save_chat_history(session_id=state.session_id)
        state.current_content_buffer = ""
        state.current_thinking_buffer = ""
        self.add_system_toast("已强制停止当前任务", "warning", session_id=state.session_id)
        self.set_session_phase("Interrupted", state.session_id)
        self.set_session_status("interrupted", state.session_id, save=True)
        self.refresh_step_list(state.session_id)
        self.refresh_change_list(state.session_id)
        self.normalize_session_ui(state)

    def on_action_clicked(self):
        state = self.get_current_session()
        if not state: return
        
        # Check if running
        is_running = (state.llm_worker and state.llm_worker.isRunning()) or \
                     (state.code_worker and state.code_worker.isRunning()) or \
                     state.daemon_running
        
        if is_running:
            self.stop_agent()
        else:
            self.handle_send()

    def on_plan_mode_toggled(self, checked):
        state = self.get_current_session()
        if not state:
            return
        state.plan_mode_enabled = bool(checked)
        state.plan_config = normalize_plan_config(state.plan_config)
        if not state.plan_mode_enabled:
            state.plan_phase = PLAN_MODE_DISABLED
            state.plan_mode_state = PLAN_MODE_EXPLORING
            state.plan_document = ""
            state.pending_plan_questions = []
        else:
            state.plan_mode_state = PLAN_MODE_EXPLORING
            state.plan_phase = derive_plan_phase(
                True,
                state.plan_mode_state,
                state.plan_document,
            )
        self.refresh_plan_controls(state.session_id)
        self.refresh_plan_view(state.session_id)
        self.save_chat_history(session_id=state.session_id)
        self.normalize_session_ui(state)

    def _build_run_context(self, state, mode):
        return normalize_run_context(
            {
                "mode": mode,
                "plan_config": normalize_plan_config(getattr(state, "plan_config", DEFAULT_PLAN_CONFIG)),
                "plan_protocol_version": int(getattr(state, "plan_protocol_version", PLAN_PROTOCOL_VERSION) or PLAN_PROTOCOL_VERSION),
                "plan_mode_state": normalize_plan_phase(
                    getattr(state, "plan_mode_state", PLAN_MODE_EXPLORING),
                    default=PLAN_MODE_EXPLORING,
                ),
                "plan_document": str(getattr(state, "plan_document", "") or "").strip(),
                "pending_plan_questions": normalize_pending_plan_questions(
                    getattr(state, "pending_plan_questions", [])
                ),
            }
        )

    def handle_send(self):
        if not self.workspace_dir:
            QMessageBox.warning(self, "提示", "请先选择一个工作区目录！")
            return
        user_text = self.input_field.toPlainText().strip()
        if not user_text: return
        now = time.time()
        if user_text == self._last_submit_text and (now - self._last_submit_ts) < 0.8:
            return
        self._last_submit_text = user_text
        self._last_submit_ts = now

        self.add_chat_bubble("User", user_text, animate=False, force_scroll=True)
        self.input_field.clear()
        
        state = self.get_current_session()
        if not state: return
        state.step_records = []
        state.changed_files = []
        state.has_file_changes = False
        state.pending_tool_results = {}
        state.observability_events = []
        state.system_prompt_text = ""
        state.system_prompt_appends = []
        self.refresh_change_list(state.session_id)
        self.refresh_step_list(state.session_id)
        self.refresh_observability_view(state.session_id)
        self.right_sidebar.setVisible(True)
        self.right_tabs.setCurrentIndex(3)
        self.set_session_phase("Preparing", state.session_id)
        self.set_session_status("running", state.session_id)
        state.active_turn_id += 1
        current_turn_id = state.active_turn_id
        state.messages.append({"role": "user", "content": user_text})
        run_mode = RUN_MODE_EXECUTION
        if state.plan_mode_enabled:
            state.plan_phase = PLAN_MODE_EXPLORING
            state.plan_mode_state = PLAN_MODE_EXPLORING
            run_mode = RUN_MODE_PLANNING
        # Keep rendered-count in sync for live messages; otherwise load-more
        # may re-render freshly added items as if they were unseen history.
        state.displayed_count = min(len(state.messages), state.displayed_count + 1)
        self.save_chat_history(session_id=state.session_id)
        self.update_session_tab_title(state.session_id)
        self.try_connect_daemon(allow_start=True, retries=4)
        run_context = self._build_run_context(state, run_mode)
        if self.daemon_available:
            self.process_daemon_logic(user_text, turn_id=current_turn_id, run_context=run_context)
        else:
            self.process_agent_logic(user_text, turn_id=current_turn_id, run_context=run_context)

    def show_tool_details(self, tool_id, args, result, meta=None, switch_tab=True):
        # 1. Update selection state in UI
        state = self.get_current_session()
        selected_card = None
        if state:
            for tid, card in state.tool_cards.items():
                card.set_selected(tid == tool_id)
                if meta is None and tid == tool_id:
                    meta = card.meta
                if tid == tool_id:
                    selected_card = card

        self.current_selected_tool_id = tool_id

        # 2. Open Sidebar & Switch Tab
        if not self.right_sidebar.isVisible():
            self.right_sidebar.setVisible(True)
            
        if switch_tab:
            self.right_tabs.setCurrentIndex(3)
        
        # 3. Update Content
        self.td_info_label.setText(f"工具 ID: {tool_id}")
        
        # Update Meta Label
        meta_text = ""
        if isinstance(meta, dict):
            start_time = meta.get("start_time")
            duration = meta.get("duration")
            try:
                if start_time is not None and start_time != "":
                    start_ts = float(start_time)
                    st_str = datetime.fromtimestamp(start_ts).strftime('%H:%M:%S')
                    meta_text += f"Time: {st_str}  "
            except Exception:
                pass
            try:
                if duration is not None and duration != "":
                    duration_sec = float(duration)
                    meta_text += f"Duration: {duration_sec:.2f}s"
            except Exception:
                pass
        self.td_meta_label.setText(meta_text)
        self.td_meta_label.setVisible(bool(meta_text))
        
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
            if selected_card and getattr(selected_card, "result_obj", None) is not None:
                res_text = json.dumps(selected_card.result_obj, indent=2, ensure_ascii=False)
            elif isinstance(result, str):
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

        if not (res_text or "").strip() and selected_card and not getattr(selected_card, "is_finished", False):
            res_text = "(Running...)"
             
        self.td_result_edit.setPlainText(res_text)

    def add_tool_card(self, data, session_id=None, index=None, animate=True):
        meta = data.get('meta') or {}
        card = ToolCallCard(data['name'], data['args'], data['id'], meta=meta)
        card.clicked.connect(self.show_tool_details)
        
        state = self.get_session(session_id)
        if not state: return
        args_obj = data.get("args")
        if isinstance(args_obj, str):
            try:
                args_obj = json.loads(args_obj)
            except Exception:
                args_obj = {"raw": data.get("args")}
        title, summary = summarize_tool_action(data.get("name"), args_obj if isinstance(args_obj, dict) else {})
        related_files = extract_related_paths(data.get("name"), args_obj if isinstance(args_obj, dict) else {})
        record = {
            "tool_id": data["id"],
            "tool_name": data.get("name"),
            "display_title": title,
            "summary": summary,
            "status": "running",
            "duration": meta.get("duration"),
            "related_files": related_files,
        }
        state.step_records = [r for r in state.step_records if r.get("tool_id") != data["id"]]
        state.step_records.append(record)
        if state.plan_mode_enabled and data.get("name") == "request_user_input":
            pending_questions = self._extract_pending_plan_questions_from_args(
                args_obj if isinstance(args_obj, dict) else {}
            )
            if pending_questions:
                state.pending_plan_questions = pending_questions
                state.plan_mode_state = PLAN_MODE_AWAITING_USER_INPUT
                state.plan_phase = PLAN_MODE_AWAITING_USER_INPUT
                self.refresh_plan_view(state.session_id)
        if related_files:
            for path in related_files:
                state.changed_files.append({"path": path, "type": "related", "summary": summary})
        state.tool_cards[data['id']] = card
        has_pending_result = data['id'] in state.pending_tool_results
        pending_result = state.pending_tool_results.pop(data['id'], None)
        
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

            if index is not None:
                state.chat_layout.insertWidget(index, wrapper)
            else:
                state.chat_layout.insertWidget(state.chat_layout.count() - 1, wrapper)
            self.process_ui_events(force=False)
            
        if has_pending_result:
            self.update_tool_card({
                "id": data["id"],
                "result": pending_result.get("result", ""),
                "meta": pending_result.get("meta"),
                "result_obj": pending_result.get("result_obj"),
            }, session_id=session_id)

        if state.plan_mode_enabled:
            self.set_session_phase("Planning", state.session_id)
        else:
            self.set_session_phase("Executing", state.session_id)
        self.refresh_step_list(state.session_id)
        self.process_ui_events(force=False)
        self.request_session_scroll_to_bottom(state.session_id, force=False)

    def update_tool_card(self, data, session_id=None):
        tool_id = data['id']
        result = data['result']
        meta = data.get('meta')
        state = self.get_session(session_id)
        if not state: return
        if tool_id not in state.tool_cards:
            state.pending_tool_results[tool_id] = {
                "result": result,
                "meta": meta,
                "result_obj": data.get("result_obj"),
            }
            return

        card = state.tool_cards[tool_id]
        card.set_result(result, result_obj=data.get("result_obj"))
        if meta:
            card.meta.update(meta)
        result_obj = data.get("result_obj")
        if state.plan_mode_enabled and isinstance(result_obj, dict) and result_obj.get("source_tool") == "request_user_input":
            state.pending_plan_questions = []
            state.plan_mode_state = PLAN_MODE_EXPLORING
            state.plan_phase = PLAN_MODE_EXPLORING
            self.refresh_plan_view(state.session_id)
        for record in state.step_records:
            if record.get("tool_id") != tool_id:
                continue
            has_result = bool((result or "").strip()) or result_obj is not None
            record["status"] = "done" if has_result else "running"
            duration = (meta or {}).get("duration") if isinstance(meta, dict) else None
            if isinstance(duration, (int, float)):
                record["duration"] = duration
            if result_obj is not None:
                preview = str(result_obj.get("content") or json.dumps(result_obj, ensure_ascii=False)).strip().replace("\n", " ")
                record["summary"] = preview[:120]
            elif (result or "").strip():
                preview = str(result).strip().replace("\n", " ")
                record["summary"] = preview[:120]
            for path in record.get("related_files") or []:
                state.changed_files.append(
                    {
                        "path": path,
                        "type": "updated",
                        "summary": record.get("display_title") or record.get("summary") or "Updated by task",
                    }
                )
                state.has_file_changes = True
            break
        self.refresh_step_list(state.session_id)
        self.refresh_change_list(state.session_id)
        
        # [Optimization] Real-time refresh if currently viewing this tool
        if (hasattr(self, 'current_selected_tool_id') and 
            self.current_selected_tool_id == tool_id and 
            self.right_sidebar.isVisible() and 
            self.right_tabs.currentIndex() == 3):
            
            self.show_tool_details(tool_id, card.args, result, meta=card.meta, switch_tab=False)
        self.process_ui_events(force=True)

    def add_chat_bubble(self, role, text, thinking=None, duration=None, index=None, animate=True, force_scroll=False):
        state = self.get_current_session()
        if not state: return
        
        # Hide Empty State if this is the first message
        if state.empty_state and state.empty_state.isVisible():
            state.empty_state.setVisible(False)
            
        # Throttling Animation
        import time
        now = time.time()
        if animate and self.last_message_time and (now - self.last_message_time) < 0.5:
            animate = False
        self.last_message_time = now
            
        bubble = ChatBubble(role, text, thinking, duration)
        
        if index is not None:
            state.chat_layout.insertWidget(index, bubble)
        else:
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, bubble)
        self.process_ui_events(force=False)
        
        # Keep latest message in view when appending.
        if index is None:
            self.request_session_scroll_to_bottom(state.session_id, force=force_scroll)
            
        return bubble

    def add_system_toast(self, text, type="info", session_id=None, auto_close_ms=None):
        state = self.get_session(session_id)
        if not state: return
        toast = SystemToast(text, type)
        state.chat_layout.insertWidget(state.chat_layout.count() - 1, toast)
        self.request_session_scroll_to_bottom(state.session_id, force=False)
        self.process_ui_events(force=True)
        if auto_close_ms: QTimer.singleShot(auto_close_ms, toast.deleteLater)

    def append_log(self, text):
        print(f"[Log] {text}")

    def process_agent_logic(self, user_text, turn_id=None, run_context=None):
        state = self.get_current_session()
        if not state: return
        if turn_id is None:
            turn_id = state.active_turn_id
        state.current_content_buffer = ""
        state.current_thinking_buffer = ""
        self.set_session_phase("Preparing", state.session_id)
        if state.session_id == self.current_session_id:
            self.current_content_buffer = ""
            self.current_thinking_buffer = ""
        
        # Insert "Thinking" bubble
        state.temp_thinking_bubble = ChatBubble("agent", "", thinking="...")
        state.chat_layout.insertWidget(state.chat_layout.count()-1, state.temp_thinking_bubble)
        self.request_session_scroll_to_bottom(state.session_id, force=True)
        self.process_ui_events(force=True)

        state.llm_worker = LLMWorker(
            state.messages,
            self.config_manager,
            self.workspace_dir,
            session_id=state.session_id,
            run_context=run_context,
        )
        if state.session_id == self.current_session_id:
            self.llm_worker = state.llm_worker
        session_id = state.session_id
        state.llm_worker.finished_signal.connect(lambda result, sid=session_id, tid=turn_id: self.handle_llm_response(result, sid, tid))
        state.llm_worker.content_signal.connect(lambda text, sid=session_id, tid=turn_id: self.handle_content_signal(text, sid, tid))
        state.llm_worker.step_signal.connect(self.append_log)
        state.llm_worker.thinking_signal.connect(lambda text, sid=session_id, tid=turn_id: self.handle_thinking_signal(text, sid, tid))
        state.llm_worker.skill_used_signal.connect(lambda name, sid=session_id: self.handle_skill_used(name, sid))
        state.llm_worker.tool_call_signal.connect(lambda data, sid=session_id: self.add_tool_card(data, sid))
        state.llm_worker.tool_result_signal.connect(lambda data, sid=session_id: self.update_tool_card(data, sid))
        state.llm_worker.observability_signal.connect(lambda data, sid=session_id: self.handle_observability_event(data, sid))
        state.llm_worker.output_signal.connect(lambda text, sid=session_id: self.handle_worker_output(text, sid))
        state.llm_worker.agent_state_signal.connect(lambda data, sid=session_id: self.handle_agent_state(data, sid))
        state.llm_worker.start()
        
        if state.session_id == self.current_session_id:
             self.normalize_session_ui(state)

    def process_daemon_logic(self, user_text, turn_id=None, run_context=None):
        state = self.get_current_session()
        if not state: return
        if turn_id is None:
            turn_id = state.active_turn_id
        state.current_content_buffer = ""
        state.current_thinking_buffer = ""
        self.set_session_phase("Preparing", state.session_id)
        if state.session_id == self.current_session_id:
            self.current_content_buffer = ""
            self.current_thinking_buffer = ""
        state.temp_thinking_bubble = ChatBubble("agent", "", thinking="...")
        state.chat_layout.insertWidget(state.chat_layout.count()-1, state.temp_thinking_bubble)
        self.request_session_scroll_to_bottom(state.session_id, force=True)
        self.process_ui_events(force=True)
        state.daemon_running = True
        state.daemon_worker = DaemonStreamWorker(
            self.daemon_client,
            state.session_id,
            user_text,
            self.workspace_dir,
            run_context=run_context,
        )
        state.daemon_worker.finished_signal.connect(lambda result, sid=state.session_id, tid=turn_id: self.handle_daemon_response(result, sid, tid))
        state.daemon_worker.thinking_signal.connect(lambda text, sid=state.session_id, tid=turn_id: self.handle_thinking_signal(text, sid, tid))
        state.daemon_worker.content_signal.connect(lambda text, sid=state.session_id, tid=turn_id: self.handle_content_signal(text, sid, tid))
        state.daemon_worker.tool_call_signal.connect(lambda data, sid=state.session_id: self.add_tool_card(data, sid))
        state.daemon_worker.tool_result_signal.connect(lambda data, sid=state.session_id: self.update_tool_card(data, sid))
        state.daemon_worker.observability_signal.connect(lambda data, sid=state.session_id: self.handle_observability_event(data, sid))
        state.daemon_worker.agent_state_signal.connect(lambda data, sid=state.session_id: self.handle_agent_state(data, sid))
        state.daemon_worker.interaction_signal.connect(lambda req, sid=state.session_id: self.handle_daemon_interaction_request(req, sid))
        state.daemon_worker.start()
        if state.session_id == self.current_session_id:
            self.normalize_session_ui(state)

    def handle_daemon_response(self, result, session_id=None, turn_id=None):
        state = self.get_session(session_id)
        if not state: return
        if turn_id is not None and turn_id != state.active_turn_id:
            return
        state.daemon_running = False
        state.daemon_worker = None
        if "error" in result and str(result.get("error", "")).lower().find("daemon") >= 0:
            self.daemon_available = False
        if isinstance(result, dict) and not result.get("_streamed"):
            result["_from_daemon"] = True
        self.handle_llm_response(result, session_id, turn_id)

    def handle_worker_output(self, text, session_id=None):
        self.append_log(f"[Worker] {text}")
        # If it looks like an error, show a toast
        if "error" in text.lower() or "exception" in text.lower() or "fail" in text.lower():
            self.add_system_toast(text, "error", session_id=session_id)

    def _record_sub_agent_event(self, state, data, content):
        if not state or not isinstance(data, dict):
            return
        status = data.get("status") or ""
        text = content or ""
        if status in {"thinking", "content"} and len((text or "").strip()) <= 1:
            return
        event = {
            "agent_id": data.get("agent_id") or "",
            "agent_name": data.get("agent_name") or "",
            "status": status,
            "content": text,
            "ts": int(time.time()),
        }
        if not event["agent_id"]:
            return
        if state.sub_agent_events:
            last = state.sub_agent_events[-1]
            if (
                isinstance(last, dict)
                and last.get("agent_id") == event["agent_id"]
                and last.get("status") == event["status"]
            ):
                merged_text = (last.get("content") or "") + (event.get("content") or "")
                last["content"] = merged_text[-240:]
                last["ts"] = event["ts"]
                return
        state.sub_agent_events.append(event)
        if len(state.sub_agent_events) > 800:
            state.sub_agent_events = state.sub_agent_events[-800:]

    def _render_sub_agent_monitor_for_state(self, state):
        if not state or not hasattr(self, "sub_agent_monitor") or self.sub_agent_monitor is None:
            return
        self.sub_agent_monitor.reset()
        for event in state.sub_agent_events:
            if not isinstance(event, dict):
                continue
            agent_id = event.get("agent_id")
            if not agent_id:
                continue
            status = event.get("status") or "running"
            content = event.get("content") or ""
            self.sub_agent_monitor.update_log(agent_id, content, status, agent_name=event.get("agent_name") or "")

    def handle_agent_state(self, data, session_id=None):
        state = self.get_session(session_id)
        if not state: return
        status = data.get("status")
        if status in {"thinking", "pending", "content", "provider_log"}:
            self.set_session_phase("Analyzing", state.session_id)
        elif status == "tool_use":
            self.set_session_phase("Executing", state.session_id)
        elif status == "provider_error":
            self.set_session_phase("Error", state.session_id)
        
        # Update Tool Card
        tool_call_id = data.get("tool_call_id")
        if tool_call_id and tool_call_id in state.tool_cards:
            card = state.tool_cards[tool_call_id]
            card.update_agent_state(data)

        monitor_content = ""
        if status == "thinking":
            monitor_content = data.get("reasoning_delta") or ""
        elif status == "content":
            monitor_content = data.get("content_delta") or ""
        elif status == "log":
            monitor_content = data.get("log_content") or ""
        elif status == "provider_log":
            monitor_content = data.get("provider_message") or ""
        elif status == "provider_error":
            monitor_content = data.get("provider_message") or data.get("error") or ""
        elif status == "tool_use":
            monitor_content = data.get("task") or ""
        elif status == "pending":
            monitor_content = data.get("task") or ""
        elif status in {"failed", "failed_recovered", "killed"}:
            monitor_content = data.get("error") or data.get("content") or ""
        elif status == "completed":
            monitor_content = data.get("content") or ""
        self._record_sub_agent_event(state, data, monitor_content)
        if session_id == self.current_session_id and hasattr(self, "sub_agent_monitor") and self.sub_agent_monitor:
            agent_id = data.get("agent_id")
            if agent_id:
                self.sub_agent_monitor.update_log(
                    agent_id,
                    monitor_content,
                    status,
                    agent_name=data.get("agent_name") or "",
                )

        # Update Sub-Agent Monitor (PiP in ChatBubble)
        if session_id == self.current_session_id:
            agent_id = data.get("agent_id")
            
            if agent_id and state.last_agent_bubble:
                content = None
                if status == "thinking":
                    content = data.get("reasoning_delta")
                elif status == "content":
                    content = data.get("content_delta")
                elif status == "log":
                    content = data.get("log_content")
                elif status == "provider_log":
                    content = data.get("provider_message")
                elif status == "provider_error":
                    content = data.get("provider_message") or data.get("error")
                elif status == "tool_use":
                    content = data.get("task")
                elif status == "pending":
                    content = f"Task: {data.get('task')}\n"
                elif status in {"running", "active"}:
                    content = "Running...\n"
                elif status == "waiting_input":
                    content = "Waiting for input...\n"
                elif status == "completed":
                    content = "\nDone."
                elif status in {"failed", "failed_recovered"}:
                    content = f"\nFailed: {data.get('content') or data.get('error') or ''}"
                elif status == "closed":
                    detail = (data.get("error") or "").strip()
                    content = f"\nClosed: {detail}" if detail else "\nClosed."
                elif status == "killed":
                    detail = (data.get("error") or "").strip()
                    content = f"\nKilled: {detail}" if detail else "\nKilled."
                    
                if content or status in ["completed", "pending", "running", "active", "waiting_input", "failed", "failed_recovered", "closed", "killed", "content", "provider_log", "provider_error"]:
                    # Update log in bubble
                    if hasattr(state.last_agent_bubble, 'update_sub_agent_log'):
                        state.last_agent_bubble.update_sub_agent_log(agent_id, content, status)
                
                # Update indicator status
                if hasattr(state.last_agent_bubble, 'add_sub_agent_indicator'):
                    state.last_agent_bubble.add_sub_agent_indicator(agent_id, status)

    def handle_content_signal(self, text, session_id=None, turn_id=None):
        state = self.get_session(session_id)
        if not state: return
        if turn_id is not None and turn_id != state.active_turn_id:
            return
        if turn_id is not None and turn_id <= state.completed_turn_id:
            return
        state.current_content_buffer += text
        if state.content_flush_timer and not state.content_flush_timer.isActive():
            state.content_flush_timer.start()
        if state.session_id == self.current_session_id:
            self.current_content_buffer = state.current_content_buffer

    def handle_thinking_signal(self, text, session_id=None, turn_id=None):
        state = self.get_session(session_id)
        if not state: return
        if turn_id is not None and turn_id != state.active_turn_id:
            return
        if turn_id is not None and turn_id <= state.completed_turn_id:
            return
        delta = text or ""
        if delta.strip():
            if state.plan_mode_enabled:
                self.set_session_phase("Planning", state.session_id)
            else:
                self.set_session_phase("Analyzing", state.session_id)
        state.current_thinking_buffer += delta
        state.pending_thinking_delta += delta
        if state.thinking_flush_timer and not state.thinking_flush_timer.isActive():
            state.thinking_flush_timer.start()
        if state.session_id == self.current_session_id:
            self.current_thinking_buffer = state.current_thinking_buffer

    def flush_session_content(self, session_id, final=False):
        state = self.get_session(session_id)
        if not state:
            return
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.set_main_content(state.current_content_buffer, final=final)
        elif state.last_agent_bubble:
            state.last_agent_bubble.set_main_content(state.current_content_buffer, final=final)
        self.request_session_scroll_to_bottom(state.session_id, force=False)

    def flush_session_thinking(self, session_id):
        state = self.get_session(session_id)
        if not state:
            return
        delta = state.pending_thinking_delta
        state.pending_thinking_delta = ""
        if not delta:
            return
        if state.temp_thinking_bubble:
            state.temp_thinking_bubble.update_thinking(delta)
        elif state.last_agent_bubble:
            state.last_agent_bubble.update_thinking(delta)
        self.request_session_scroll_to_bottom(state.session_id, force=False)

    def _message_signature_for_merge(self, message):
        if not isinstance(message, dict):
            return None
        role = message.get("role") or ""
        signature = {
            "role": role,
            "content": message.get("content") or "",
            "tool_call_id": message.get("tool_call_id") or "",
        }
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            normalized_calls = []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                    except Exception:
                        arguments = str(arguments)
                elif arguments is None:
                    arguments = ""
                normalized_calls.append(
                    {
                        "id": tool_call.get("id") or "",
                        "type": tool_call.get("type") or "function",
                        "name": function.get("name") or "",
                        "arguments": arguments,
                    }
                )
            signature["tool_calls"] = normalized_calls
        try:
            return json.dumps(signature, ensure_ascii=False, sort_keys=True)
        except Exception:
            return f"{role}:{signature.get('content', '')}"

    def _merge_generated_messages(self, existing_messages, generated_messages):
        if not isinstance(generated_messages, list):
            return []
        new_messages = [msg for msg in generated_messages if isinstance(msg, dict)]
        if not new_messages:
            return []

        existing_ids = {
            msg.get("id")
            for msg in (existing_messages or [])
            if isinstance(msg, dict) and msg.get("id")
        }
        if existing_ids:
            new_messages = [
                msg for msg in new_messages
                if not msg.get("id") or msg.get("id") not in existing_ids
            ]
            if not new_messages:
                return []

        existing_signatures = [
            sig
            for sig in (self._message_signature_for_merge(msg) for msg in (existing_messages or []))
            if sig is not None
        ]
        new_signatures = [
            sig
            for sig in (self._message_signature_for_merge(msg) for msg in new_messages)
            if sig is not None
        ]

        overlap = 0
        max_overlap = min(len(existing_signatures), len(new_signatures))
        for size in range(max_overlap, 0, -1):
            if existing_signatures[-size:] == new_signatures[:size]:
                overlap = size
                break

        delta_messages = new_messages[overlap:]
        if not delta_messages:
            return []

        deduped_delta = []
        tail_signature = existing_signatures[-1] if existing_signatures else None
        for msg in delta_messages:
            msg_signature = self._message_signature_for_merge(msg)
            if msg_signature is None:
                continue
            if msg_signature == tail_signature:
                continue
            deduped_delta.append(msg)
            tail_signature = msg_signature
        return deduped_delta

    def handle_llm_response(self, result, session_id=None, turn_id=None):
        state = self.get_session(session_id)
        if not state: return
        previous_message_count = len(state.messages)
        if turn_id is not None:
            if turn_id != state.active_turn_id:
                return
            if turn_id <= state.completed_turn_id:
                return
            state.completed_turn_id = turn_id
        if state.content_flush_timer and state.content_flush_timer.isActive():
            state.content_flush_timer.stop()
        if state.thinking_flush_timer and state.thinking_flush_timer.isActive():
            state.thinking_flush_timer.stop()
        self.flush_session_content(state.session_id, final=True)
        self.flush_session_thinking(state.session_id)
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
            bubble.stop_thinking_timers()
            bubble.update_thinking(duration=None, is_final=True)
            bubble.set_main_content(f"⚠️ Error: {result['error']}", final=True)
            self.request_session_scroll_to_bottom(state.session_id, force=False)
            state.current_content_buffer = ""
            state.current_thinking_buffer = ""
            self.set_session_phase("Error", state.session_id)
            self.set_session_status("error", state.session_id, save=True)
            if is_current: self.normalize_session_ui(state)
            return

        reasoning = result.get("reasoning", "")
        content = result.get("content", "")
        content_parts = result.get("content_parts") if isinstance(result.get("content_parts"), list) else []
        role = result.get("role", "assistant")
        duration = result.get("duration", None)
        generated_messages_raw = result.get("generated_messages", [])
        generated_messages = self._merge_generated_messages(
            state.messages,
            generated_messages_raw,
        )

        if not (content or "").strip() and generated_messages:
            for msg in reversed(generated_messages):
                if msg.get("role") == "assistant":
                    msg_content = msg.get("content") or ""
                    if msg_content.strip():
                        content = msg_content
                        if isinstance(msg.get("content_parts"), list):
                            content_parts = msg.get("content_parts") or content_parts
                        if not (reasoning or "").strip():
                            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or reasoning
                        break
        tool_results = {}
        if generated_messages:
            for msg in generated_messages:
                if msg.get("role") == "tool" and msg.get("tool_call_id"):
                    tool_results[msg["tool_call_id"]] = {
                        "result": msg.get("content", ""),
                        "result_obj": msg.get("result_obj"),
                    }
        tool_calls = []
        if result.get("tool_calls"):
            tool_calls.extend(result.get("tool_calls") or [])
        for msg in generated_messages or []:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_calls.extend(msg.get("tool_calls") or [])

        if not (content or "").strip() and not tool_calls:
            content = "任务已处理完成，请查看上方思考过程。"

        has_thinking_text = False
        if bubble.think_container_layout.count() > 0:
            item = bubble.think_container_layout.itemAt(bubble.think_container_layout.count() - 1)
            widget = item.widget()
            if isinstance(widget, AutoResizingLabel):
                has_thinking_text = bool(widget.text().strip())

        should_replay_thinking = (reasoning or "").strip() and not has_thinking_text
        if should_replay_thinking and result.get("_from_daemon"):
            replay_index = 0
            interval_ms = 30
            total_ms = int(max((duration or 0) * 1000, interval_ms))
            chunk_size = max(1, int(len(reasoning) * interval_ms / total_ms))

            timer = QTimer(bubble)
            bubble._thinking_replay_timer = timer

            def _tick():
                nonlocal replay_index
                next_index = min(len(reasoning), replay_index + chunk_size)
                if next_index > replay_index:
                    bubble.update_thinking(reasoning[replay_index:next_index])
                    replay_index = next_index
                if replay_index >= len(reasoning):
                    timer.stop()
                    bubble.update_thinking(duration=duration, is_final=True)
                    bubble.set_main_content(content, content_parts=content_parts, final=True)

            timer.timeout.connect(_tick)
            timer.start(interval_ms)
        elif should_replay_thinking:
            bubble.update_thinking(reasoning, duration=duration, is_final=True)
            bubble.set_main_content(content, content_parts=content_parts, final=True)
        else:
            bubble.update_thinking(duration=duration, is_final=True)
            bubble.set_main_content(content, content_parts=content_parts, final=True)
        self.request_session_scroll_to_bottom(state.session_id, force=False)

        for tc in tool_calls:
            t_id = tc.get("id")
            func = tc.get("function", {})
            t_name = func.get("name")
            t_args = func.get("arguments")
            if t_id and t_id not in state.tool_cards:
                self.add_tool_card({
                    "id": t_id,
                    "name": t_name,
                    "args": t_args
                }, session_id=state.session_id)
            if t_id in tool_results:
                self.update_tool_card({
                    "id": t_id,
                    "result": tool_results[t_id].get("result", ""),
                    "result_obj": tool_results[t_id].get("result_obj"),
                }, session_id=state.session_id)

        if generated_messages:
            state.messages.extend(generated_messages)
        elif isinstance(generated_messages_raw, list) and generated_messages_raw:
            pass
        else:
            state.messages.append({
                "role": role, 
                "content": content,
                "reasoning": reasoning,
                "content_parts": content_parts
            })
        state.messages = self.chat_storage.normalize_messages(state.messages)
        if len(state.messages) > previous_message_count:
            newly_rendered = len(state.messages) - previous_message_count
            state.displayed_count = min(len(state.messages), state.displayed_count + newly_rendered)
        self.save_chat_history(session_id=state.session_id)
        state.current_content_buffer = ""
        state.current_thinking_buffer = ""
        self.update_session_tab_title(state.session_id)
        if state.plan_mode_enabled:
            phase_text = "Plan Ready" if normalize_plan_phase(state.plan_phase) == PLAN_MODE_READY_TO_PRESENT else "Planning"
            self.set_session_phase(phase_text, state.session_id)
        else:
            self.set_session_phase("Wrapping up", state.session_id)

        if state.plan_mode_enabled:
            proposed_plan = self._extract_proposed_plan(content)
            if proposed_plan:
                state.plan_document = proposed_plan
                state.pending_plan_questions = []
                state.plan_mode_state = PLAN_MODE_READY_TO_PRESENT
                state.plan_phase = PLAN_MODE_READY_TO_PRESENT
                self.refresh_plan_view(state.session_id)

        code_match = re.search(r'```\s*python(.*?)```', content, re.DOTALL | re.IGNORECASE)
        should_run_code_block = not state.plan_mode_enabled
        if code_match and should_run_code_block:
            code_block = code_match.group(1).strip()
            self.append_log("System: 检测到代码块，准备执行...")
            self.set_session_phase("Executing", state.session_id)
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
            if state.plan_mode_enabled:
                self.set_session_status("draft", state.session_id, save=True)
            else:
                self.set_session_phase("Completed", state.session_id)
                self.set_session_status("completed", state.session_id, save=True)
            self.refresh_step_list(state.session_id)
            self.refresh_change_list(state.session_id)
            self.refresh_plan_view(state.session_id)
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
            self.process_ui_events()
            self.request_session_scroll_to_bottom(state.session_id, force=False)

    def handle_code_finished(self, session_id=None):
        state = self.get_session(session_id)
        if state: state.code_worker = None
        if state:
            self.set_session_phase("Completed", state.session_id)
            self.set_session_status("completed", state.session_id, save=True)
            self.refresh_step_list(state.session_id)
            self.refresh_change_list(state.session_id)
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
    if "--daemon" in sys.argv:
        port = DEFAULT_PORT
        for arg in sys.argv:
            if arg.startswith("--daemon-port="):
                try:
                    port = int(arg.split("=", 1)[1])
                except Exception:
                    port = DEFAULT_PORT
        run_daemon(DEFAULT_HOST, port)
        sys.exit(0)
    if "--im-gateway" in sys.argv:
        from core.im_gateway import run as run_im_gateway
        run_im_gateway()
        sys.exit(0)
    if platform.system() == 'Windows':
        try:
            import ctypes
            myappid = f'deepseek.cowork.v3.5.{uuid.getnode()}'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = SafeApplication(sys.argv)
    icon_path = resolve_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    app.setStyle("Fusion")
    font = app.font()
    font.setFamily("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)
    window = MainWindow()
    app.main_window = window
    window.showMaximized()
    sys.exit(app.exec())
