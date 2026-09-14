"""Compact usage overview; technical detail stays behind a disclosure."""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.context_usage import CONTEXT_CATEGORIES
from core.theme import DesignTokens, bind_theme


class UsageProportionBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self._segments = []

    def set_segments(self, segments):
        if segments != self._segments:
            self._segments = segments
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(bounds, 3, 3)
        painter.setClipPath(path)
        painter.fillRect(bounds, QColor(DesignTokens.bg_tertiary))
        total = sum(value for value, _token in self._segments)
        if total <= 0:
            return
        x = 0.0
        for value, token in self._segments:
            width = self.width() * value / total
            painter.fillRect(QRectF(x, 0, width, self.height()), QColor(getattr(DesignTokens, token)))
            x += width


class TokenUsagePopover(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("TokenUsagePopover")
        self.setAccessibleName("用量概览")
        self._anchor = None
        self._context = None
        self._detail_text = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("UsageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self.scroll)
        body = QWidget()
        body.setObjectName("UsageBody")
        self.scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        heading.addWidget(self._label("用量概览", "title"))
        heading.addStretch()
        close = QPushButton("×")
        close.setObjectName("UsageClose")
        close.setFixedSize(26, 26)
        close.setAccessibleName("关闭用量概览")
        close.setToolTip("关闭 · Esc")
        close.clicked.connect(self.hide)
        heading.addWidget(close)
        layout.addLayout(heading)
        self.scope = self._label("当前会话 · 当前模型", "muted")
        layout.addWidget(self.scope)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(14)
        self.metrics = {}
        for index, key in enumerate(("total", "cached", "hit", "round", "balance", "speed")):
            box = QVBoxLayout()
            box.setSpacing(3)
            title = self._label("", "muted")
            value = self._label("—", "value")
            note = self._label("", "note")
            box.addWidget(title)
            box.addWidget(value)
            box.addWidget(note)
            grid.addLayout(box, index // 3, index % 3)
            grid.setColumnStretch(index % 3, 1)
            self.metrics[key] = (title, value, note)
        layout.addLayout(grid)
        self.cache_bar = UsageProportionBar()
        self.cache_bar.setFixedHeight(5)
        layout.addWidget(self.cache_bar)
        self.cache_note = self._label("等待用量数据", "note")
        layout.addWidget(self.cache_note)

        line = QFrame()
        line.setObjectName("UsageSeparator")
        line.setFixedHeight(1)
        layout.addWidget(line)
        row = QHBoxLayout()
        row.addWidget(self._label("上下文组成", "section"))
        row.addStretch()
        self.context_total = self._label("待统计", "muted")
        row.addWidget(self.context_total)
        layout.addLayout(row)
        self.context_bar = UsageProportionBar()
        layout.addWidget(self.context_bar)
        legend = QGridLayout()
        legend.setHorizontalSpacing(16)
        legend.setVerticalSpacing(8)
        self.legend = {}
        for index, (key, label, _token) in enumerate(CONTEXT_CATEGORIES):
            row = QHBoxLayout()
            row.setSpacing(5)
            dot = QFrame()
            dot.setObjectName("UsageDot_" + key)
            dot.setFixedSize(7, 7)
            row.addWidget(dot)
            row.addWidget(self._label(label, "note"))
            row.addStretch()
            value = self._label("—", "note")
            row.addWidget(value)
            legend.addLayout(row, index // 2, index % 2)
            legend.setColumnStretch(index % 2, 1)
            self.legend[key] = value
        layout.addLayout(legend)
        self.context_note = self._label("下一次模型请求后显示组成。", "note")
        layout.addWidget(self.context_note)

        self.details_button = QPushButton("统计详情 ›")
        self.details_button.setObjectName("UsageDisclosure")
        self.details_button.setCheckable(True)
        self.details_button.setCursor(Qt.PointingHandCursor)
        self.details_button.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_button, 0, Qt.AlignLeft)
        self.details = QPlainTextEdit()
        self.details.setObjectName("UsageDetails")
        self.details.setReadOnly(True)
        self.details.setFixedHeight(150)
        self.details.hide()
        layout.addWidget(self.details)
        layout.addStretch()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    @staticmethod
    def _label(text, role):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setProperty("usageRole", role)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def refresh_theme(self, _resolved=None):
        t = DesignTokens
        self.setStyleSheet(
            f"QFrame#TokenUsagePopover {{ background: {t.overlay_bg}; border: 1px solid {t.overlay_border}; border-radius: {t.overlay_radius}px; }}"
            f"QWidget#UsageBody, QScrollArea#UsageScroll {{ background: {t.overlay_bg}; border: none; }}"
            f"QLabel[usageRole] {{ background: transparent; color: {t.overlay_text}; font-size: {t.font_size_meta}px; border: none; }}"
            f"QLabel[usageRole='title'] {{ font-size: {t.font_size_section}px; font-weight: 600; }}"
            f"QLabel[usageRole='section'] {{ font-weight: 600; }}"
            f"QLabel[usageRole='value'] {{ font-size: {t.font_size_page}px; font-weight: 600; }}"
            f"QLabel[usageRole='muted'], QLabel[usageRole='note'] {{ color: {t.text_secondary}; }}"
            f"QLabel[usageRole='note'] {{ font-size: {t.font_size_caption}px; }}"
            f"QFrame#UsageSeparator {{ background: {t.separator}; border: none; }}"
            f"QPushButton#UsageDisclosure, QPushButton#UsageClose {{ color: {t.text_secondary}; background: transparent; border: none; padding: 4px; font-size: {t.font_size_meta}px; }}"
            f"QPushButton#UsageDisclosure:hover, QPushButton#UsageClose:hover {{ background: {t.bg_hover}; color: {t.text_primary}; }}"
            f"QPushButton#UsageDisclosure:focus, QPushButton#UsageClose:focus {{ border: 1px solid {t.primary_focus}; }}"
            f"QPlainTextEdit#UsageDetails {{ background: {t.bg_secondary}; color: {t.overlay_text}; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; padding: 6px; font-size: {t.font_size_meta}px; }}"
            + "".join(
                f"QFrame#UsageDot_{key} {{ background: {getattr(t, token)}; border: none; border-radius: 2px; }}"
                for key, _label, token in CONTEXT_CATEGORIES
            )
        )
        self.context_bar.update()
        self.cache_bar.update()

    def set_overview(self, data):
        self.scope.setText(data.get("scope", "当前会话 · 当前模型"))
        for key, labels in self.metrics.items():
            metric = data.get(key, {})
            for label, field in zip(labels, ("title", "value", "note")):
                label.setText(metric.get(field, ""))
                label.setToolTip(metric.get("detail", ""))
        self.cache_bar.set_segments(data.get("cache_segments", []))
        self.cache_note.setText(data.get("cache_note", ""))
        context = data.get("context", {})
        if context != self._context:
            self._context = context
            self.context_total.setText(context.get("total", "待统计"))
            self.context_bar.set_segments(context.get("segments", []))
            self.context_note.setText(context.get("note", "下一次模型请求后显示组成。"))
            for key, label in self.legend.items():
                label.setText(context.get("values", {}).get(key, "—"))
                label.setToolTip(context.get("details", {}).get(key, ""))

    def set_detail_text(self, text):
        self._detail_text = str(text or "")
        if self.details.isVisible():
            self._refresh_details()

    def _refresh_details(self):
        if self.details.toPlainText() != self._detail_text:
            bar = self.details.verticalScrollBar()
            position = bar.value()
            self.details.setPlainText(self._detail_text)
            bar.setValue(position)

    def _toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.details_button.setText("收起详情 ⌃" if expanded else "统计详情 ›")
        if expanded:
            self._refresh_details()
        if self.isVisible():
            self._place()

    def _place(self):
        anchor = self._anchor
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        self.setFixedWidth(min(420, available.width() - 16))
        self.scroll.widget().layout().activate()
        natural_height = self.scroll.widget().sizeHint().height() + 4
        self.setFixedHeight(min(max(440, natural_height), 660, available.height() - 16))
        point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x = max(available.left() + 8, min(point.x(), available.right() - self.width() - 8))
        y = max(available.top() + 8, min(point.y() + 6, available.bottom() - self.height() - 8))
        self.move(x, y)

    def show_for(self, anchor):
        self._anchor = anchor
        self._place()
        self.show()
        self.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)
