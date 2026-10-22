"""Persistent settings sections: moving between tabs never discards a draft."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget, QSizePolicy

from core.theme import bind_theme
from ui.primitives import ProductSegmentedControl, PageScrollSafeComboBox, product_field_style


class SettingsSection(QWidget):
    sectionChanged = Signal(str)

    def __init__(self, pages, parent=None):
        super().__init__(parent)
        self.pages = dict(pages)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.tabs = ProductSegmentedControl([(label, label) for label in self.pages])
        layout.addWidget(self.tabs)
        self.compact_tabs = PageScrollSafeComboBox()
        self.compact_tabs.addItems(list(self.pages))
        self.compact_tabs.setAccessibleName("设置子页面")
        self.compact_tabs.currentTextChanged.connect(self.select)
        self.compact_tabs.hide()
        layout.addWidget(self.compact_tabs)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        for page in self.pages.values():
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.tabs.currentChanged.connect(self.select)
        self.select(next(iter(self.pages)))
        bind_theme(self, self.refresh_theme, surface="settings")

    def refresh_theme(self, _resolved=None):
        self.compact_tabs.setStyleSheet(product_field_style())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < self.tabs.minimumSizeHint().width()
        self.tabs.setVisible(not compact)
        self.compact_tabs.setVisible(compact)

    def select(self, label):
        if label not in self.pages:
            return
        self.tabs.set_current(label)
        self.compact_tabs.blockSignals(True)
        self.compact_tabs.setCurrentText(label)
        self.compact_tabs.blockSignals(False)
        self.stack.setCurrentWidget(self.pages[label])
        self.sectionChanged.emit(label)

    @property
    def current_label(self):
        return next(label for label, page in self.pages.items() if page is self.stack.currentWidget())
