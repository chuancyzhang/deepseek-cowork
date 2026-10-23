"""Conversation-local find controls; searching never edits the message ledger."""
import re

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLineEdit, QLabel, QPushButton
from core.conversation_render import project_visible_messages
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style, product_field_style


def find_conversation_messages(messages, query):
    query = query.strip()
    if not query:
        return []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    for message in project_visible_messages(messages):
        if message.get("role") not in {"user", "assistant"} or not message.get("id"):
            continue
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        match = pattern.search(str(content))
        if match:
            excerpt = str(content)[max(0, match.start() - 28):match.end() + 70]
            results.append({"message_id": message["id"], "excerpt": " ".join(excerpt.split())})
    return results


class ConversationSearchBar(QFrame):
    queryChanged = Signal(str)
    stepRequested = Signal(int)
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ConversationSearchBar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("搜索对话内容")
        self.input.setAccessibleName("搜索对话内容")
        row.addWidget(self.input, 1)
        self.count = QLabel()
        row.addWidget(self.count)
        self.buttons = []
        for icon, name, callback in (
            ("fa5s.chevron-up", "上一条（Shift+Enter）", lambda: self.stepRequested.emit(-1)),
            ("fa5s.chevron-down", "下一条（Enter）", lambda: self.stepRequested.emit(1)),
            ("fa5s.times", "关闭搜索（Esc）", self.closeRequested.emit),
        ):
            button = QPushButton()
            button.setFixedSize(28, 28)
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(callback)
            row.addWidget(button)
            self.buttons.append((button, icon))
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(lambda: self.queryChanged.emit(self.input.text()))
        self.input.textChanged.connect(lambda: self.timer.start())
        self.input.installEventFilter(self)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="conversation")
        self.set_results([], -1)
        self.hide()

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QFrame#ConversationSearchBar {{ background: {DesignTokens.bg_secondary}; border: none; border-radius: 8px; }}")
        self.input.setStyleSheet(product_field_style())
        for button, icon in self.buttons:
            button.setIcon(qta.icon(icon, color=DesignTokens.text_secondary))
            button.setStyleSheet(product_button_style("ghost"))
        for label in (self.count,):
            label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")

    def set_results(self, matches, index):
        self.count.setText(f"{index + 1} / {len(matches)} 条" if matches else ("无结果" if self.input.text().strip() else ""))
        for button, _ in self.buttons[:2]:
            button.setEnabled(bool(matches))

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.closeRequested.emit()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.timer.isActive():
                    self.timer.stop()
                    self.queryChanged.emit(self.input.text())
                else:
                    self.stepRequested.emit(-1 if event.modifiers() & Qt.ShiftModifier else 1)
                return True
        return super().eventFilter(watched, event)
