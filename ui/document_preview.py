"""PDF/Office/worksheet presentation inside the existing file tab surface."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid

from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget

from core.document_preview import cache_pdf, render_office_pdf
from core.sheet_preview import load_sheet_preview
from core.theme import DesignTokens, bind_theme
from ui.browser_panes import asset_path
from ui.primitives import product_button_style

log = logging.getLogger(__name__)


class _Result(QObject):
    ready = Signal(str, object)


class SheetPreviewBridge(QObject):
    def __init__(self, preview):
        super().__init__(preview)
        self.preview = preview

    @Slot(str)
    def ready(self, mode):
        if mode == "sheet":
            self.preview.sheet_ready = True
            self.preview.send_sheet()

    @Slot(str)
    def editorLoaded(self, identifier):
        if identifier == self.preview.generation:
            self.preview.refresh_theme()
            self.preview.stack.setCurrentWidget(self.preview.sheet_view)

    @Slot(str)
    def reportError(self, message):
        self.preview.show_error(message)

    @Slot(bool)
    def setDirty(self, value):
        # A read-only preview has deliberately no save/payload slots.
        if value:
            log.debug("sheet_preview.non_persistent_view_change")


class DocumentPreview(QWidget):
    simpleRequested = Signal(str)
    externalRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = ""
        self.mode = ""
        self.generation = ""
        self.cancel_event = threading.Event()
        self.sheet_view = self.pdf_view = None
        self.sheet_ready = False
        self.sheet_data = None
        self.view_state = {}
        self._signature = None
        self._thread = None
        self._closed = False
        self.result = _Result(self)
        self.result.ready.connect(self.finished)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel)
        self.retry_button = QPushButton("重试")
        self.retry_button.clicked.connect(lambda: self.open(self.source, self.mode, force=True, view_state=self.view_state))
        self.simple_button = QPushButton("简化预览")
        self.simple_button.clicked.connect(lambda: self.simpleRequested.emit(self.source))
        self.external_button = QPushButton("用系统应用打开")
        self.external_button.clicked.connect(lambda: self.externalRequested.emit(self.source))
        for item in (self.cancel_button, self.retry_button, self.simple_button, self.external_button):
            actions.addWidget(item)
            item.hide()
        actions.addStretch()
        layout.addLayout(actions)
        self.stack = QStackedWidget()
        self.empty = QWidget()
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)
        bind_theme(self, self.refresh_theme, surface="preview_shell")

    def refresh_theme(self, _resolved=None):
        self.status.setStyleSheet(f"color:{DesignTokens.text_secondary};background:transparent;")
        for item in (self.cancel_button, self.retry_button, self.simple_button, self.external_button):
            item.setStyleSheet(product_button_style("secondary"))
        if self.sheet_view and self.sheet_ready:
            theme = {"bg-primary": DesignTokens.bg_main, "bg-secondary": DesignTokens.bg_secondary,
                     "text-primary": DesignTokens.text_primary, "text-secondary": DesignTokens.text_secondary,
                     "border-subtle": DesignTokens.border_subtle, "primary": DesignTokens.primary,
                     "selection": DesignTokens.selection_bg}
            self.sheet_view.page().runJavaScript("window.coworkEditor && window.coworkEditor.setTheme(" + json.dumps(theme) + ")")

    def remember_position(self):
        state = self.view_state
        if self.stack.currentWidget() is self.sheet_view and self.sheet_ready:
            def remember(value):
                try:
                    value = json.loads(value) if isinstance(value, str) else value
                    if isinstance(value, dict):
                        state.update(sheet=value)
                except (TypeError, ValueError):
                    pass
            self.sheet_view.page().runJavaScript("JSON.stringify(window.coworkEditor && window.coworkEditor.getViewState())", remember)
        if self.stack.currentWidget() is self.pdf_view and self.pdf_view:
            state["pdf_fragment"] = self.pdf_view.url().fragment()

    def open(self, source, mode="", *, force=False, view_state=None):
        source = os.path.abspath(source)
        stat = os.stat(source)
        signature = (source, mode, stat.st_mtime_ns, stat.st_size)
        if self._signature == signature and not force:
            return
        self.remember_position()
        self.cancel_event.set()
        self.cancel_event = threading.Event()
        self.source, self.mode, self._signature = source, mode, signature
        self.view_state = view_state if view_state is not None else {}
        self.generation = uuid.uuid4().hex
        generation, cancel = self.generation, self.cancel_event
        self.stack.setCurrentWidget(self.empty)
        self.status.setText("正在生成版式预览…" if mode == "office" else "正在加载文件…")
        self.status.show()
        self.cancel_button.show()
        for item in (self.retry_button, self.simple_button, self.external_button):
            item.hide()
        result_signal = self.result.ready
        def run():
            try:
                if mode == "sheet":
                    result = {"kind": "sheet", **load_sheet_preview(source)}
                elif mode == "office":
                    pdf, fingerprint = render_office_pdf(source, cancel=cancel, force=force)
                    result = {"kind": "pdf", "path": pdf, "fingerprint": fingerprint}
                else:
                    result = {"kind": "pdf", "path": cache_pdf(source)}
            except Exception as exc:
                result = {"error": str(exc), "code": getattr(exc, "code", "preview_failed")}
            if not self._closed:
                try:
                    result_signal.emit(generation, result)
                except RuntimeError:
                    pass  # Host was destroyed after the worker completed.
        self._thread = threading.Thread(target=run, name="document-preview", daemon=True)
        self._thread.start()

    def cancel(self):
        self.cancel_event.set()
        self.generation = uuid.uuid4().hex
        self._signature = None
        self.show_error("已取消预览，源文件未修改。")

    def show_error(self, message):
        self.status.setText(str(message))
        self.status.show()
        self.cancel_button.hide()
        self.retry_button.show()
        self.simple_button.setVisible(os.path.splitext(self.source)[1].lower() in {".docx", ".xlsx", ".pptx"})
        self.external_button.show()
        self._signature = None

    def finished(self, generation, result):
        if self._closed or generation != self.generation or self.cancel_event.is_set():
            return
        self.cancel_button.hide()
        if result.get("error"):
            self.show_error(result["error"])
            return
        try:
            self.status.setText(result.get("warning", ""))
            self.status.setVisible(bool(result.get("warning")))
            if result["kind"] == "pdf":
                view = self.ensure_pdf()
                url = QUrl.fromLocalFile(result["path"])
                fragment = self.view_state.get("pdf_fragment")
                if fragment:
                    url.setFragment(fragment)
                view.setUrl(url)
                self.stack.setCurrentWidget(view)
            else:
                self.sheet_data = result["snapshot"]
                state = self.view_state.get("sheet", {})
                for sid, position in state.get("sheets", {}).items():
                    if sid in self.sheet_data["sheets"]:
                        self.sheet_data["sheets"][sid].update(position)
                self.sheet_data["coworkActiveSheet"] = state.get("activeSheet")
                self.ensure_sheet()
                self.send_sheet()
        except Exception as exc:
            self.show_error(str(exc))

    def ensure_pdf(self):
        if self.pdf_view is None:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self.pdf_profile = QWebEngineProfile(self)
            self.pdf_view = QWebEngineView(self)
            self.pdf_page = QWebEnginePage(self.pdf_profile, self.pdf_view)
            self.pdf_view.setPage(self.pdf_page)
            attributes = QWebEngineSettings.WebAttribute
            self.pdf_page.settings().setAttribute(attributes.PluginsEnabled, True)
            self.pdf_page.settings().setAttribute(attributes.PdfViewerEnabled, True)
            self.pdf_page.settings().setAttribute(attributes.LocalContentCanAccessRemoteUrls, False)
            self.pdf_profile.downloadRequested.connect(lambda item: item.cancel())
            self.pdf_view.loadFinished.connect(lambda ok: None if ok else self.show_error("PDF 加载失败，请重试或用系统应用打开。"))
            self.stack.addWidget(self.pdf_view)
        return self.pdf_view

    def ensure_sheet(self):
        if self.sheet_view is not None:
            return
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebChannel import QWebChannel
        self.sheet_profile = QWebEngineProfile(self)
        self.sheet_view = QWebEngineView(self)
        self.sheet_page = QWebEnginePage(self.sheet_profile, self.sheet_view)
        self.sheet_view.setPage(self.sheet_page)
        self.sheet_page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.sheet_profile.downloadRequested.connect(lambda item: item.cancel())
        self.sheet_channel = QWebChannel(self.sheet_page)
        self.sheet_channel.registerObject("deliverableEditorBridge", SheetPreviewBridge(self))
        self.sheet_page.setWebChannel(self.sheet_channel)
        self.stack.addWidget(self.sheet_view)
        self.sheet_view.loadFinished.connect(lambda ok: None if ok else self.show_error("工作表阅读器加载失败，请检查离线资源。"))
        self.sheet_view.setUrl(QUrl.fromLocalFile(asset_path("sheet.html")))

    def send_sheet(self):
        if not self.sheet_ready or self.sheet_data is None:
            return
        page = self.sheet_view.page()
        data = json.dumps(self.sheet_data, ensure_ascii=False, separators=(",", ":"))
        chunks = [data[index:index + 192 * 1024] for index in range(0, len(data), 192 * 1024)]
        page.runJavaScript("window.coworkEditor.setReadOnly(true); window.coworkEditor.beginLoad(" + json.dumps(self.generation) + "," + str(len(chunks)) + ")")
        for index, chunk in enumerate(chunks):
            page.runJavaScript("window.coworkEditor.appendLoadChunk(" + str(index) + "," + json.dumps(chunk, ensure_ascii=False) + ")")
        page.runJavaScript("window.coworkEditor.finishLoad()")
        self.sheet_data = None

    def dispose(self):
        if self._closed:
            return
        self._closed = True
        self.cancel_event.set()
        for name in ("pdf_view", "sheet_view"):
            view = getattr(self, name)
            if view:
                view.stop()
                view.page().setWebChannel(None)
                view.page().deleteLater()
                view.deleteLater()
