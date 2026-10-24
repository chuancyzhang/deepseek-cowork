"""Shared browsing controls for skills, MCP services and experts."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QWidget, QApplication, QComboBox,
)
import qtawesome as qta

from core.theme import DesignTokens as T, bind_theme
from ui.primitives import ProductSegmentedControl, product_button_style, product_field_style
from ui.skillhub_widgets import ClampedText, store_columns


def capability_action_style():
    return product_button_style("ghost") + (
        f"QPushButton {{padding: 0 6px; font-weight: 400; color: {T.primary};}}"
        "QPushButton::menu-indicator {image: none; width: 0px;}"
    )


class CapabilityFilter(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CapabilitySceneFilter")
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="controls")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(product_field_style() +
                          "QComboBox#CapabilitySceneFilter::drop-down {border: none; width: 24px;}"
                          "QComboBox#CapabilitySceneFilter::down-arrow {image: none;}")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(T.text_secondary), 1.2))
        x, y = self.width() - 14, self.height() // 2
        painter.drawLine(x - 3, y - 1, x, y + 2)
        painter.drawLine(x, y + 2, x + 3, y - 1)


class CapabilityTabs(ProductSegmentedControl):
    """Quiet navigation with one selected underline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.layout().addStretch()
        self.refresh_theme()

    def refresh_theme(self, _resolved=None):
        self.setObjectName("CapabilityTabs")
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(T.spacing_md)
        self.setStyleSheet(
            f"QFrame#CapabilityTabs {{background: transparent; border: none; border-bottom: 1px solid {T.border_subtle};}}"
            f"QFrame#CapabilityTabs > QPushButton {{background: transparent; color: {T.text_secondary}; border: none; "
            f"border-bottom: 2px solid transparent; border-radius: 0; padding: 8px 4px; font-size: {T.font_size_body}px;}}"
            f"QFrame#CapabilityTabs > QPushButton:checked {{color: {T.primary}; border-bottom-color: {T.primary};}}"
            f"QFrame#CapabilityTabs > QPushButton:hover {{color: {T.primary};}}"
            f"QFrame#CapabilityTabs > QPushButton:focus {{background: {T.primary_soft};}}"
        )


class CapabilitySwitch(QPushButton):
    """Keyboard accessible, immediate intent; persistence belongs to the owner."""

    def __init__(self, name, enabled, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(enabled)
        self.setFixedSize(38, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"启用{name}")
        self.toggled.connect(lambda checked: self.setToolTip("关闭" if checked else "开启"))
        self.setToolTip("关闭" if enabled else "开启")
        bind_theme(self, lambda _=None: self.update(), surface="controls")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.primary if self.isChecked() else T.text_disabled))
        if not self.isEnabled():
            painter.setOpacity(0.45)
        painter.drawRoundedRect(QRectF(3, 7, 32, 16), 8, 8)
        painter.setBrush(QColor(T.text_inverse))
        painter.drawEllipse(QRectF(21 if self.isChecked() else 5, 9, 12, 12))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(T.primary), 1))
            painter.drawRoundedRect(QRectF(0.5, 3.5, 37, 23), 6, 6)


class CapabilityCard(QFrame):
    def __init__(self, name, description, metadata, icon="◇", *, object_name="CapabilityCard", parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.icon_name = icon
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.spacing_md, T.spacing_md, T.spacing_md, T.spacing_md)
        layout.setSpacing(T.spacing_sm)
        self.header = QHBoxLayout()
        self.header.setSpacing(T.spacing_sm)
        self.icon = QLabel(icon)
        self.icon.setObjectName("CapabilityIcon")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedSize(36, 36)
        self.header.addWidget(self.icon)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.title_button = QPushButton()
        self.title_button.setObjectName("CapabilityTitle")
        self.title_button.setMinimumWidth(0)
        self.title_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title_button.setAccessibleName(f"查看{name}")
        self.title_button.setCursor(Qt.PointingHandCursor)
        title_layout = QHBoxLayout(self.title_button)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.addWidget(ClampedText(name, strong=True))
        title_box.addWidget(self.title_button)
        title_box.addWidget(ClampedText(metadata))
        self.header.addLayout(title_box, 1)
        layout.addLayout(self.header)
        self.description = ClampedText(description or "暂无说明", lines=2)
        layout.addWidget(self.description)
        layout.addStretch()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(4)
        layout.addLayout(self.actions)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setFixedHeight(max(166, 2 * T.spacing_md + 3 * T.spacing_sm + T.font_size_meta * 5 + 40))
        self.setStyleSheet(
            f"QFrame#{self.objectName()} {{background: {T.bg_main}; border: 1px solid {T.border_subtle}; border-radius: {T.radius_md}px;}}"
            f"QLabel#CapabilityIcon {{background: {T.bg_secondary}; color: {T.text_secondary}; border: none; border-radius: {T.radius_sm}px;}}"
            f"QPushButton#CapabilityTitle {{background: transparent; border: none; padding: 0; text-align: left;}}"
            f"QPushButton#CapabilityTitle:focus {{background: {T.primary_soft}; border-radius: 2px;}}"
        )
        if self.icon_name.startswith("fa5"):
            self.icon.setPixmap(qta.icon(self.icon_name, color=T.text_secondary).pixmap(18, 18))
        for text in self.findChildren(ClampedText):
            text.refresh_theme()
        for button in self.findChildren(QPushButton, "CapabilityCardAction"):
            button.setStyleSheet(capability_action_style())

    def add_action(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("CapabilityCardAction")
        button.setStyleSheet(capability_action_style())
        button.clicked.connect(callback)
        self.actions.addWidget(button)
        return button

    def add_status(self, text, tone="text_secondary"):
        status = QLabel(text)
        status.setObjectName("CapabilityStateLabel")
        status.setWordWrap(True)
        status.setMinimumWidth(0)
        status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status.setStyleSheet(f"color: {getattr(T, tone)}; font-size: {T.font_size_meta}px; border: none;")
        bind_theme(status, lambda _=None: status.setStyleSheet(
            f"color: {getattr(T, tone)}; font-size: {T.font_size_meta}px; border: none;"), surface="management")
        self.actions.addWidget(status, 1)
        return status


class CapabilityGrid(QScrollArea):
    """Reflow existing cards without replacing focused controls or scroll state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CapabilityGrid")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.host.setObjectName("CapabilityGridContent")
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, T.spacing_sm)
        self.grid.setSpacing(T.spacing_md)
        self.grid.setAlignment(Qt.AlignTop)
        self.setWidget(self.host)
        self.cards = []
        self._columns = 0
        self.empty = QLabel(self.host)
        self.empty.setWordWrap(True)
        self.refresh_theme()
        bind_theme(self, self.refresh_theme, surface="management")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QScrollArea#CapabilityGrid {{border: none; background: {T.bg_main};}}"
                          f"QWidget#CapabilityGridContent {{background: {T.bg_main};}}")
        self.empty.setStyleSheet(f"color: {T.text_secondary}; padding: {T.spacing_md}px;")

    def set_cards(self, cards, empty_text="没有匹配的结果，试试其他关键词。"):
        focused = QApplication.focusWidget()
        focus_key = None
        for card in self.cards:
            if focused is not None and card.isAncestorOf(focused):
                focus_key = (card.property("serverId"), card.property("profileId"), focused.accessibleName())
        for card in self.cards:
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = cards
        self.empty.setText(empty_text)
        self.reflow(force=True)
        if focus_key and focus_key[2]:
            for card in cards:
                if (card.property("serverId"), card.property("profileId")) == focus_key[:2]:
                    for control in card.findChildren(QPushButton):
                        if control.accessibleName() == focus_key[2]:
                            control.setFocus(Qt.OtherFocusReason)

    def reflow(self, force=False):
        columns = store_columns(self.viewport().width())
        if columns == self._columns and not force:
            return
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(max(columns, self._columns)):
            self.grid.setColumnStretch(column, int(column < columns))
        self._columns = columns
        self.empty.setVisible(not self.cards)
        if not self.cards:
            self.grid.addWidget(self.empty, 0, 0, 1, columns)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
            card.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "cards"):
            self.reflow()
