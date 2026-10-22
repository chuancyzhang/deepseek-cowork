"""The shared numbered code surface with a local, keyboard-accessible finder."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
from core.theme import DesignTokens

from ui.primitives import ProductCodeViewer, product_field_style


class FileCodeViewer(ProductCodeViewer):
    def __init__(self, *, editable=False, parent=None):
        super().__init__(parent=parent)
        self.setReadOnly(not editable)
        self.search = QLineEdit(self._header)
        self.search.setPlaceholderText("查找 · Enter 下一处 · Esc 关闭")
        self.search.setStyleSheet(product_field_style())
        self.search.hide()
        self._header.layout().insertWidget(1, self.search, 1)
        self.search.returnPressed.connect(self.find_next)
        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self.show_find)
        self.close_find_shortcut = QShortcut(QKeySequence("Escape"), self.search)
        self.close_find_shortcut.activated.connect(self.close_find)
        self.refresh_theme()

    def show_find(self):
        self.search.show()
        self.search.setFocus()
        self.search.selectAll()

    def refresh_theme(self, _resolved=None):
        super().refresh_theme(_resolved)
        self.setStyleSheet(self.styleSheet() + (
            f"QPlainTextEdit {{ background: {DesignTokens.preview_shell_bg}; border: none; border-radius: 0; }}"
            f"QFrame#ProductCodeViewerHeader {{ background: {DesignTokens.preview_shell_toolbar_bg}; border: none; "
            f"border-bottom: 1px solid {DesignTokens.preview_shell_border}; }}"
        ))
        if hasattr(self, "search"):
            self.search.setStyleSheet(product_field_style())

    def close_find(self):
        self.search.hide()
        self.setFocus()

    def find_next(self):
        value = self.search.text()
        if value and not self.find(value):
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.setTextCursor(cursor)
            self.find(value)

    def contextMenuEvent(self, event):
        if self.isReadOnly():
            return super().contextMenuEvent(event)
        return QPlainTextEdit.contextMenuEvent(self, event)
