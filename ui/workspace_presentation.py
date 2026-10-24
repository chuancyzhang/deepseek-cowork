"""Presentation-only controls for the conversation workspace."""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QLabel, QLayout, QPushButton, QStyle, QStyleOptionButton, QStylePainter,
    QHBoxLayout, QVBoxLayout, QSizePolicy, QWidget,
)
import qtawesome as qta
from core.theme import DesignTokens, bind_theme
from ui.primitives import ProductInlineNotice


class ConversationIdentityHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 8, 0, 10)
        row.setSpacing(8)
        self.icon_label = QLabel()
        row.addWidget(self.icon_label)
        self.name_label = QLabel("Cowork")
        self.name_label.setObjectName("ConversationAgentName")
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setWordWrap(True)
        row.addWidget(self.name_label, 1)
        bind_theme(self, self.refresh_theme, surface="conversation")
        self.refresh_theme()

    def refresh_theme(self, _resolved=None):
        self.icon_label.setPixmap(qta.icon("fa5s.magic", color=DesignTokens.icon_primary).pixmap(18, 18))
        self.name_label.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_body}px; font-weight: 600; background: transparent; border: none;")


class HomeRecentButton(QPushButton):
    """A readable recent conversation row, with the existing click action."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomeRecentButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)
        self.icon_label = QLabel()
        row.addWidget(self.icon_label)
        column = QVBoxLayout()
        column.setSpacing(5)
        self.title_label = QLabel()
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.meta_label = QLabel()
        self.meta_label.setMinimumWidth(0)
        self.meta_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        column.addWidget(self.title_label)
        column.addWidget(self.meta_label)
        row.addLayout(column, 1)
        self.arrow_label = QLabel()
        row.addWidget(self.arrow_label)
        for label in self.findChildren(QLabel):
            label.setTextFormat(Qt.PlainText)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._title = self._metadata = ""
        bind_theme(self, self.refresh_theme, surface="conversation")
        self.refresh_theme()

    def set_record(self, title, metadata):
        self._title, self._metadata = title, metadata
        self.setAccessibleName(f"{title}，{metadata}")
        self.setToolTip(title)
        self._elide()

    def text(self):
        return self._title

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        for label, value in ((self.title_label, self._title), (self.meta_label, self._metadata)):
            label.setText(label.fontMetrics().elidedText(value, Qt.ElideRight, max(0, label.width())))

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QPushButton#HomeRecentButton {{ background: transparent; border: none; border-bottom: 1px solid {DesignTokens.border_subtle}; border-radius: 0; }}"
            f"QPushButton#HomeRecentButton:hover {{ background: {DesignTokens.bg_hover}; }}"
            f"QPushButton#HomeRecentButton:focus {{ background: {DesignTokens.primary_soft}; }}")
        self.icon_label.setPixmap(qta.icon("fa5s.comment-alt", color=DesignTokens.icon_secondary).pixmap(18, 18))
        self.arrow_label.setPixmap(qta.icon("fa5s.chevron-right", color=DesignTokens.icon_secondary).pixmap(10, 10))
        self.title_label.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_body}px; background: transparent; border: none;")
        self.meta_label.setStyleSheet(f"color: {DesignTokens.text_tertiary}; font-size: {DesignTokens.font_size_meta}px; background: transparent; border: none;")
        self.setMinimumHeight(max(68, self.title_label.fontMetrics().height() + self.meta_label.fontMetrics().height() + 29))
        self._elide()


class ConversationRunNotice(ProductInlineNotice):
    """Present the actual run notice and existing recovery actions together."""

    def __init__(self, text="", tone="info", parent=None):
        super().__init__(text, tone, parent)
        layout = self.layout()
        for widget in (self.label, self.action_button, self.details_button):
            layout.removeWidget(widget)
        layout.setContentsMargins(16, 14, 16, 14)
        self.notice_icon = QLabel()
        layout.addWidget(self.notice_icon, 0, Qt.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(8)
        self.notice_title = QLabel()
        column.addWidget(self.notice_title)
        column.addWidget(self.label)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.action_button)
        actions.addWidget(self.details_button)
        actions.addStretch()
        column.addLayout(actions)
        layout.addLayout(column, 1)
        self.refresh_theme()

    def set_tone(self, tone):
        super().set_tone(tone)
        if not hasattr(self, "notice_title"):
            return
        attention = self._tone in {"error", "warning"}
        self.notice_title.setVisible(attention)
        self.notice_title.setText("任务未完成" if self._tone == "error" else "需要处理")
        self.notice_title.setStyleSheet(f"font-size: {DesignTokens.font_size_body}px; font-weight: 600;")
        self.label.setStyleSheet(f"font-size: {DesignTokens.font_size_body}px; color: {DesignTokens.text_secondary};")
        color = DesignTokens.error_text if self._tone == "error" else DesignTokens.warning_text if attention else DesignTokens.info_text
        self.notice_icon.setPixmap(qta.icon("fa5s.exclamation-circle" if attention else "fa5s.info-circle", color=color).pixmap(18, 18))


def context_action_style(active=False):
    return (
        f"QToolButton {{ background: {DesignTokens.bg_hover if active else 'transparent'}; "
        f"color: {DesignTokens.text_primary if active else DesignTokens.text_secondary}; "
        "border: 1px solid transparent; border-radius: 6px; padding: 4px 8px; font-size: 12px; }"
        f"QToolButton:hover {{ background: {DesignTokens.bg_hover}; color: {DesignTokens.text_primary}; }}"
        f"QToolButton:focus {{ border-color: {DesignTokens.primary_focus}; }}"
    )


class ComposerFlowLayout(QLayout):
    """Wrap context controls without changing their ownership or action handlers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(4)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect)

    def sizeHint(self):
        visible = [item for item in self._items if not item.isEmpty()]
        return QSize(sum(item.sizeHint().width() for item in visible) + max(0, len(visible) - 1) * self.spacing(), max((item.sizeHint().height() for item in visible), default=0))

    def minimumSize(self):
        result = QSize()
        for item in self._items:
            if not item.isEmpty():
                result = result.expandedTo(item.minimumSize())
        return result

    def _arrange(self, rect, measure=False):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            width = min(hint.width(), rect.width())
            if x > rect.x() and x + width > rect.right() + 1:
                x = rect.x()
                y += row_height + self.spacing()
                row_height = 0
            if not measure:
                item.setGeometry(QRect(x, y, width, hint.height()))
            x += width + self.spacing()
            row_height = max(row_height, hint.height())
        return y - rect.y() + row_height


class EditableConversationTitle(QLabel):
    editRequested = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(0)
        self.setTextFormat(Qt.PlainText)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.editRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            self.editRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ComposerSubmitButton(QPushButton):
    """Keep the action's text/accessibility semantics while painting a compact icon."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._compact = False

    def setText(self, text):
        super().setText(text)
        self.setAccessibleName(text)
        self.setToolTip(text)

    def setCompact(self, compact):
        self._compact = bool(compact)
        self.setFixedSize(34 if self._compact else 88, 34)
        self.update()

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if self._compact:
            option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButton, option)
