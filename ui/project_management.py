"""Project/history browsing; all actions use the application's existing services."""
import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate, QStyleOptionViewItem
from PySide6.QtGui import QColor
from core.theme import DesignTokens


class SettingsNavigationDelegate(QStyledItemDelegate):
    """Section captions remain outside the row's selection and hover background."""
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), size.height() + (26 if index.data(Qt.UserRole + 1) else 0))

    def paint(self, painter, option, index):
        adjusted = QStyleOptionViewItem(option)
        group = index.data(Qt.UserRole + 1)
        if group:
            painter.save()
            painter.setPen(QColor(DesignTokens.text_secondary))
            rect = option.rect.adjusted(8, 0, 0, 0)
            rect.setHeight(26)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, group)
            painter.restore()
            adjusted.rect.setTop(adjusted.rect.top() + 26)
        super().paint(painter, adjusted, index)


class ProjectBrowser(QWidget):
    def __init__(self, owner, kind):
        super().__init__()
        self.owner = owner
        self.kind = kind
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索项目名称或路径" if kind == "projects" else "搜索对话")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.items = QListWidget()
        layout.addWidget(self.items, 1)
        self.items.setContextMenuPolicy(Qt.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._context_menu)
        self.items.itemActivated.connect(self.open_current)
        self.items.setStyleSheet(f"QListWidget {{border: none; background: {DesignTokens.bg_panel_strong};}} QListWidget::item {{padding: 12px;}} QListWidget::item:selected {{background: {DesignTokens.bg_sidebar_selected}; color: {DesignTokens.text_primary};}}")
        self.refresh()

    def _context_menu(self, position):
        item = self.items.itemAt(position)
        if item is None:
            return
        self.items.setCurrentItem(item)
        self.more(self.items.viewport().mapToGlobal(position))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, *_):
        previous = self.items.currentItem()
        identity = previous.data(Qt.UserRole) if previous else None
        scroll_position = self.items.verticalScrollBar().value()
        self.items.clear()
        if self.owner is None:
            return
        query = self.search.text().strip().casefold()
        records = (self.owner.config_manager.get_projects() if self.kind == "projects"
                   else self.owner.chat_storage.list_conversations())
        for record in records:
            if record.get("archived") or record.get("hidden") or (record.get("meta") or {}).get("archived"):
                continue
            if self.kind == "projects":
                key = record.get("path", "")
                title = record.get("name") or os.path.basename(key) or key
                detail = key
            else:
                key = record["id"]
                title = record.get("title") or "新对话"
                detail = (record.get("meta") or {}).get("workspace_dir") or "独立对话"
            if query and query not in f"{title} {detail}".casefold():
                continue
            workspace = self.owner._conversation_workspace_path(record) if self.kind != "projects" else ""
            display_detail = detail if self.kind == "projects" else (os.path.basename(workspace) if workspace else "独立对话")
            item = QListWidgetItem(f"{title}\n{display_detail}")
            item.setToolTip(detail)
            item.setData(Qt.UserRole, key)
            self.items.addItem(item)
            if key == identity:
                self.items.setCurrentItem(item)
        if self.items.count() == 0:
            empty = QListWidgetItem("没有匹配内容" if query else "暂无内容，可从左侧栏开始")
            empty.setFlags(Qt.NoItemFlags)
            self.items.addItem(empty)
        self.items.verticalScrollBar().setValue(scroll_position)

    def _key(self):
        item = self.items.currentItem()
        return item.data(Qt.UserRole) if item else None

    def open_current(self, *_):
        key = self._key()
        if not key or not self.owner.show_conversation_page():
            return
        if self.kind == "projects":
            self.owner.select_project(key)
        else:
            self.owner.activate_session(key)

    def memory(self):
        key = self._key()
        if key:
            self.owner.open_project_management("记忆", workspace_dir=key)

    def more(self, position=None):
        key = self._key()
        if not key:
            return
        if self.kind == "projects":
            self.owner.show_project_menu(key, self.items, position=position)
        else:
            self.owner.show_session_menu(key, self.items, position=position)
