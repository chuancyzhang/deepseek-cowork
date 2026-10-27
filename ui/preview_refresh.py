"""Detect newer file versions without replacing the preview a user is reading."""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import os

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Slot

from core.preview_versions import MANUAL_PREVIEW_EXTENSIONS, PreviewVersion, path_key, read_preview_version
from ui.worker_lifecycle import ui_work_stopping

log = logging.getLogger(__name__)


def managed_preview(path):
    return os.path.splitext(path or "")[1].lower() in MANUAL_PREVIEW_EXTENSIONS


def controller(window):
    value = getattr(window, "preview_refresh_controller", None)
    if value is None:
        value = PreviewRefreshController(window)
        window.preview_refresh_controller = value
    return value


@dataclass
class PreviewState:
    loaded: PreviewVersion | None = None
    observed: PreviewVersion | None = None
    loading: bool = False
    error: str = ""
    render_failed: bool = False
    mode: str = ""


class PreviewVersionWorker(QThread):
    def run(self):
        self.version, self.error = None, ""
        try:
            self.version = read_preview_version(self.request["path"])
        except Exception as exc:
            self.error = str(exc)


class PreviewRefreshController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.states = {}
        self.visit_key = None
        self.visit = 0
        self.serial = 0
        self.pending = None
        self.attempt = None
        self.page = None
        self.document = None
        self.html_key = self.document_key = None
        self.worker = PreviewVersionWorker(self)
        self.worker.finished.connect(self._checked)
        window.preview_version_worker = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._start)
        window.preview_version_timer = self.timer

    def key(self, path=None):
        path = path or getattr(self.window, "current_deliverable_path", "")
        return (str(getattr(self.window, "current_session_id", "") or ""), path_key(path)) if path else None

    def navigate(self, session_id, path):
        key = (str(session_id or ""), path_key(path)) if path else None
        if key == self.visit_key:
            return
        self.visit_key = key
        self.visit += 1
        self.pending = None
        self._cancel_attempt()
        browser = getattr(self.window, "browser_workbench", None)
        if browser:
            live = {(tab.session_id, path_key(tab.path)) for tab in browser.store.tabs.values() if tab.kind == "file"}
            self.states = {k: state for k, state in self.states.items() if k in live or k == key}

    def _valid(self, request):
        browser = getattr(self.window, "browser_workbench", None)
        return (not ui_work_stopping(self.window) and request["visit"] == self.visit
                and request["key"] == self.key()
                and (not browser or (not browser.closed and browser.file_active()))
                and getattr(self.window, "deliverable_edit_state", "idle") == "idle")

    def _state(self, path=None):
        return self.states.get(self.key(path))

    def can_reuse(self, path):
        state = self._state(path)
        html = os.path.splitext(path)[1].lower() in {".html", ".htm"}
        resident = self.html_key if html else self.document_key
        if not (state and state.loaded and resident == self.key(path)):
            return False
        if html:
            view = getattr(self.window, "deliverable_web_view", None)
            return bool(view and view.url().isLocalFile() and path_key(view.url().toLocalFile()) == path_key(path))
        return True

    def resources(self, path):
        state = self._state(path)
        return {resource for version in (state.loaded, state.observed) if version
                for resource, _ in version.resources} if state else set()

    def message(self):
        path = getattr(self.window, "current_deliverable_path", "")
        state = self._state(path)
        if not managed_preview(path) or not state or getattr(self.window, "deliverable_edit_state", "idle") != "idle":
            return "", False
        if state.loading:
            return ("正在刷新预览…" if state.loaded else "正在加载预览…"), False
        if state.observed and state.observed.source is None:
            kept = "已显示的内容保留，" if self.can_reuse(path) else ""
            return f"源文件已不可用；{kept}可恢复文件后刷新。", False
        if state.error:
            kept = "当前预览已保留。" if self.can_reuse(path) else ""
            return f"预览未更新，{kept}{state.error}", True
        if state.loaded and state.observed and state.loaded != state.observed:
            return ("文件已更新" if state.loaded.source != state.observed.source else "页面资源已更新"), True
        if state.loaded is None and state.observed and state.observed.source:
            return "文件已可用，可刷新加载预览。", True
        return "", False

    def sync_status(self):
        self.window._set_deliverable_status("")

    def cancel(self):
        request = self.attempt
        if request is None:
            return
        state = self.states[request["key"]]
        state.error, state.render_failed = "已取消加载，可刷新重试。", False
        if self.pending and self.pending["action"] == "render":
            self.pending = None
        log.info("preview.cancel session=%s path=%s attempt=%s", *request["key"], request["serial"])
        self._cancel_attempt()
        if self.can_reuse(request["path"]):
            self._show_resident(state.mode)
        else:
            self.window.deliverable_text_preview.setPlainText("预览已取消，可刷新重试。")
            self.window.preview_stack.setCurrentWidget(self.window.deliverable_text_preview)
        self.sync_status()

    def request_render(self, path, *, force=False, mode=None):
        self.navigate(getattr(self.window, "current_session_id", ""), path)
        key = self.key(path)
        state = self.states.setdefault(key, PreviewState())
        mode = mode or state.mode or ("html" if path.lower().endswith((".html", ".htm")) else
                        "sheet" if path.lower().endswith((".xls", ".xlsx")) else "office")
        if state.loading:
            self.sync_status()
            return
        if (self.can_reuse(path) and state.mode == mode and not force
                and not getattr(self.window, "current_deliverable_stale", False)):
            self._show_resident(mode)
            self.sync_status()
            from ui.file_refresh import schedule_file_refresh
            schedule_file_refresh(self.window)
            return
        self.serial += 1
        request = dict(key=key, path=path, visit=self.visit, action="render", mode=mode,
                       serial=self.serial, rebuild=state.render_failed)
        self.attempt = request
        state.loading, state.error = True, ""
        self.window.deliverable_render_loading = True
        if not self.can_reuse(path):
            self.window.deliverable_text_preview.setPlainText("正在加载预览…")
            self.window.preview_stack.setCurrentWidget(self.window.deliverable_text_preview)
        self.sync_status()
        log.info("preview.request session=%s path=%s attempt=%s manual=%s", key[0], path, self.serial, force)
        self._enqueue(request)

    def check(self, path):
        self.navigate(getattr(self.window, "current_session_id", ""), path)
        if self._state(path) is None:
            return
        self._enqueue(dict(key=self.key(path), path=path, visit=self.visit, action="check"))

    def _enqueue(self, request):
        # A directory notification cannot replace a user's queued refresh.
        if self.pending and self.pending["action"] == "render" and request["action"] == "check":
            return
        self.pending = request
        if not self.timer.isActive():
            self.timer.start(0)

    @Slot()
    def _start(self):
        if ui_work_stopping(self.window) or not self.pending:
            return
        if getattr(self.window, "preview_version_worker", None) is not None:
            return
        if not self.worker.wait(0):
            self.timer.start(10)
            return
        self.worker.request, self.pending = self.pending, None
        self.window.preview_version_worker = self.worker
        self.worker.start()

    @Slot()
    def _checked(self):
        request = self.worker.request
        try:
            if request["action"] == "render" and self.attempt is not request:
                return
            if not self._valid(request):
                log.debug("preview.discarded session=%s path=%s visit=%s", *request["key"], request["visit"])
                if self.attempt is request:
                    self._cancel_attempt()
                return
            state = self.states[request["key"]]
            if self.worker.error:
                log.warning("preview.check_failed session=%s path=%s error=%s", *request["key"], self.worker.error)
                if request["action"] == "render":
                    self._failed(request, self.worker.error, render_failed=False)
                else:
                    state.error = "无法检查文件变化，可稍后刷新重试。"
                    self.sync_status()
                return
            previous = state.observed
            state.observed = self.worker.version
            if previous != state.observed:
                log.info("preview.version session=%s path=%s source=%s changed=%s missing=%s",
                         *request["key"], (state.observed.source or "")[:12],
                         state.loaded is not None and state.loaded != state.observed,
                         state.observed.source is None)
            if not state.render_failed:
                state.error = ""
            self.window._watch_deliverable_paths()
            if hasattr(self.window, "_set_deliverable_controls_enabled"):
                self.window._set_deliverable_controls_enabled(request["path"])
            if request["action"] == "render":
                request["version"] = self.worker.version
                if self.worker.version.source is None:
                    self._failed(request, "源文件已不可用。", render_failed=False)
                elif (request["mode"] != "html" and self.can_reuse(request["path"])
                      and state.mode == request["mode"] and state.loaded == self.worker.version
                      and not request["rebuild"]):
                    self._show_resident(state.mode)
                    self._completed(request)
                elif request["mode"] == "html":
                    self._load_html(request)
                else:
                    self._load_document(request)
            else:
                log.debug("preview.checked session=%s path=%s changed=%s", *request["key"], state.loaded != state.observed)
                self.sync_status()
        except Exception as exc:
            log.exception("preview.start_failed session=%s path=%s", *request["key"])
            if self.attempt is request:
                self._failed(request, f"无法启动预览，请刷新重试。{exc}")
            elif self._valid(request):
                self.states[request["key"]].error = "无法检查文件变化，可稍后刷新重试。"
                self.sync_status()
        finally:
            self.window.preview_version_worker = None
            if self.pending and not ui_work_stopping(self.window):
                self.timer.start(0)

    def _load_html(self, request):
        from PySide6.QtWebEngineCore import QWebEnginePage
        window = self.window
        if not window._ensure_deliverable_web_view():
            self._failed(request, window.deliverable_web_configuration_error)
            return
        page = QWebEnginePage(window.deliverable_preview_profile, window.deliverable_web_view)
        self.page = page
        error = window._configure_deliverable_web_view(page=page)
        if error:
            self._failed(request, error)
            return
        request["scroll"] = window.deliverable_web_view.page().scrollPosition() if self.can_reuse(request["path"]) else None
        page.loadFinished.connect(lambda ok: self._html_finished(request, page, ok))
        url = QUrl.fromLocalFile(os.path.abspath(request["path"]))
        url.setQuery(f"cowork_refresh={self.visit}-{request['serial']}")
        page.setUrl(url)

    def _html_finished(self, request, page, ok):
        if self.page is not page or self.attempt is not request:
            return
        if not self._valid(request):
            self._cancel_attempt()
            return
        if not ok:
            self._failed(request, "页面加载失败，可刷新重试或用系统应用打开。")
            return
        window = self.window
        old = window.deliverable_web_view.page()
        window.deliverable_web_view.setPage(page)
        self.page = None
        old.deleteLater()
        self.html_key = request["key"]
        position = request.get("scroll")
        if position is not None:
            page.runJavaScript(f"window.scrollTo({position.x()}, {position.y()})")
        window._show_deliverable_web_preview()
        self._completed(request)
        window.handle_deliverable_render_finished(True)

    def _load_document(self, request):
        from ui.document_preview import DocumentPreview
        window = self.window
        old = getattr(window, "document_preview", None)
        if old and self.can_reuse(request["path"]):
            old.remember_position()
        browser = getattr(window, "browser_workbench", None)
        tab = browser.active_tab() if browser else None
        view_state = tab.view_state if tab and tab.kind == "file" else {}
        preview = DocumentPreview(window)
        preview.managed_refresh = True
        self.document = preview
        preview.simpleRequested.connect(window._render_simplified_office_deliverable)
        preview.externalRequested.connect(window.open_path_in_system)
        preview.previewReady.connect(lambda fingerprint: self._document_finished(request, preview, fingerprint))
        preview.previewFailed.connect(lambda message: self._document_failed(request, preview, message))
        preview.retry_button.clicked.disconnect()
        preview.retry_button.clicked.connect(lambda: self.request_render(request["path"], force=True, mode=request["mode"]))
        window.preview_stack.addWidget(preview)
        # The old document stays visible until the replacement has actually loaded.
        if not self.can_reuse(request["path"]):
            window.preview_stack.setCurrentWidget(preview)
        try:
            preview.open(request["path"], request["mode"], force=request["rebuild"], view_state=view_state)
            preview.cancel_button.hide()  # The shared status row owns cancel/retry.
        except Exception as exc:
            self._failed(request, str(exc))

    def _document_finished(self, request, preview, fingerprint):
        if self.document is not preview or self.attempt is not request:
            return
        if not self._valid(request):
            self._cancel_attempt()
            return
        old = getattr(self.window, "document_preview", None)
        self.window.document_preview = preview
        self.document = None
        self.document_key = request["key"]
        self.window.preview_stack.setCurrentWidget(preview)
        if old and old is not preview:
            self._dispose_document(old)
        if fingerprint:
            request["version"] = replace(request["version"], source=fingerprint)
        self._completed(request)

    def _document_failed(self, request, preview, message):
        if self.attempt is request:
            self._failed(request, message)
        elif (getattr(self.window, "document_preview", None) is preview
              and self.document_key == request["key"] and self._valid(request)):
            state = self.states[request["key"]]
            state.error, state.render_failed = str(message), True
            preview.status.hide()
            preview.retry_button.hide()
            self.sync_status()

    def _completed(self, request):
        state = self.states[request["key"]]
        state.loaded, state.mode = request["version"], request["mode"]
        state.loading, state.render_failed, state.error = False, False, ""
        self.attempt = None
        window = self.window
        window.deliverable_render_path = request["path"]
        window.deliverable_render_fingerprint = window._deliverable_fingerprint(request["path"])
        window.deliverable_render_loading = False
        window.current_deliverable_stale = False
        session = window.get_current_session()
        if session:
            session.deliverable_preview_rendered = True
        log.info("preview.loaded session=%s path=%s attempt=%s", *request["key"], request["serial"])
        self.sync_status()
        # Changes during loading remain pending, even if no further watcher event arrives.
        self.check(request["path"])

    def _failed(self, request, message, *, render_failed=True):
        if self.attempt is not request:
            return
        state = self.states[request["key"]]
        state.error, state.render_failed = str(message), render_failed
        log.warning("preview.failed session=%s path=%s attempt=%s error=%s", *request["key"], request["serial"], message)
        failed_document = None
        if self._valid(request) and self.document is not None and not self.can_reuse(request["path"]):
            # First-load failures retain the existing explicit simplified/system
            # recovery actions. A failed replacement must not obscure a good view.
            failed_document, self.document = self.document, None
            old = getattr(self.window, "document_preview", None)
            self.window.document_preview = failed_document
            self.document_key = None
            if old and old is not failed_document:
                self._dispose_document(old)
            failed_document.status.hide()
            failed_document.retry_button.hide()
        self._cancel_attempt()
        if self._valid(request):
            if self.can_reuse(request["path"]):
                self._show_resident(state.mode)
            elif failed_document is not None:
                self.window.preview_stack.setCurrentWidget(failed_document)
            else:
                self.window.deliverable_text_preview.setPlainText("预览未完成，可刷新重试。")
                self.window.preview_stack.setCurrentWidget(self.window.deliverable_text_preview)
            self.sync_status()

    def _show_resident(self, mode):
        if mode == "html":
            self.window._show_deliverable_web_preview()
        elif getattr(self.window, "document_preview", None):
            self.window.preview_stack.setCurrentWidget(self.window.document_preview)

    def _dispose_document(self, preview):
        preview.hide()
        preview.dispose()
        self.window.preview_stack.removeWidget(preview)
        preview.deleteLater()

    def _cancel_attempt(self):
        if self.attempt:
            state = self.states.get(self.attempt["key"])
            if state:
                state.loading = False
        self.attempt = None
        self.window.deliverable_render_loading = False
        if self.page is not None:
            page, self.page = self.page, None
            page.triggerAction(page.WebAction.Stop)
            page.deleteLater()
        if self.document is not None:
            preview, self.document = self.document, None
            self._dispose_document(preview)

    def dispose(self):
        self.timer.stop()
        self.pending = None
        self.visit += 1
        self._cancel_attempt()
