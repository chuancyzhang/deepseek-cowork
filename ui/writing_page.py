"""Conversation-owned writing surface with bounded Markdown formatting."""
from __future__ import annotations

import copy
import hashlib
import logging
import re

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextListFormat, QTextBlockFormat, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTextEdit,
                              QLabel, QPushButton, QComboBox, QMenu)

from core.theme import DesignTokens, bind_theme
from core.deliverable_editing import create_edit_session, atomic_save_session, sha256_file, ExternalModificationError
from core.workspace_pages import page_markdown, page_target, create_page_file, read_page
from ui.primitives import product_button_style, product_field_style, ProductInputDialog, ProductMessageBox
from ui.browser_panes import button, style_button

log = logging.getLogger(__name__)


def _escape(text):
    return re.sub(r'([\\`*_\[\]<>|])', r'\\\1', text).replace("\u2028", "  \n")


def document_markdown(document):
    """Export the supported Qt formats explicitly (Qt's exporter loses weight)."""
    lines = []
    previous_list = None
    block = document.begin()
    while block.isValid():
        heading = block.blockFormat().headingLevel()
        runs = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                value = _escape(fragment.text())
                leading = value[:len(value) - len(value.lstrip())]
                trailing = value[len(value.rstrip()):]
                middle = value.strip()
                if middle:
                    marker = ("**" if not heading and fmt.fontWeight() >= QFont.Bold else "") + ("*" if fmt.fontItalic() else "")
                    middle = marker + middle + marker
                    if fmt.isAnchor() and fmt.anchorHref():
                        url = fmt.anchorHref().replace("\\", "%5C").replace(")", "%29").replace(" ", "%20")
                        middle = "[" + middle + "](" + url + ")"
                    value = leading + middle + trailing
                runs.append(value)
            iterator += 1
        text = "".join(runs)
        text_list = block.textList()
        if text_list:
            ordered = text_list.format().style() in {QTextListFormat.ListDecimal,
                       QTextListFormat.ListLowerAlpha, QTextListFormat.ListUpperAlpha}
            prefix = f"{text_list.itemNumber(block) + 1}. " if ordered else "- "
            text = "  " * max(0, text_list.format().indent() - 1) + prefix + text
        elif heading:
            text = "#" * heading + " " + text
        else:
            text = re.sub(r"^(\s*)([#>+\-])", r"\1\\\2", text)
            text = re.sub(r"^(\s*\d+)([.)])(?=\s)", r"\1\\\2", text)
        if lines and not (text_list and previous_list == text_list):
            lines.append("")
        lines.append(text)
        previous_list = text_list
        block = block.next()
    return "\n".join(lines).rstrip()


class WritingTextEdit(QTextEdit):
    def setMarkdown(self, markdown):
        super().setMarkdown(markdown)
        self.document().setUndoRedoEnabled(False)
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            heading = fmt.headingLevel()
            fmt.setLineHeight(150, QTextBlockFormat.ProportionalHeight.value)
            fmt.setTopMargin(16 if heading and block.blockNumber() else 0)
            fmt.setBottomMargin(4 if block.textList() else 10)
            cursor.setBlockFormat(fmt)
            block = block.next()
        self.document().setUndoRedoEnabled(True)

    def insertFromMimeData(self, source):
        # Plain-text paste keeps unsupported tables/images from silently losing
        # content at save. Formatting is applied with the explicit page tools.
        if source.hasText():
            self.insertPlainText(source.text())


class PageSaveWorker(QThread):

    def __init__(self, path, markdown, session, parent):
        super().__init__(parent)
        self.path, self.markdown = path, markdown
        self.session = copy.deepcopy(session)
        self.error = None

    def run(self):
        try:
            data = self.markdown.encode("utf-8")
            if self.session is None:
                create_page_file(self.path, data)
                self.session, _ = create_edit_session(self.path)
                self.session.initial_fingerprint = hashlib.sha256(data).hexdigest()
            else:
                atomic_save_session(self.session, data)
        except Exception as exc:
            self.error = (str(getattr(exc, "code", "write_failed")), str(exc))


class WritingPage(QWidget):
    changed = Signal()
    saved = Signal()
    sourceRequested = Signal()
    recoveryRequested = Signal()

    def __init__(self, tab, workspace, parent=None, recovery=None):
        super().__init__(parent)
        self.tab, self.workspace = tab, str(workspace)
        self.edit_session = None
        self.worker = None
        self.revision = 0
        self._loading = True
        self._saved_content = None
        self._content_cache = None
        self._error = False
        self._after_save = None
        self.setObjectName("WritingPage")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(16, 8, 16, 16)
        self.outer.setSpacing(8)
        toolbar = QWidget(self)
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.format = QComboBox(toolbar)
        self.format.addItems(["正文", "标题 1", "标题 2", "标题 3"])
        self.format.setAccessibleName("段落格式")
        self.format.activated.connect(self.set_heading)
        row.addWidget(self.format)
        self.bold = button("B", "加粗 (Ctrl+B)", self.toggle_bold, toolbar)
        self.italic = button("I", "斜体 (Ctrl+I)", self.toggle_italic, toolbar)
        self.more = button("⋯", "列表、链接、撤销与重做", self.format_menu, toolbar)
        for control in (self.bold, self.italic, self.more):
            row.addWidget(control)
        row.addStretch()
        self.save_button = QPushButton("保存", toolbar)
        self.save_button.setToolTip("保存到工作区 (Ctrl+S)")
        self.save_button.clicked.connect(lambda: self.save())
        row.addWidget(self.save_button)
        self.outer.addWidget(toolbar)
        self.content_host = QWidget(self)
        self.content_host.setMaximumWidth(DesignTokens.message_max_width)
        content_layout = QVBoxLayout(self.content_host)
        content_layout.setContentsMargins(0, 8, 0, 0)
        content_layout.setSpacing(16)
        self.title = QLineEdit(self.content_host)
        self.title.setPlaceholderText("无标题页")
        self.title.setAccessibleName("页面标题")
        content_layout.addWidget(self.title)
        self.editor = WritingTextEdit(self.content_host)
        self.editor.setPlaceholderText("开始写作…")
        self.editor.setAccessibleName("页面正文")
        self.editor.setAcceptRichText(False)
        self.editor.setMarkdown("")
        content_layout.addWidget(self.editor, 1)
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.addStretch(1)
        content_row.addWidget(self.content_host, 100)
        content_row.addStretch(1)
        self.outer.addLayout(content_row, 1)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.outer.addWidget(self.status)
        self.recovery_actions = QWidget(self)
        actions = QHBoxLayout(self.recovery_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.save_as_button = QPushButton("另存为", self.recovery_actions)
        self.reload_button = QPushButton("重新加载", self.recovery_actions)
        self.save_as_button.clicked.connect(lambda: self.save(as_copy=True))
        self.reload_button.clicked.connect(self.reload)
        actions.addWidget(self.save_as_button)
        actions.addWidget(self.reload_button)
        actions.addStretch()
        self.outer.addWidget(self.recovery_actions)
        self.recovery_actions.hide()
        self.recovery_notice = QLabel(self)
        self.recovery_notice.setWordWrap(True)
        self.recovery_notice.hide()
        self.recovery_retry = QPushButton("重试恢复保存", self)
        self.recovery_retry.clicked.connect(self.recoveryRequested.emit)
        self.recovery_retry.hide()
        self.outer.addWidget(self.recovery_notice)
        self.outer.addWidget(self.recovery_retry, alignment=Qt.AlignLeft)
        self.recovery_revision = -1
        self._shortcuts = []
        for sequence, callback in (("Ctrl+S", lambda: self.save()), ("Ctrl+B", self.toggle_bold),
                                   ("Ctrl+I", self.toggle_italic), ("Ctrl+K", self.insert_link)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        if recovery:
            self.title.setText(str(recovery.get("title") or ""))
            self.editor.setMarkdown(str(recovery.get("body") or ""))
            self._saved_content = recovery.get("saved_content")
            if tab.path:
                try:
                    self.edit_session, _ = create_edit_session(tab.path)
                    self.edit_session.initial_fingerprint = str(recovery.get("fingerprint") or "")
                except Exception as exc:
                    self.show_error(str(exc))
        elif tab.path:
            self.load_file()
        self.editor.document().setModified(self._saved_content != self.content())
        self.title.textChanged.connect(self.content_changed)
        self.editor.textChanged.connect(self.content_changed)
        self.editor.cursorPositionChanged.connect(self.sync_format)
        self._loading = False
        self.sync_status()
        bind_theme(self, self.refresh_theme, surface="preview_shell")

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QWidget#WritingPage{{background:{DesignTokens.bg_main};}}")
        self.title.setStyleSheet(product_field_style() +
            f"QLineEdit{{border:none;background:transparent;font-size:{DesignTokens.font_size_hero}px;font-weight:600;padding:8px 0;}}")
        self.editor.setStyleSheet(f"QTextEdit{{background:transparent;color:{DesignTokens.text_primary};border:none;"
                                 f"font-size:{DesignTokens.font_size_body}px;selection-background-color:{DesignTokens.selection_bg};}}")
        self.status.setStyleSheet(f"color:{DesignTokens.text_secondary};background:transparent;")
        self.save_button.setStyleSheet(product_button_style("primary"))
        for control in (self.save_as_button, self.reload_button, self.recovery_retry):
            control.setStyleSheet(product_button_style("secondary"))
        self.recovery_notice.setStyleSheet(f"color:{DesignTokens.error_text};background:transparent;")
        for control in (self.bold, self.italic, self.more):
            style_button(control)
        self.format.setStyleSheet(product_field_style())
        self.format.setStyleSheet(self.format.styleSheet() +
            f"QComboBox{{border:1px solid {DesignTokens.border};border-radius:6px;padding:0 8px;min-height:{DesignTokens.control_height_sm}px;}}"
            "QComboBox::drop-down{border:0;width:18px;}")

    def content(self):
        key = (self.title.text(), self.editor.document().revision())
        if self._content_cache is None or self._content_cache[0] != key:
            self._content_cache = (key, {"title": key[0], "body": document_markdown(self.editor.document())})
        return dict(self._content_cache[1])

    def recovery_result(self, revision, error=""):
        if revision < self.recovery_revision:
            return
        self.recovery_revision = revision
        self.recovery_notice.setText("页面恢复副本未能保存，当前编辑内容仍在。" if error else "")
        self.recovery_notice.setToolTip(error)
        self.recovery_notice.setVisible(bool(error))
        self.recovery_retry.setVisible(bool(error))

    @property
    def dirty(self):
        return self._saved_content is None or self.editor.document().isModified() or self.title.text() != self._saved_content["title"]

    def snapshot(self):
        content = self.content()
        return dict(content, id=self.tab.id, path=self.tab.path, workspace=self.workspace,
                    saved_content=self._saved_content,
                    fingerprint=self.edit_session.initial_fingerprint if self.edit_session else "")

    def content_changed(self):
        if self._loading:
            return
        self.revision += 1
        self.tab.title = (self.title.text().strip() or "无标题页") + (" •" if self.dirty else "")
        self.sync_status()
        self.changed.emit()

    def sync_status(self):
        if self.worker is not None:
            return
        if self._error:
            return
        self.status.setText("未保存" if self.dirty else "已保存")
        self.status.setToolTip(self.tab.path or self.workspace)

    def sync_format(self):
        self.format.setCurrentIndex(min(3, self.editor.textCursor().blockFormat().headingLevel()))

    def toggle_bold(self):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if self.editor.currentCharFormat().fontWeight() >= QFont.Bold else QFont.Bold)
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()

    def toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.currentCharFormat().fontItalic())
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFocus()

    def set_heading(self, level):
        cursor = self.editor.textCursor()
        fmt = cursor.blockFormat()
        fmt.setHeadingLevel(int(level))
        fmt.setTopMargin(16 if level else 0)
        fmt.setBottomMargin(10)
        fmt.setLineHeight(150, QTextBlockFormat.ProportionalHeight.value)
        cursor.mergeBlockFormat(fmt)
        char = QTextCharFormat()
        char.setFontPointSize(float(DesignTokens.font_size_body + (8 - 2 * level if level else 0)))
        char.setFontWeight(QFont.Bold if level else QFont.Normal)
        cursor.mergeBlockCharFormat(char)
        self.editor.setFocus()

    def set_list(self, ordered):
        cursor = self.editor.textCursor()
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.ListDecimal if ordered else QTextListFormat.ListDisc)
        fmt.setIndent(1)
        cursor.createList(fmt)
        self.editor.setFocus()

    def insert_link(self):
        url, accepted = ProductInputDialog.getText(self, "插入链接", "链接地址")
        if not accepted:
            return
        url = url.strip()
        if not re.match(r"^(https?://|mailto:)", url, re.I):
            self.status.setText("请填写 http、https 或 mailto 链接。")
            return
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(url)
        fmt.setFontUnderline(True)
        cursor.insertText(cursor.selectedText() or url, fmt)
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(QTextCharFormat())
        self.editor.setFocus()

    def format_menu(self):
        menu = QMenu(self)
        menu.addAction("无序列表", lambda: self.set_list(False))
        menu.addAction("有序列表", lambda: self.set_list(True))
        menu.addAction("插入链接…", self.insert_link)
        menu.addSeparator()
        menu.addAction("撤销", self.editor.undo).setEnabled(self.editor.document().isUndoAvailable())
        menu.addAction("重做", self.editor.redo).setEnabled(self.editor.document().isRedoAvailable())
        if self.tab.path:
            menu.addSeparator()
            menu.addAction("另存为…", lambda: self.save(as_copy=True))
            menu.addAction("预览与源码编辑", self.sourceRequested.emit)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def save(self, *, as_copy=False, after_save=None, target=None):
        if self.worker is not None:
            return False
        if self.tab.path and not as_copy and self.edit_session is None:
            self.show_error("原文件不可用，内容已保留。请另存为新文件。")
            return False
        path = target or ("" if as_copy else self.tab.path)
        if not path:
            name, accepted = ProductInputDialog.getText(self, "保存页面", self.workspace,
                                text=(self.title.text().strip() or "无标题页") + ".md")
            if not accepted:
                return False
            try:
                path = page_target(self.workspace, name)
            except Exception as exc:
                self.show_error(str(exc))
                return False
        snapshot = self.content()
        self._saving_content = snapshot
        self._saving_revision = self.revision
        self._saving_path = path
        self._after_save = after_save
        self.status.setText("正在保存…")
        self._error = False
        self.recovery_actions.hide()
        self.save_button.setEnabled(False)
        self.worker = PageSaveWorker(path, page_markdown(**snapshot),
                         None if as_copy or not self.tab.path else self.edit_session, self)
        self.worker.finished.connect(self.save_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        log.info("writing_page.save_start session=%s page=%s revision=%s", self.tab.session_id, self.tab.id, self.revision)
        self.worker.start()
        return True

    def save_finished(self):
        worker = self.worker
        if worker.error:
            self.save_failed(*worker.error)
        else:
            self.save_completed(worker.session)

    def save_completed(self, session):
        self.edit_session = session
        self.tab.path = session.path
        self.tab.kind = "file"
        self.tab.view_state["writing_page"] = True
        self._saved_content = self._saving_content
        callback = self._after_save if self.revision == self._saving_revision else None
        if self.revision == self._saving_revision:
            self.editor.document().setModified(False)
        self.worker = None
        self._after_save = None
        self.save_button.setEnabled(True)
        self.recovery_actions.hide()
        self._error = False
        self.content_changed()
        self.saved.emit()
        log.info("writing_page.save_finish session=%s page=%s dirty=%s", self.tab.session_id, self.tab.id, self.dirty)
        if callback:
            QTimer.singleShot(0, callback)

    def save_failed(self, code, message):
        self.worker = None
        self._after_save = None
        self.save_button.setEnabled(True)
        self.show_error(f"保存失败：{message} 内容已保留。")
        log.warning("writing_page.save_failed session=%s page=%s reason=%s", self.tab.session_id, self.tab.id, code)

    def show_error(self, message):
        self._error = True
        self.status.setText(message)
        self.recovery_actions.show()
        self.reload_button.setVisible(bool(self.tab.path))

    def load_file(self):
        session, _ = create_edit_session(self.tab.path)
        content = read_page(self.tab.path)
        if content is None:
            raise ValueError("文件已不再是写作页面，请使用预览或源码编辑。")
        if sha256_file(self.tab.path) != session.initial_fingerprint:
            raise ExternalModificationError("页面在读取时发生变化，请重新加载。")
        self.edit_session = session
        self._loading = True
        self.title.setText(content["title"])
        self.editor.setMarkdown(content["body"])
        self._saved_content = self.content()
        self.editor.document().setModified(False)
        self._loading = False
        self.recovery_actions.hide()
        self._error = False

    def reload(self):
        if self.worker is not None:
            return
        if ProductMessageBox.question(self, "重新加载页面", "重新加载将放弃当前页面的未保存修改。", defaultButton=ProductMessageBox.No) != ProductMessageBox.Yes:
            return
        try:
            # Inspect before replacing the current content.
            self.load_file()
            self.content_changed()
        except Exception as exc:
            self.show_error(str(exc))

    def can_close(self, close_callback):
        if self.worker is not None:
            self.status.setText("页面正在保存，请保存完成后再关闭。")
            return False
        if not self.dirty or (not self.tab.path and not self.title.text() and not self.editor.toPlainText()):
            return True
        answer = ProductMessageBox.question(self, "关闭页面", "页面有未保存修改。",
                    ProductMessageBox.Save | ProductMessageBox.Discard | ProductMessageBox.Cancel,
                    ProductMessageBox.Cancel)
        if answer == ProductMessageBox.Save:
            self.save(after_save=close_callback)
            return False
        return answer == ProductMessageBox.Discard

    def close_pane(self):
        self.hide()
