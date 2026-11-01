"""Presentation-only history loading and recorded run timing."""

import math

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QWidget

from core.theme import DesignTokens, bind_theme


RUN_ELAPSED_META_KEY = "ui_run_elapsed_v1"


def valid_elapsed_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def history_elapsed_seconds(messages, conversation_meta):
    """Use only an explicitly recorded duration for one exact run identity."""
    request_ids = {
        str(meta.get("request_id") or "")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
        and isinstance(meta := message.get("meta"), dict)
        and meta.get("request_id")
    }
    if len(request_ids) != 1 or not isinstance(conversation_meta, dict):
        return None
    recorded = conversation_meta.get(RUN_ELAPSED_META_KEY)
    if not isinstance(recorded, dict):
        return None
    return valid_elapsed_seconds(recorded.get(next(iter(request_ids))))


def history_elapsed_label(value):
    seconds = valid_elapsed_seconds(value)
    if seconds is None:
        return "执行过程"
    if seconds < 1:
        return "用时 不足1秒"
    minutes, seconds = divmod(int(seconds + 0.5), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if seconds:
        parts.append(f"{seconds}秒")
    return "用时 " + " ".join(parts)


class HistoryLoadingOverlay(QWidget):
    """Mask incremental rendering without taking part in the scroll layout."""

    def __init__(self, viewport, text="正在打开对话…"):
        super().__init__(viewport)
        self.setObjectName("HistoryLoadingOverlay")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName(text)
        self.status_label = QLabel(text, self)
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setWordWrap(True)
        self._viewport = viewport
        viewport.installEventFilter(self)
        self.setGeometry(viewport.rect())
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="conversation")

    def refresh_theme(self, _resolved=None):
        self.status_label.setStyleSheet(
            "QLabel { background: transparent; border: none; "
            f"color: {DesignTokens.chat_text_muted}; font-size: {DesignTokens.font_size_meta}px; }}"
        )
        self._position_status()
        self.update()

    def _position_status(self):
        self.status_label.setGeometry(8, 104, max(1, self.width() - 24),
                                      max(28, self.status_label.fontMetrics().height() * 2))

    def eventFilter(self, watched, event):
        if watched is self._viewport and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_status()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(DesignTokens.bg_chat))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(DesignTokens.bg_secondary))
        width = max(0, self.width() - 24)
        user_width = min(width * .68, DesignTokens.user_bubble_max_width)
        painter.drawRoundedRect(QRectF(8 + width - user_width, 18, user_width, 48),
                                DesignTokens.radius_md, DesignTokens.radius_md)
        top = self.status_label.geometry().bottom() + 20
        for ratio in (.91, 1.0, .72):
            painter.drawRoundedRect(QRectF(8, top, width * ratio, 12), 5, 5)
            top += 25
        painter.drawRoundedRect(QRectF(8, top + 16, width, 64),
                                DesignTokens.radius_sm, DesignTokens.radius_sm)
