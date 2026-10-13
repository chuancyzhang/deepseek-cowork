"""Compact, theme-owned file table for a conversation answer."""
import os

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from core.deliverable_preview import DELIVERABLE_TYPES as DELIVERABLE_EXTENSIONS
from core.theme import DesignTokens, bind_theme


class FileTableRow(QPushButton):
    def __init__(self, path, workspace_dir, parent=None):
        super().__init__(parent)
        self.path = path
        try:
            inside_workspace = bool(workspace_dir) and os.path.commonpath([
                os.path.normcase(os.path.abspath(path)),
                os.path.normcase(os.path.abspath(workspace_dir)),
            ]) == os.path.normcase(os.path.abspath(workspace_dir))
            display_path = os.path.relpath(path, workspace_dir) if inside_workspace else path
        except ValueError:
            # Windows paths on different drives have no relative form.
            display_path = path
        self._path_text = display_path.replace("\\", "/")
        kind, type_label, self._icon_name = DELIVERABLE_EXTENSIONS.get(
            os.path.splitext(path)[1].lower(), ("file", "文件", "fa5s.file"))
        self.setObjectName("ConversationFileRow")
        self.setProperty("deliverableType", kind)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        self.setAccessibleName("打开文件 " + self._path_text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)
        self.icon_label = QLabel()
        self.path_label = QLabel()
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.type_label = QLabel(type_label)
        for label in (self.icon_label, self.path_label, self.type_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.path_label, 1)
        layout.addWidget(self.type_label)
        self.refresh_theme()

    def text(self):
        return self._path_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_path()

    def _elide_path(self):
        self.path_label.setText(self.path_label.fontMetrics().elidedText(
            self._path_text, Qt.ElideMiddle, max(0, self.path_label.width())))

    def refresh_theme(self):
        self.setStyleSheet(
            f"QPushButton#ConversationFileRow {{ background: transparent; border: none; "
            f"border-top: 1px solid {DesignTokens.border_subtle}; text-align: left; }}"
            f"QPushButton#ConversationFileRow:hover {{ background: {DesignTokens.bg_hover}; }}"
            f"QPushButton#ConversationFileRow:focus {{ background: {DesignTokens.primary_soft}; }}"
        )
        self.icon_label.setPixmap(qta.icon(self._icon_name, color=DesignTokens.icon_secondary).pixmap(14, 14))
        self.path_label.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_body}px; padding: 0; border: none;")
        self.type_label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px; padding: 0; border: none;")
        self.setFixedHeight(max(14, self.path_label.fontMetrics().height(), self.type_label.fontMetrics().height()) + 20)
        self._elide_path()


class ConversationFileTable(QFrame):
    fileActivated = Signal(str)
    PREVIEW_ROWS = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ConversationFileTable")
        self.setMinimumWidth(0)
        self._paths = []
        self._workspace_dir = ""
        self._expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.heading = QLabel()
        layout.addWidget(self.heading)
        self.rows_host = QFrame()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        layout.addWidget(self.rows_host)
        self.toggle = QPushButton()
        self.toggle.setObjectName("ConversationFilesToggle")
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.clicked.connect(self._toggle)
        layout.addWidget(self.toggle)
        bind_theme(self, self.refresh_theme, surface="conversation")
        self.refresh_theme()

    def set_files(self, paths, workspace_dir):
        paths = [path for path in paths if DELIVERABLE_EXTENSIONS.get(
            os.path.splitext(path)[1].lower(), ("file", "", ""))[0] != "image"]
        if paths == self._paths and workspace_dir == self._workspace_dir:
            return
        self._paths = list(paths)
        self._workspace_dir = workspace_dir
        self.heading.setText(f"文件 · {len(paths)}")
        self._rebuild_rows()
        self.setVisible(bool(paths))

    def _rebuild_rows(self):
        while self.rows_layout.count():
            widget = self.rows_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        visible = self._paths if self._expanded else self._paths[:self.PREVIEW_ROWS]
        for path in visible:
            row = FileTableRow(path, self._workspace_dir, self.rows_host)
            row.clicked.connect(lambda checked=False, value=path: self.fileActivated.emit(value))
            self.rows_layout.addWidget(row)
        self.toggle.setVisible(len(self._paths) > self.PREVIEW_ROWS)
        self.toggle.setText("收起文件" if self._expanded else f"展开其余 {len(self._paths) - self.PREVIEW_ROWS} 个文件")

    def _toggle(self):
        self._expanded = not self._expanded
        self._rebuild_rows()

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(
            f"QFrame#ConversationFileTable {{ background: {DesignTokens.bg_main}; "
            f"border: 1px solid {DesignTokens.border_subtle}; border-radius: {DesignTokens.radius_sm}px; }}")
        self.heading.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_body}px; font-weight: 600; padding: 10px 12px;")
        self.toggle.setStyleSheet(
            f"QPushButton#ConversationFilesToggle {{ background: transparent; border: none; "
            f"border-top: 1px solid {DesignTokens.border_subtle}; color: {DesignTokens.text_secondary}; "
            f"font-size: {DesignTokens.font_size_meta}px; text-align: left; padding: 9px 12px; }}"
            f"QPushButton#ConversationFilesToggle:hover {{ background: {DesignTokens.bg_hover}; color: {DesignTokens.text_primary}; }}"
            f"QPushButton#ConversationFilesToggle:focus {{ background: {DesignTokens.primary_soft}; }}")
        for index in range(self.rows_layout.count()):
            self.rows_layout.itemAt(index).widget().refresh_theme()
