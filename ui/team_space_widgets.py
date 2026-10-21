"""Responsive, theme-bound controls used by the native team-space pages."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.theme import DesignTokens, bind_theme


class SpaceActionRow(QWidget):
    """Keep ordinary toolbars on one line, reflow instead of clipping on narrow pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._widgets = []

    def addWidget(self, widget, stretch=0):
        self._widgets.append((widget, stretch))
        self.row.addWidget(widget, stretch)
        return widget

    def addStretch(self):
        self.row.addStretch()

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        required = sum(min(260, w.sizeHint().width()) if stretch else w.sizeHint().width()
                       for w, stretch in self._widgets if not w.isHidden())
        required += max(0, len(self._widgets) - 1) * self.row.spacing()
        vertical = self.width() < required
        self.row.setDirection(QBoxLayout.TopToBottom if vertical else QBoxLayout.LeftToRight)
        for widget, stretch in self._widgets:
            self.row.setAlignment(widget, Qt.AlignmentFlag(0) if stretch or not vertical else Qt.AlignLeft)


class SpaceSkeleton(QWidget):
    """A first-load placeholder confined to the missing content, never the shell."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("正在读取内容")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(18)
        for width in (0, 230, 0, 180):
            line = QLabel()
            line.setObjectName("SpaceSkeletonLine")
            line.setFixedHeight(18)
            if width:
                line.setMaximumWidth(width)
            layout.addWidget(line)
        self.apply_theme()
        bind_theme(self, self.apply_theme)

    def apply_theme(self, *_):
        self.setStyleSheet(f"QLabel#SpaceSkeletonLine {{background:{DesignTokens.bg_secondary};"
                           f"border-radius:{DesignTokens.radius_sm}px;}}")
