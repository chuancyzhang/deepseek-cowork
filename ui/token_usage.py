"""Compact usage overview; technical detail stays behind a disclosure."""

from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.theme import DesignTokens, bind_theme
from ui.long_context import ContextLimitControl


OVERVIEW_CONTEXT_GROUPS = (
    ("dialogue", "对话消息", "accent_ai", ("assistant", "user")),
    ("results", "工具结果", "accent_success", ("results",)),
    ("instructions", "指令与技能", "text_tertiary", ("system", "injected")),
    ("tools", "工具定义", "accent_tool", ("tools",)),
)


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
    policy_saved = Signal()

    def __init__(self, parent=None, *, config_manager=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("TokenUsagePopover")
        self.setAccessibleName("用量概览")
        self._anchor = None
        self._context = None
        self._detail_text = ""
        self._base_detail_text = ""
        self._overview_detail_text = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("UsageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self.scroll)
        body = QWidget(self.scroll)
        body.setObjectName("UsageBody")
        self.scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.addWidget(self._label("用量", "title"))
        heading.addStretch()
        close = QPushButton("×", body)
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
        grid.setContentsMargins(0, 8, 0, 8)
        grid.setHorizontalSpacing(24)
        self.metrics = {}
        for index, key in enumerate(("total", "hit")):
            title = self._label("", "muted")
            value = self._label("—", "value")
            note = self._label("", "note")
            grid.addWidget(title, 0, index)
            grid.addWidget(value, 1, index)
            grid.addWidget(note, 2, index)
            grid.setColumnStretch(index, 1)
            self.metrics[key] = (title, value, note)
        layout.addLayout(grid)
        self.balance_box = QWidget(body)
        balance_row = QHBoxLayout(self.balance_box)
        balance_row.setContentsMargins(0, 0, 0, 4)
        balance_row.addWidget(self._label("账户余额", "muted"))
        balance_row.addStretch()
        self.balance_value = self._label("", "section")
        self.balance_note = self._label("", "note")
        balance_values = QVBoxLayout()
        balance_values.setSpacing(2)
        balance_values.addWidget(self.balance_value, 0, Qt.AlignRight)
        balance_values.addWidget(self.balance_note, 0, Qt.AlignRight)
        balance_row.addLayout(balance_values)
        layout.addWidget(self.balance_box)
        self.balance_box.hide()
        layout.addWidget(self._separator(body))

        self.capacity_box = QWidget(body)
        capacity_layout = QVBoxLayout(self.capacity_box)
        capacity_layout.setContentsMargins(0, 4, 0, 0)
        capacity_layout.setSpacing(8)
        capacity_heading = QHBoxLayout()
        capacity_heading.addWidget(self._label("上下文剩余", "section"))
        capacity_heading.addStretch()
        self.capacity_value = self._label("待统计", "remaining")
        capacity_heading.addWidget(self.capacity_value)
        capacity_layout.addLayout(capacity_heading)
        self.capacity_bar = UsageProportionBar(self.capacity_box)
        capacity_layout.addWidget(self.capacity_bar)
        self.capacity_used = self._label("", "note")
        self.capacity_budget = QPushButton(self.capacity_box)
        self.capacity_budget.setObjectName("UsageDisclosure")
        self.capacity_budget.setAccessibleName("查看上下文预算来源与限制")
        self.capacity_budget.setCursor(Qt.PointingHandCursor)
        self.capacity_budget.clicked.connect(lambda: self.details_button.setChecked(not self.details_button.isChecked()))
        self.capacity_usage_row = QBoxLayout(QBoxLayout.LeftToRight)
        self.capacity_usage_row.setSpacing(4)
        self.capacity_usage_row.addWidget(self.capacity_used, 1)
        self.capacity_usage_row.addWidget(self.capacity_budget, 0, Qt.AlignRight)
        capacity_layout.addLayout(self.capacity_usage_row)
        self.capacity_note = self._label("", "note")
        capacity_layout.addWidget(self.capacity_note)
        layout.addWidget(self.capacity_box)
        self.capacity_box.hide()
        self.limit_control = ContextLimitControl(config_manager, body) if config_manager is not None else None
        if self.limit_control is not None:
            layout.addWidget(self.limit_control)
            self.limit_control.saved.connect(self.policy_saved)
            self.limit_control.layout_changed.connect(self._schedule_place)
        self.limit_note = self._label("", "note")
        layout.addWidget(self.limit_note)
        layout.addWidget(self._separator(body))
        self.context_bar = UsageProportionBar(body)
        self.context_bar.setAccessibleName("最近请求的文本组成比例")
        layout.addWidget(self.context_bar)
        self.legend_layout = QGridLayout()
        self.legend_layout.setHorizontalSpacing(20)
        self.legend_layout.setVerticalSpacing(8)
        self.legend = {}
        self.legend_rows = []
        for index, (key, label, _token, _members) in enumerate(OVERVIEW_CONTEXT_GROUPS):
            item = QWidget(body)
            row = QHBoxLayout(item)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            dot = QFrame(item)
            dot.setObjectName("UsageDot_" + key)
            dot.setFixedSize(7, 7)
            row.addWidget(dot)
            row.addWidget(self._label(label, "note"))
            row.addStretch()
            value = self._label("—", "note")
            row.addWidget(value)
            self.legend_layout.addWidget(item, index // 2, index % 2)
            self.legend_layout.setColumnStretch(index % 2, 1)
            self.legend_rows.append(item)
            self.legend[key] = value
        self._legend_columns = 2
        layout.addLayout(self.legend_layout)
        self.context_note = self._label("下一次模型请求后显示组成。", "note")
        layout.addWidget(self.context_note)

        detail_actions = QHBoxLayout()
        layout.addWidget(self._separator(body))
        self.details_button = QPushButton("详情 ›", body)
        self.details_button.setObjectName("UsageDisclosure")
        self.details_button.setCheckable(True)
        self.details_button.setCursor(Qt.PointingHandCursor)
        self.details_button.toggled.connect(self._toggle_details)
        detail_actions.addWidget(self.details_button)
        detail_actions.addStretch()
        self.copy_button = QPushButton("复制全部统计", body)
        self.copy_button.setObjectName("UsageDisclosure")
        self.copy_button.setAccessibleName("复制全部用量统计")
        self.copy_button.clicked.connect(self._copy_details)
        self.copy_reset_timer = QTimer(self)
        self.copy_reset_timer.setSingleShot(True)
        self.copy_reset_timer.timeout.connect(lambda: self.copy_button.setText("复制全部统计"))
        detail_actions.addWidget(self.copy_button)
        self.copy_button.hide()
        layout.addLayout(detail_actions)
        self.details = QPlainTextEdit(body)
        self.details.setObjectName("UsageDetails")
        self.details.setReadOnly(True)
        self.details.setFixedHeight(150)
        self.details.hide()
        layout.addWidget(self.details)
        layout.addStretch()
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="feedback")

    def _separator(self, parent):
        line = QFrame(parent)
        line.setObjectName("UsageSeparator")
        line.setFixedHeight(1)
        return line

    def _label(self, text, role):
        label = QLabel(text, self.scroll.widget())
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
            f"QLabel[usageRole='remaining'] {{ font-size: {t.font_size_section}px; font-weight: 500; }}"
            f"QLabel[usageRole='muted'], QLabel[usageRole='note'] {{ color: {t.text_secondary}; }}"
            f"QLabel[usageRole='note'] {{ font-size: {t.font_size_caption}px; }}"
            f"QFrame#UsageSeparator {{ background: {t.separator}; border: none; }}"
            f"QPushButton#UsageDisclosure, QPushButton#UsageClose {{ color: {t.text_secondary}; background: transparent; border: none; padding: 4px; font-size: {t.font_size_meta}px; }}"
            f"QPushButton#UsageDisclosure:hover, QPushButton#UsageClose:hover {{ background: {t.bg_hover}; color: {t.text_primary}; }}"
            f"QPushButton#UsageDisclosure:focus, QPushButton#UsageClose:focus {{ border: 1px solid {t.primary_focus}; }}"
            f"QPlainTextEdit#UsageDetails {{ background: {t.bg_secondary}; color: {t.overlay_text}; border: 1px solid {t.border_subtle}; border-radius: {t.radius_sm}px; padding: 6px; font-size: {t.font_size_meta}px; }}"
            + "".join(
                f"QFrame#UsageDot_{key} {{ background: {getattr(t, token)}; border: none; border-radius: 2px; }}"
                for key, _label, token, _members in OVERVIEW_CONTEXT_GROUPS
            )
        )
        self.context_bar.update()
        self.capacity_bar.update()
        self._schedule_place()

    def set_overview(self, data):
        self.scope.setText(data.get("scope", "当前会话 · 当前模型"))
        capacity = data.get("capacity", {})
        self.capacity_box.setVisible(bool(capacity.get("visible")))
        self.capacity_value.setText(capacity.get("remaining", capacity.get("value", "待统计")))
        self.capacity_bar.set_segments(capacity.get("segments", []))
        self.capacity_used.setText(capacity.get("used", ""))
        self.capacity_used.setVisible(bool(capacity.get("used")))
        self.capacity_budget.setText(capacity.get("budget", "") + " ⓘ")
        self.capacity_budget.setVisible(bool(capacity.get("budget")))
        self.capacity_note.setText(capacity.get("caption", capacity.get("note", "")))
        self.capacity_note.setVisible(bool(self.capacity_note.text()))
        self.limit_note.setText(capacity.get("limit_note", ""))
        self.limit_note.setVisible(bool(self.limit_note.text()))
        for key, labels in self.metrics.items():
            metric = data.get(key, {})
            for label, field in zip(labels, ("title", "value", "note")):
                label.setText(metric.get("primary_note", "") if field == "note" else metric.get(field, ""))
                label.setToolTip(metric.get("detail", ""))
                if field == "note":
                    label.setVisible(bool(label.text()))
        balance = data.get("balance", {})
        self.balance_box.setVisible(bool(balance.get("visible")))
        self.balance_value.setText(balance.get("value", ""))
        self.balance_note.setText(balance.get("note", ""))
        self.balance_value.setToolTip(balance.get("detail", ""))
        context = data.get("context", {})
        if context != self._context:
            self._context = context
            self.context_bar.set_segments(context.get("segments", []))
            self.context_note.setText(context.get("caption", context.get("note", "下一次模型请求后显示组成。")))
            for key, label in self.legend.items():
                label.setText(context.get("values", {}).get(key, "—"))
                label.setToolTip(context.get("details", {}).get(key, ""))
            for row in self.legend_rows:
                row.setVisible(bool(context.get("segments")))
            self.context_bar.setVisible(bool(context.get("segments")))
        details = [capacity.get("detail", capacity.get("note", ""))]
        for key in ("round", "balance"):
            metric = data.get(key, {})
            details.append(f"{metric.get('title', '')}：{metric.get('value', '—')} · {metric.get('note', '')}")
        details.extend([data.get("cache_note", ""), context.get("detail", context.get("note", ""))])
        self._overview_detail_text = "\n\n".join(text for text in details if text)
        self.set_detail_text(self._base_detail_text)
        self._schedule_place()

    def set_detail_text(self, text):
        self._base_detail_text = str(text or "")
        self._detail_text = "\n\n".join(text for text in (self._overview_detail_text, self._base_detail_text) if text)
        if self.details.isVisible():
            self._refresh_details()

    def _refresh_details(self):
        if self.details.toPlainText() != self._detail_text:
            bar = self.details.verticalScrollBar()
            position = bar.value()
            self.details.setPlainText(self._detail_text)
            bar.setValue(position)

    def _copy_details(self):
        QApplication.clipboard().setText(self._detail_text)
        self.copy_button.setText("已复制")
        self.copy_reset_timer.start(1500)

    def _toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.copy_button.setVisible(expanded)
        self.details_button.setText("收起详情 ⌃" if expanded else "详情 ›")
        if expanded:
            self._refresh_details()
        self._schedule_place()

    def _schedule_place(self):
        if self.isVisible() and self._anchor is not None and not getattr(self, "_place_pending", False):
            self._place_pending = True
            QTimer.singleShot(0, self._place_after_layout)

    def _place_after_layout(self):
        self._place_pending = False
        if self.isVisible():
            self._place()

    def _place(self):
        anchor = self._anchor
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        host = anchor.window()
        if host is not None:
            available = available.intersected(QRect(host.mapToGlobal(QPoint()), host.size()))
        available = available.adjusted(8, 8, -8, -8)
        self.setFixedWidth(max(1, min(440, available.width())))
        columns = 1 if self.width() < 360 else 2
        self.capacity_usage_row.setDirection(QBoxLayout.TopToBottom if columns == 1 else QBoxLayout.LeftToRight)
        if columns != self._legend_columns:
            self._legend_columns = columns
            for index, item in enumerate(self.legend_rows):
                self.legend_layout.removeWidget(item)
                self.legend_layout.addWidget(item, index // columns, index % columns)
            self.legend_layout.setColumnStretch(1, 0 if columns == 1 else 1)
        self.scroll.widget().setFixedWidth(max(1, self.width() - 2 - self.scroll.verticalScrollBar().sizeHint().width()))
        self.scroll.widget().layout().activate()
        natural_height = self.scroll.widget().sizeHint().height() + 4
        self.setFixedHeight(max(1, min(natural_height, 660, available.height())))
        point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x = max(available.left(), min(point.x(), available.right() - self.width() + 1))
        y = point.y() + 6
        if y + self.height() > available.bottom() + 1:
            y = anchor.mapToGlobal(QPoint()).y() - self.height() - 6
        y = max(available.top(), min(y, available.bottom() - self.height() + 1))
        self.move(x, y)

    def show_for(self, anchor):
        self._anchor = anchor
        if self.limit_control is not None:
            self.limit_control.sync_saved()
        self._place()
        self.show()
        self.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)
