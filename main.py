import sys
import subprocess
import tempfile
import os
import ast
import re
import json
import platform
from datetime import datetime
from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.agent import LLMWorker
from core.interaction import bridge
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox, QFileDialog, QScrollArea, QFrame, QDialog, QFormLayout, QCheckBox, QGroupBox, QInputDialog)
from PySide6.QtCore import Qt, QThread, Signal

# Try importing OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

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

        # 思考过程 (折叠区域)
        if thinking:
            self.think_btn = QPushButton(f"👁️ 查看思考过程 ({len(thinking)} chars)")
            self.think_btn.setCheckable(True)
            self.think_btn.setStyleSheet("text-align: left; color: #7f8c8d; border: none; background: none;")
            self.think_btn.toggled.connect(self.toggle_thinking)
            layout.addWidget(self.think_btn)
            
            self.think_content = QTextEdit()
            self.think_content.setPlainText(thinking)
            self.think_content.setReadOnly(True)
            self.think_content.setStyleSheet("background-color: #f9f9f9; color: #666; font-style: italic; border: 1px dashed #ccc;")
            self.think_content.setMaximumHeight(150)
            self.think_content.setVisible(False) # 默认折叠
            layout.addWidget(self.think_content)

        # 主要内容
        content_label = QLabel(text)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(content_label)
        
        # 样式区分
        if role == "User":
            self.setStyleSheet("background-color: #e3f2fd; border-radius: 10px; margin: 5px;")
        else:
            self.setStyleSheet("background-color: #ffffff; border-radius: 10px; margin: 5px; border: 1px solid #ddd;")

    def toggle_thinking(self, checked):
        self.think_content.setVisible(checked)
        self.think_btn.setText(f"{'🔽' if checked else '👁️'} 思考过程")

class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(400, 300)
        self.config_manager = config_manager
        # Use a temporary SkillManager to find skill directories
        self.temp_skill_manager = SkillManager() 

        layout = QVBoxLayout(self)

        # API Key
        form_layout = QFormLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.config_manager.get("api_key", ""))
        form_layout.addRow("DeepSeek API Key:", self.api_key_input)
        layout.addLayout(form_layout)

        # Skills
        skills_group = QGroupBox("Skills / MCP (启用/禁用)")
        skills_layout = QVBoxLayout()
        
        self.skill_checks = {}
        
        # Iterate over skill directories
        for skills_dir in self.temp_skill_manager.skills_dirs:
            if os.path.exists(skills_dir):
                for skill_name in os.listdir(skills_dir):
                    skill_path = os.path.join(skills_dir, skill_name)
                    if os.path.isdir(skill_path):
                        # Avoid duplicates if skill exists in multiple locations (though unlikely with current logic)
                        if skill_name in self.skill_checks:
                            continue
                            
                        chk = QCheckBox(skill_name)
                        chk.setChecked(self.config_manager.is_skill_enabled(skill_name))
                        self.skill_checks[skill_name] = chk
                        skills_layout.addWidget(chk)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        skills_widget = QWidget()
        skills_widget.setLayout(skills_layout)
        scroll.setWidget(skills_widget)
        skills_group.setLayout(QVBoxLayout())
        skills_group.layout().addWidget(scroll)
        
        layout.addWidget(skills_group)

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
        
        # Save Skills
        for skill_name, chk in self.skill_checks.items():
            self.config_manager.set_skill_enabled(skill_name, chk.isChecked())
            
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart File Assistant (DeepSeek Mode)")
        self.resize(1000, 800)
        self.workspace_dir = None
        
        # Initialize conversation history
        self.messages = []
        
        self.config_manager = ConfigManager()
        # API Key is now in config_manager

        # Connect to Interaction Bridge
        bridge.request_confirmation_signal.connect(self.handle_confirmation_request)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 0. Workspace
        ws_layout = QHBoxLayout()
        self.ws_label = QLabel("当前工作区: 未选择")
        self.ws_label.setStyleSheet("color: red; font-weight: bold;")
        self.ws_btn = QPushButton("📂 选择工作区")
        self.ws_btn.clicked.connect(self.select_workspace)
        
        self.settings_btn = QPushButton("⚙️ 设置")
        self.settings_btn.clicked.connect(self.open_settings)
        
        ws_layout.addWidget(self.ws_label)
        ws_layout.addWidget(self.ws_btn)
        ws_layout.addWidget(self.settings_btn)
        layout.addLayout(ws_layout)

        # 1. Chat Area (Scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.addStretch() # 让消息从顶部开始堆叠
        scroll.setWidget(self.chat_container)
        layout.addWidget(scroll, 3)

        # 2. Input Area
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("请输入您的需求...")
        self.input_field.returnPressed.connect(self.handle_send)
        
        self.pause_btn = QPushButton("⏸️ 暂停")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setVisible(False)
        
        self.stop_btn = QPushButton("⏹️ 停止")
        self.stop_btn.clicked.connect(self.stop_agent)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet("color: red;")
        self.stop_btn.setToolTip("User Control : If the model gets stuck in a loop (e.g., repeatedly listing the same directory), please use the Stop button to terminate the operation manually.")
        
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.handle_send)
        
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.pause_btn)
        input_layout.addWidget(self.stop_btn)
        input_layout.addWidget(self.send_btn)
        
        # Loop Hint Label
        self.loop_hint = QLabel("⚠️ 若陷入死循环请按停止")
        self.loop_hint.setStyleSheet("color: #e74c3c; font-size: 10px; margin-left: 5px;")
        self.loop_hint.setVisible(False)
        input_layout.addWidget(self.loop_hint)
        
        layout.addLayout(input_layout)

        # 3. Log Area
        layout.addWidget(QLabel("执行日志:"))
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas;")
        self.log_display.setMaximumHeight(200)
        layout.addWidget(self.log_display, 1)

        # Load chat history
        self.load_chat_history()

    def handle_confirmation_request(self, message):
        """Handle confirmation requests from background threads"""
        reply = QMessageBox.question(self, '需要确认', message, 
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        bridge.respond(reply == QMessageBox.Yes)

    def load_chat_history(self):
        """Load chat history from JSON file"""
        history_path = os.path.join(os.getcwd(), 'chat_history.json')
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    self.messages = json.load(f)
                
                # Reconstruct UI bubbles
                for msg in self.messages:
                    role = msg.get('role')
                    content = msg.get('content')
                    if role == 'user':
                        self.add_chat_bubble('User', content, save=False)
                    elif role == 'assistant' and content:
                        self.add_chat_bubble('Agent', content, save=False)
                
                self.append_log(f"System: 已加载 {len(self.messages)} 条历史消息")
            except Exception as e:
                self.append_log(f"Error loading history: {e}")

    def save_chat_history(self):
        """Save chat history to JSON file"""
        history_path = os.path.join(os.getcwd(), 'chat_history.json')
        try:
            with open(history_path, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.append_log(f"Error saving history: {e}")

    def select_workspace(self):
        directory = QFileDialog.getExistingDirectory(self, "选择工作区")
        if directory:
            self.workspace_dir = directory
            self.ws_label.setText(f"当前工作区: {directory}")
            self.ws_label.setStyleSheet("color: green; font-weight: bold;")
            self.append_log(f"System: 工作区已切换至 {directory}")

    def open_settings(self):
        try:
            dialog = SettingsDialog(self.config_manager, self)
            if dialog.exec():
                self.append_log("System: 配置已更新")
        except Exception as e:
            self.append_log(f"Error opening settings: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to open settings: {str(e)}")

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
        self.log_display.append(text)

    def process_agent_logic(self, user_text):
        """启动 LLM 线程获取响应"""
        self.append_log(f"Agent: 正在深度思考 (DeepSeek CoT)...")
        
        # Start LLM Worker
        # Note: We pass self.messages, but LLMWorker works on a copy for the turn loop.
        # MainWindow only stores the FINAL result of the turn sequence (or we could choose to store intermediate tool calls too).
        # For simplicity and clean history, we only store the User Request and the Final Assistant Answer in MainWindow messages.
        # BUT DeepSeek might need context of previous tools? 
        # Actually, for next turn, we just need the conversation flow.
        # Let's assume LLMWorker returns the Final Answer content.
        
        self.llm_worker = LLMWorker(self.messages, self.config_manager, self.workspace_dir)
        self.llm_worker.finished_signal.connect(self.handle_llm_response)
        self.llm_worker.step_signal.connect(self.append_log) # Log intermediate steps
        self.llm_worker.start()
        
        # UI State Update
        self.pause_btn.setVisible(True)
        self.pause_btn.setText("⏸️ 暂停")
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.loop_hint.setVisible(True)

    def handle_llm_response(self, result):
        # UI State Reset
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        self.loop_hint.setVisible(False)
        
        if "error" in result:
            self.append_log(f"Error: {result['error']}")
            self.add_chat_bubble("System", f"Error: {result['error']}")
            self.send_btn.setEnabled(True)
            return

        reasoning = result.get("reasoning", "")
        content = result.get("content", "")
        role = result.get("role", "assistant")

        # 1. Update UI
        self.add_chat_bubble("Agent", content, reasoning)

        # 2. Update History (Important: Do not include reasoning_content in context for next turn)
        # According to DeepSeek docs, we just append content.
        self.messages.append({"role": role, "content": content})
        self.save_chat_history()

        # 3. Extract and Execute Code
        code_match = re.search(r'```python(.*?)```', content, re.DOTALL)
        if code_match:
            code_block = code_match.group(1).strip()
            self.append_log("Agent: 检测到代码块，准备执行...")
            self.code_worker = CodeWorker(code_block, self.workspace_dir)
            self.code_worker.output_signal.connect(self.append_log)
            self.code_worker.finished_signal.connect(self.handle_code_finished)
            self.code_worker.input_request_signal.connect(self.handle_code_input_request)
            
            # Show Stop Button for Code Execution
            self.stop_btn.setVisible(True)
            self.stop_btn.setEnabled(True)
            self.stop_btn.setText("⏹️ 停止代码")
            
            self.code_worker.start()
        else:
            self.send_btn.setEnabled(True)

    def handle_code_finished(self):
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
