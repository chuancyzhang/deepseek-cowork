"""Opt-in context controls and runtime-only status, with no model requests."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.long_context import normalize_policy
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductMessageDialog


class LongContextSettings(QWidget):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("LongContextSettings")
        self.config_manager = config_manager
        self.saved = normalize_policy(config_manager.get("experimental_long_context"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("超长上下文")
        title.setObjectName("LongContextHeading")
        layout.addWidget(title)
        note = QLabel("接近所选上限时自动压缩；实际可用长度受模型限制。完整历史与工具结果保留在当前会话中。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.target = QComboBox()
        self.target.setAccessibleName("上下文目标上限")
        self.target.addItem("300k tokens", 300000)
        self.target.addItem("1m tokens", 1000000)
        self.target.setCurrentIndex(self.target.findData(self.saved["target_tokens"]))
        self.target.currentIndexChanged.connect(self._refresh)
        layout.addWidget(self.target)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.apply_button = QPushButton()
        self.apply_button.setObjectName("LongContextApply")
        self.apply_button.clicked.connect(lambda: self.save(True))
        actions.addWidget(self.apply_button)
        self.disable_button = QPushButton("关闭")
        self.disable_button.clicked.connect(lambda: self.save(False))
        actions.addWidget(self.disable_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.result = QLabel("设置从下一次启动的任务轮次生效，重启后保留。仅用于桌面主会话。")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch()
        self._refresh()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="settings")

    def has_pending_input(self):
        return self.target.currentData() != self.saved["target_tokens"]

    def _refresh(self):
        enabled = self.saved["enabled"]
        self.status.setText("已启用 · 当前任务保持原设置" if enabled else "未启用 · 默认使用完整历史")
        self.apply_button.setText("应用设置" if enabled else "启用")
        self.apply_button.setEnabled(not enabled or self.has_pending_input())
        self.disable_button.setVisible(enabled)

    def save(self, enabled):
        value = {"enabled": bool(enabled), "target_tokens": self.target.currentData()}
        try:
            self.config_manager.set_experimental_long_context(value)
        except Exception as exc:
            self.result.setText(f"保存失败，选择已保留，请重试。{exc}")
            return False
        self.saved = value
        self.result.setText("已保存，新设置下一轮生效。" if enabled else "已关闭，下一轮使用完整历史；历史和检查点已保留。若历史超限，可重新启用或新建会话。")
        self._refresh()
        return True

    def confirm_leave(self):
        if not self.has_pending_input():
            return True
        choice = ProductMessageDialog(
            "超长上下文设置尚未保存", "保存所选档位后离开，或放弃更改。", "confirm",
            [("应用并离开" if self.saved["enabled"] else "启用并离开", "save", "primary", False),
             ("放弃更改", "discard", "secondary", False),
             ("继续编辑", "stay", "secondary", True)], parent=self,
        ).exec_result("stay")
        if choice == "save":
            return self.save(True)
        if choice == "discard":
            self.target.setCurrentIndex(self.target.findData(self.saved["target_tokens"]))
            return True
        return False

    def refresh_theme(self, _resolved=None):
        t = DesignTokens
        self.setStyleSheet(
            f"QWidget#LongContextSettings {{ background: transparent; }}"
            f"QWidget#LongContextSettings > QLabel {{ color: {t.text_secondary}; font-size: {t.font_size_body}px; }}"
            f"QLabel#LongContextHeading {{ color: {t.text_primary}; font-weight: 600; font-size: {t.font_size_section}px; }}"
            f"QWidget#LongContextSettings > QComboBox {{ background: {t.bg_main}; color: {t.text_primary}; padding: 8px; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; }}"
            f"QWidget#LongContextSettings > QPushButton {{ color: {t.text_primary}; background: {t.bg_secondary}; padding: 8px 14px; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; }}"
            f"QPushButton#LongContextApply {{ color: {t.bg_main}; background: {t.primary}; }}"
            f"QWidget#LongContextSettings > QPushButton:disabled {{ color: {t.text_tertiary}; background: {t.bg_secondary}; }}"
            f"QWidget#LongContextSettings > QPushButton:focus, QWidget#LongContextSettings > QComboBox:focus {{ border: 1px solid {t.primary_focus}; }}"
        )


class ContextRolloverNotice(QFrame):
    TEXT = {
        "started": "开始压缩上下文，完成后将继续当前任务",
        "completed": "上下文已压缩，继续当前任务",
        "failed": "上下文压缩失败，历史与已有成果已保留",
        "cancelled": "已停止压缩，历史与已有成果已保留",
    }

    def __init__(self, retry, parent=None):
        super().__init__(parent)
        self.setObjectName("ContextRolloverNotice")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 7, 24, 7)
        layout.setSpacing(6)
        line = QFrame()
        line.setObjectName("ContextRolloverLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail)
        self.retry_button = QPushButton("重试压缩")
        self.retry_button.clicked.connect(retry)
        layout.addWidget(self.retry_button, 0, Qt.AlignCenter)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="conversation")

    def set_event(self, event, *, active=True):
        phase = event.get("phase", "started")
        text = self.TEXT.get(phase, self.TEXT["started"])
        if phase == "started" and event.get("part"):
            text += f" · 正在整理第 {event['part']} 段"
        self.label.setText(text)
        self.setAccessibleName(text)
        self.detail.setText(event.get("error", "") if phase == "failed" else "")
        self.detail.setVisible(bool(self.detail.text()))
        self.retry_button.setVisible(phase == "failed" and active)
        self.retry_button.setEnabled(True)

    def refresh_theme(self, _resolved=None):
        t = DesignTokens
        self.setStyleSheet(
            f"QFrame#ContextRolloverNotice {{ background: transparent; border: none; }}"
            f"QFrame#ContextRolloverLine {{ background: {t.separator}; border: none; }}"
            f"QFrame#ContextRolloverNotice > QLabel {{ color: {t.text_secondary}; background: transparent; font-size: {t.font_size_caption}px; }}"
            f"QFrame#ContextRolloverNotice > QPushButton {{ background: {t.bg_secondary}; color: {t.text_primary}; padding: 5px 12px; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; }}"
            f"QFrame#ContextRolloverNotice > QPushButton:focus {{ border-color: {t.primary_focus}; }}"
        )
