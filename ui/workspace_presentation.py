"""Presentation-only controls for the conversation workspace."""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QLayout, QPushButton, QStyle, QStyleOptionButton, QStylePainter
from core.theme import DesignTokens


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
