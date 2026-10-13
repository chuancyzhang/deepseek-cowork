"""Opt-in context controls and runtime-only status, with no model requests."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.long_context import normalize_policy
from core.theme import DesignTokens, bind_theme
from ui.primitives import PageScrollSafeComboBox, ProductMessageDialog


logger = logging.getLogger(__name__)


class LongContextSettings(QWidget):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("LongContextSettings")
        self.setMaximumWidth(720)
        self.config_manager = config_manager
        self.saved = normalize_policy(config_manager.get("experimental_long_context"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("超长上下文")
        title.setObjectName("LongContextHeading")
        layout.addWidget(title)
        note = QLabel("适合需要多轮处理大量材料的长任务。接近上限时自动整理较早的上下文，完整对话和工具结果仍保留在当前会话中。整理会产生额外模型用量。")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.enabled = QCheckBox("启用超长上下文")
        self.enabled.setChecked(self.saved["enabled"])
        layout.addWidget(self.enabled)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.target_label = QLabel("上下文长度")
        layout.addWidget(self.target_label)
        self.target = PageScrollSafeComboBox()
        self.target.setAccessibleName("上下文长度")
        self.target.addItem("30 万 tokens（300k，推荐）", 300000)
        self.target.addItem("100 万 tokens（1m）", 1000000)
        self.target.setMaximumWidth(360)
        self.target_label.setBuddy(self.target)
        self.target.setCurrentIndex(self.target.findData(self.saved["target_tokens"]))
        layout.addWidget(self.target)
        self.target_hint = QLabel()
        self.target_hint.setWordWrap(True)
        layout.addWidget(self.target_hint)
        timing = QLabel("保存后，下次开始或重试任务时生效；正在进行的任务继续使用原设置。设置在重启后保留，仅用于桌面主会话。")
        timing.setWordWrap(True)
        layout.addWidget(timing)

        actions = QHBoxLayout()
        self.apply_button = QPushButton("保存设置")
        self.apply_button.setObjectName("LongContextApply")
        self.apply_button.clicked.connect(self.save)
        actions.addWidget(self.apply_button)
        self.reset_button = QPushButton("撤销更改")
        self.reset_button.clicked.connect(self.reset)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.result)
        layout.addStretch()
        self.enabled.toggled.connect(self._input_changed)
        self.target.currentIndexChanged.connect(self._input_changed)
        self._refresh()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="settings")

    def _value(self):
        return {"enabled": self.enabled.isChecked(), "target_tokens": self.target.currentData()}

    def has_pending_input(self):
        return self._value() != self.saved

    def _input_changed(self):
        self.result.clear()
        self._refresh()

    def _refresh(self):
        dirty = self.has_pending_input()
        saved_length = "30 万" if self.saved["target_tokens"] == 300000 else "100 万"
        status = f"已保存：已启用 · {saved_length} tokens" if self.saved["enabled"] else "已保存：已关闭 · 使用完整历史"
        self.status.setText(status + ("\n有未保存的更改，点击“保存设置”后生效。" if dirty else ""))
        enabled = self.enabled.isChecked()
        self.target.setEnabled(enabled)
        self.target_label.setEnabled(enabled)
        if not enabled:
            hint = "开启后可选择长度。关闭后使用完整历史；已有记录和整理结果会保留。历史过长时，需要重新启用或新建会话。"
        else:
            hint = ("30 万适合多数长任务，会更早整理上下文。" if self.target.currentData() == 300000
                    else "100 万可保留更多近期材料，单次请求可能更慢、用量更高。")
            hint += "实际可用长度受模型限制，选择更大数值不会扩大模型容量。"
        self.target_hint.setText(hint)
        self.apply_button.setEnabled(dirty)
        self.reset_button.setEnabled(dirty)

    def save(self):
        value = self._value()
        logger.info("long_context_settings_save_started enabled=%s target_tokens=%s", value["enabled"], value["target_tokens"])
        try:
            self.config_manager.set_experimental_long_context(value)
        except Exception as exc:
            logger.error("long_context_settings_save_failed error_type=%s", type(exc).__name__)
            self.result.setText("保存失败，选择已保留。请检查磁盘空间和配置目录权限，再点击“保存设置”重试。")
            return False
        logger.info("long_context_settings_save_completed enabled=%s target_tokens=%s", value["enabled"], value["target_tokens"])
        self.saved = value
        self.result.setText("已保存，下次开始或重试任务时使用新设置。")
        self._refresh()
        return True

    def reset(self):
        self.enabled.setChecked(self.saved["enabled"])
        self.target.setCurrentIndex(self.target.findData(self.saved["target_tokens"]))
        self.result.clear()
        self._refresh()

    def confirm_leave(self):
        if not self.has_pending_input():
            return True
        choice = ProductMessageDialog(
            "超长上下文设置尚未保存", "启用状态或上下文长度有更改。保存后离开，或放弃这些更改。", "confirm",
            [("保存并离开", "save", "primary", False),
             ("放弃更改", "discard", "secondary", False),
             ("继续编辑", "stay", "secondary", True)], parent=self,
        ).exec_result("stay")
        if choice == "save":
            return self.save()
        if choice == "discard":
            self.reset()
            return True
        return False

    def refresh_theme(self, _resolved=None):
        t = DesignTokens
        self.setStyleSheet(
            f"QWidget#LongContextSettings {{ background: transparent; }}"
            f"QWidget#LongContextSettings > QLabel {{ color: {t.text_secondary}; font-size: {t.font_size_body}px; }}"
            f"QWidget#LongContextSettings > QLabel#LongContextHeading {{ color: {t.text_primary}; font-weight: 600; font-size: {t.font_size_section}px; }}"
            f"QWidget#LongContextSettings > QCheckBox {{ color: {t.text_primary}; font-size: {t.font_size_body}px; spacing: 8px; min-height: {t.control_height}px; }}"
            f"QWidget#LongContextSettings > QComboBox {{ background: {t.bg_main}; color: {t.text_primary}; padding: 0 10px; min-height: {t.control_height_lg}px; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; }}"
            f"QWidget#LongContextSettings > QComboBox QAbstractItemView {{ background: {t.bg_main}; color: {t.text_primary}; selection-background-color: {t.selection_bg}; selection-color: {t.selection_text}; }}"
            f"QWidget#LongContextSettings > QPushButton {{ color: {t.text_primary}; background: {t.bg_secondary}; padding: 0 14px; min-height: {t.control_height_lg}px; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; }}"
            f"QWidget#LongContextSettings > QPushButton#LongContextApply {{ color: {t.bg_main}; background: {t.primary}; }}"
            f"QWidget#LongContextSettings > QPushButton:disabled, QWidget#LongContextSettings > QPushButton#LongContextApply:disabled {{ color: {t.text_tertiary}; background: {t.bg_secondary}; }}"
            f"QWidget#LongContextSettings > QComboBox:disabled, QWidget#LongContextSettings > QLabel:disabled {{ color: {t.text_disabled}; }}"
            f"QWidget#LongContextSettings > QPushButton:focus, QWidget#LongContextSettings > QComboBox:focus, QWidget#LongContextSettings > QCheckBox:focus {{ border: 1px solid {t.primary_focus}; }}"
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
