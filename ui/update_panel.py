"""User-facing projection of the application-owned updater."""
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QProgressBar, QTextEdit, QFileDialog

from core.app_version import APP_VERSION, compare_versions
from core.update_coordinator import release_notes_text, update_log_directory
from core.runtime_logs import log_path as runtime_log_path
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style


class UpdatePanel(QWidget):
    def __init__(self, coordinator, parent=None):
        super().__init__(parent)
        self.coordinator = coordinator
        self.notes_expanded = False
        self.setObjectName("UpdatePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.current = QLabel(f"当前版本：{APP_VERSION}")
        self.status = QLabel()
        self.version = QLabel()
        for label in (self.current, self.status, self.version):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            layout.addWidget(label)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(12)
        self.primary = QPushButton("检查更新")
        self.primary.setObjectName("PrimaryBtn")
        self.local = QPushButton("选择本地安装包")
        self.cancel_button = QPushButton("取消更新")
        for button in (self.primary, self.local, self.cancel_button):
            self.actions.addWidget(button)
        self.actions.addStretch()
        layout.addLayout(self.actions)
        self.hint = QLabel("更新时应用会短暂关闭，完成后自动重新打开。")
        self.hint.setWordWrap(True)
        if not coordinator.install_enabled:
            self.hint.setText("当前为源码运行模式，可以检查版本；自动安装仅适用于 Windows 打包版。")
        layout.addWidget(self.hint)
        self.notes_title = QLabel("本次更新")
        self.notes = QLabel()
        self.notes.setTextFormat(Qt.PlainText)
        self.notes.setWordWrap(True)
        self.notes.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.expand = QPushButton("展开阅读")
        self.expand.clicked.connect(self.toggle_notes)
        layout.addWidget(self.notes_title)
        layout.addWidget(self.notes)
        layout.addWidget(self.expand, alignment=Qt.AlignLeft)
        self.details_button = QPushButton("查看详情")
        self.details_button.setCheckable(True)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(120)
        self.details.setMaximumHeight(240)
        self.details.hide()
        self.details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details_button, alignment=Qt.AlignLeft)
        layout.addWidget(self.details)
        self.primary.clicked.connect(self.act)
        self.local.clicked.connect(self.choose_local)
        self.cancel_button.clicked.connect(coordinator.cancel)
        coordinator.changed.connect(self.render)
        bind_theme(self, lambda *_: self.apply_theme())
        self.apply_theme()
        self.render(coordinator.snapshot())

    def apply_theme(self):
        self.setStyleSheet(f"QWidget#UpdatePanel QLabel {{color: {DesignTokens.text_primary}; background: transparent;}}")
        self.current.setStyleSheet(f"font-size: {DesignTokens.font_size_section}px; font-weight: 600;")
        self.hint.setStyleSheet(f"color: {DesignTokens.text_secondary};")
        self.notes_title.setStyleSheet("font-weight: 600;")
        self.primary.setStyleSheet(product_button_style("primary"))
        for button in (self.local, self.cancel_button, self.expand, self.details_button):
            button.setStyleSheet(product_button_style("secondary"))

    def toggle_notes(self):
        self.notes_expanded = not self.notes_expanded
        self.render(self.coordinator.snapshot())

    def render(self, state):
        result = state.get("result") or {}
        release = result.get("release") or {}
        phase = state.get("phase")
        busy = state.get("busy", False)
        version = release.get("tag_name") or ""
        self.status.setText(state.get("message") or "")
        self.version.setText(("安装版本：" if result.get("update_source") == "local" else "可用版本：") + version)
        self.version.setVisible(bool(version) and result.get("update_available", False))
        ready = self.coordinator.prepared is not None
        available = bool(result.get("update_available"))
        self.primary.setText("立即更新" if available else "检查更新")
        if result.get("update_source") == "local":
            self.primary.setText("重新安装当前版本" if compare_versions(version, APP_VERSION) == 0 else "立即安装")
        if phase == "error":
            self.primary.setText("重新选择安装包" if state.get("source") == "local" else "重试")
        self.local.setVisible(not (phase == "error" and state.get("source") == "local"))
        if not self.coordinator.install_enabled and available:
            self.primary.setText("查看新版")
        self.primary.setEnabled(phase != "installing" and (not busy or ready or available) and not state.get("install_requested"))
        self.local.setEnabled(self.coordinator.install_enabled and phase != "installing")
        self.cancel_button.setVisible((busy and phase != "installing") or bool(state.get("can_cancel_install")))
        self.cancel_button.setText("取消更新" if state.get("install_requested") or phase == "installing" else "取消下载")
        self.progress.setVisible(phase in {"downloading", "validating", "planning", "retrying", "installing"})
        percent = state.get("percent", -1)
        self.progress.setRange(0, 0 if percent < 0 else 100)
        if percent >= 0:
            self.progress.setValue(percent)
        text = release_notes_text(release.get("body"))
        lines = text.splitlines()
        self.notes_title.setVisible(bool(text))
        self.notes.setVisible(bool(text))
        self.notes.setText(text if self.notes_expanded else "\n".join(lines[:6]))
        self.expand.setVisible(len(lines) > 6)
        self.expand.setText("收起" if self.notes_expanded else "展开阅读")
        details = [state.get("error") or "", f"更新文件：{result.get('zip_path', '尚未下载')}",
                   f"诊断记录：{runtime_log_path('app_update', update_log_directory(self.coordinator.root))}"]
        self.details.setPlainText("\n".join(line for line in details if line))
        # Keep all actions reachable at small window widths / high Windows scaling.
        self.actions.setDirection(QHBoxLayout.TopToBottom if self.width() < 600 else QHBoxLayout.LeftToRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.actions.setDirection(QHBoxLayout.TopToBottom if self.width() < 600 else QHBoxLayout.LeftToRight)

    def act(self):
        state = self.coordinator.snapshot()
        result = state.get("result") or {}
        if not self.coordinator.install_enabled and result.get("update_available"):
            QDesktopServices.openUrl(QUrl(result["release"]["html_url"]))
        elif state.get("phase") == "error":
            if state.get("source") == "local":
                self.choose_local()
            else:
                self.coordinator.check()
        elif self.coordinator.prepared or result.get("update_available"):
            self.coordinator.request_install()
        else:
            self.coordinator.check()

    def choose_local(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择本地安装包", "", "DeepSeek Cowork 安装包 (deepseek-cowork-v*.zip);;ZIP 文件 (*.zip)")
        if path:
            self.coordinator.select_local(path)
