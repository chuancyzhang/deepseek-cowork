"""Coordinates the existing file workbench with conversation-owned web/PTY tabs."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, Qt, QUrl, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
                              QPushButton, QStackedWidget, QMenu, QFileDialog, QApplication)

from core.theme import DesignTokens, bind_theme
from core.env_utils import get_app_data_dir
from core.workbench import workbench_bridge, WorkbenchError, path_key
from core.workbench_terminal import terminal_profiles, TerminalSession
from ui.browser_panes import BrowserPane, TerminalPane, input_url, button, style_button
from ui.primitives import product_field_style, product_button_style, ProductMessageBox

log = logging.getLogger(__name__)


class BrowserWorkbench(QObject):
    def __init__(self, window, file_surface):
        super().__init__(window)
        self.window = window
        self.bridge = workbench_bridge()
        if not self.bridge.available:
            # A newly created desktop host starts with an empty runtime registry.
            from core.workbench import WorkbenchStore
            self.bridge.store = WorkbenchStore()
        self.store = self.bridge.store
        self.bridge.available = True
        self.bridge.requested.connect(self.handle_request, Qt.QueuedConnection)
        self.stack = QStackedWidget()
        self.stack.setObjectName("BrowserContentStack")
        self.file_surface = file_surface
        self.stack.addWidget(file_surface)
        self.start_page = self.make_start_page()
        self.stack.addWidget(self.start_page)
        self.stack.setCurrentWidget(self.start_page)
        self.panes = {}
        self._profile = None
        self.closed = False
        self.restoring = False
        self._signature = None
        self.downloads = []
        header = window.file_header_controls.layout()
        self.add_button = button("+", "新标签页", self.new_tab)
        header.insertWidget(header.count() - 1, self.add_button)
        window.file_tab_strip.activateRequested.disconnect()
        window.file_tab_strip.closeRequested.disconnect()
        window.file_tab_strip.activateRequested.connect(self.activate)
        window.file_tab_strip.closeRequested.connect(self.close_tab)
        bind_theme(self.stack, self.refresh_theme, surface="preview_shell")

    def make_start_page(self):
        widget = QWidget()
        widget.setObjectName("BrowserStartPage")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        self.start_address = QLineEdit()
        self.start_address.setPlaceholderText("搜索或输入网址")
        self.start_address.setClearButtonEnabled(True)
        self.start_address.returnPressed.connect(self.open_start_address)
        layout.addWidget(self.start_address)
        title = QLabel("工具")
        title.setObjectName("BrowserStartTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.files_button = QPushButton("文件")
        self.files_button.setMinimumHeight(42)
        self.files_button.clicked.connect(lambda: self.window.set_file_navigator_visible(True, reason="browser_start"))
        self.terminal_button = QPushButton("终端")
        self.terminal_button.setMinimumHeight(42)
        self.terminal_button.clicked.connect(self.terminal_menu)
        row.addWidget(self.files_button); row.addWidget(self.terminal_button)
        layout.addLayout(row)
        self.start_notice = QLabel()
        self.start_notice.setWordWrap(True)
        self.start_notice.hide()
        layout.addWidget(self.start_notice)
        layout.addStretch()
        self.browser_settings = QPushButton("浏览数据与下载…")
        self.browser_settings.clicked.connect(self.settings_menu)
        layout.addWidget(self.browser_settings, alignment=Qt.AlignLeft)
        self.refresh_theme()
        return widget

    def refresh_theme(self, _resolved=None):
        self.start_address.setStyleSheet(product_field_style())
        for widget in (self.files_button, self.terminal_button, self.browser_settings):
            widget.setStyleSheet(product_button_style("secondary" if widget is not self.browser_settings else "ghost"))
        self.start_notice.setStyleSheet(f"color:{DesignTokens.error_text};background:transparent;")
        if hasattr(self, "add_button"):
            style_button(self.add_button)

    def session_id(self):
        return str(self.window.current_session_id or "")

    def resolve_tab(self, session_id, key):
        for tab in self.store.list(session_id):
            if key in {tab.id, "@" + tab.id} or (tab.kind == "file" and path_key(tab.path) == path_key(key)):
                return tab
        raise WorkbenchError("tab_unavailable", "标签已关闭，请重新打开。")

    def active_tab(self, session_id=None):
        session_id = session_id or self.session_id()
        key = self.store.active.get(session_id)
        return self.store.get(session_id, key) if key else None

    def file_active(self):
        tab = self.active_tab()
        return tab is not None and tab.kind == "file"

    def _compat_state(self, session_id):
        state = self.window.get_session(session_id)
        if state is None:
            return
        state.open_file_paths = [tab.path for tab in self.store.list(session_id) if tab.kind == "file"]
        tab = self.active_tab(session_id)
        state.selected_deliverable_path = tab.path if tab and tab.kind == "file" else ""

    def _import_legacy_state(self, state):
        if state and not self.store.list(state.session_id):
            for path in getattr(state, "open_file_paths", []) or []:
                if path:
                    self.store.open(state.session_id, "file", path=path,
                                    activate=path == state.selected_deliverable_path)

    def sync(self, state=None):
        if self.closed:
            return
        state = state or self.window.get_current_session()
        if state is None:
            return
        self._import_legacy_state(state)
        tabs = self.store.list(state.session_id)
        entries = [dict(tab.snapshot(), key=tab.path if tab.kind == "file" else "@" + tab.id) for tab in tabs]
        active = self.active_tab(state.session_id)
        active_key = (active.path if active.kind == "file" else "@" + active.id) if active else ""
        signature = json.dumps([entries, active_key], ensure_ascii=False, sort_keys=True)
        if signature != self._signature:
            self.window.file_tab_strip.set_entries(entries, active_key)
            self._signature = signature
        self.window.file_tab_strip.setVisible(bool(tabs))
        self.window.file_header_identity_label.setVisible(not bool(tabs))
        self._compat_state(state.session_id)

    def remember_file(self, path, state):
        if not path or state is None:
            return False
        previous = self.active_tab(state.session_id)
        if previous and previous.kind == "new":
            self.store.close(state.session_id, previous.id)
        tab = self.store.open(state.session_id, "file", path=path, activate=True)
        if state.session_id == self.session_id():
            self.park_other_webpages(tab.id)
            self.stack.setCurrentWidget(self.file_surface)
            self.sync(state)
        self._compat_state(state.session_id)
        return True

    def capture_file_position(self):
        tab = self.active_tab()
        if tab and tab.kind == "file":
            preview = self.window.preview_stack.currentWidget()
            if hasattr(preview, "verticalScrollBar"):
                tab.view_state["scroll"] = preview.verticalScrollBar().value()

    def restore_session(self, state):
        self._import_legacy_state(state)
        self.restoring = True
        try:
            tab = self.active_tab(state.session_id)
            if tab is None:
                tab = self.store.open(state.session_id, "new", activate=True)
            self.show_tab(tab, render_file=False)
            self.sync(state)
        finally:
            self.restoring = False

    def park_other_webpages(self, active_id):
        for pane in self.panes.values():
            if isinstance(pane, BrowserPane) and pane.tab.id != active_id and self.stack.indexOf(pane) >= 0:
                size = pane.size()
                self.stack.removeWidget(pane)
                pane.park(size)

    def show_tab(self, tab, render_file=True):
        self.park_other_webpages(tab.id)
        self.store.active[tab.session_id] = tab.id
        self._compat_state(tab.session_id)
        if tab.kind == "file":
            self.stack.setCurrentWidget(self.file_surface)
            self.window.current_deliverable_path = tab.path
            self.window.current_preview_path = tab.path
            if render_file:
                self.window.activate_file_tab(tab.path)
                position = tab.view_state.get("scroll")
                preview = self.window.preview_stack.currentWidget()
                if position is not None and hasattr(preview, "verticalScrollBar"):
                    QTimer.singleShot(0, lambda: preview.verticalScrollBar().setValue(position))
        else:
            self.window.current_deliverable_path = ""
            self.window.current_preview_path = ""
            self.window.preview_text.clear()
            self.window.preview_stack.setCurrentWidget(self.window.preview_text)
            pane = None if tab.kind == "new" else self.ensure_pane(tab)
            if isinstance(pane, BrowserPane):
                pane.mount(self.stack)
            else:
                self.stack.setCurrentWidget(pane or self.start_page)
        self.sync()

    def activate(self, key):
        try:
            tab = self.resolve_tab(self.session_id(), key)
            if self.active_tab() is not tab:
                if not self.window.confirm_leave_deliverable_edit("切换标签"):
                    return False
                self.capture_file_position()
            self.show_tab(tab)
            return True
        except Exception as exc:
            self.window.add_system_toast(str(exc), "error")
            return False

    def new_tab(self):
        if not self.window.confirm_leave_deliverable_edit("新建标签"):
            return
        self.capture_file_position()
        tab = self.store.open(self.session_id(), "new", activate=True)
        self.show_tab(tab)
        self.start_address.clear()
        self.start_address.setFocus()

    def open_start_address(self):
        try:
            self.open_web(self.session_id(), input_url(self.start_address.text()).toString(), activate=True)
            self.start_notice.hide()
        except Exception as exc:
            self.start_notice.setText(str(exc)); self.start_notice.show()

    def open_web(self, session_id, url, *, activate=False):
        url = input_url(url).toString()
        if activate and not self.window.confirm_leave_deliverable_edit("打开网页"):
            raise WorkbenchError("cancelled", "已取消打开网页，编辑内容已保留。")
        previous = self.active_tab(session_id)
        tab = self.store.open(session_id, "web", url=url, title=url, activate=activate)
        try:
            self.ensure_pane(tab)
        except Exception:
            self.store.close(session_id, tab.id)
            raise
        if activate:
            if previous and previous.kind == "new":
                self.store.close(session_id, previous.id)
            self.show_tab(tab)
        self.sync()
        return tab

    def terminal_menu(self):
        try:
            profiles = terminal_profiles()
            preferred = self.window.config_manager.get("browser_terminal_profile", "bash")
            profiles.sort(key=lambda item: item["id"] != preferred)
            menu = QMenu(self.terminal_button)
            for profile in profiles:
                menu.addAction(profile["name"], lambda current=profile: self.open_user_terminal(current["id"]))
            if not profiles:
                self.start_notice.setText("没有可用终端，请检查 Git Bash 运行时或 Windows PowerShell。")
                self.start_notice.show()
                return
            menu.exec(self.terminal_button.mapToGlobal(self.terminal_button.rect().bottomLeft()))
        except Exception as exc:
            self.window.add_system_toast(str(exc), "error")

    def open_user_terminal(self, shell):
        try:
            if not self.window.confirm_leave_deliverable_edit("打开终端"):
                return
            tab = self.open_terminal(self.session_id(), shell)
            self.window.config_manager.set("browser_terminal_profile", shell)
            previous = self.active_tab()
            if previous and previous.kind == "new":
                self.store.close(previous.session_id, previous.id)
            self.show_tab(tab)
        except Exception as exc:
            self.window.add_system_toast(str(exc), "error")

    def open_terminal(self, session_id, shell="", cwd=""):
        state = self.window.get_session(session_id)
        if state is None:
            raise WorkbenchError("session_unavailable", "原聊天已关闭。")
        profiles = terminal_profiles()
        shell = shell or self.window.config_manager.get("browser_terminal_profile", "bash")
        profile = next((item for item in profiles if item["id"] == shell), None)
        if profile is None:
            raise WorkbenchError("shell_unavailable", "所选终端不可用，请选择已安装的终端。")
        cwd = os.path.abspath(cwd or self.window._workspace_dir_for_state(state) or "")
        if not os.path.isdir(cwd):
            raise WorkbenchError("cwd_unavailable", "终端启动目录不存在。")
        tab = self.store.open(session_id, "terminal", cwd=cwd, shell=shell, title=profile["name"], status="starting")
        try:
            self.ensure_pane(tab)
        except Exception:
            self.store.close(session_id, tab.id)
            raise
        self.sync()
        return tab

    def browser_profile(self):
        if self._profile is None:
            from PySide6.QtWebEngineCore import QWebEngineProfile
            self._profile = QWebEngineProfile("cowork-browser", self)
            generation = str(self.window.config_manager.get("browser_storage_generation", "default"))
            self._profile.setPersistentStoragePath(os.path.join(get_app_data_dir(), "browser", generation, "storage"))
            self._profile.setCachePath(os.path.join(get_app_data_dir(), "browser", generation, "cache"))
            self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
            self._profile.downloadRequested.connect(self.handle_download)
        return self._profile

    def ensure_pane(self, tab):
        if tab.id in self.panes:
            return self.panes[tab.id]
        if tab.kind == "web":
            pane = BrowserPane(tab, self.store, self.bridge, self.browser_profile())
            def new_window(holder):
                # New windows retain the origin conversation and never steal focus.
                created = self.open_web(tab.session_id, "about:blank")
                holder.append(self.panes[created.id].page)
            pane.openWindow.connect(new_window)
        elif tab.kind == "terminal":
            profile = next((item for item in terminal_profiles() if item["id"] == tab.shell), None)
            if profile is None:
                raise WorkbenchError("shell_unavailable", "终端已不可用。")
            terminal = TerminalSession(tab.session_id, tab.id, profile, tab.cwd)
            pane = TerminalPane(tab, self.store, self.bridge, terminal)
            pane.restartRequested.connect(lambda: self.restart_terminal(tab))
            terminal.start()
        else:
            raise WorkbenchError("invalid_kind", "标签不是网页或终端。")
        pane.changed.connect(self.sync)
        self.panes[tab.id] = pane
        pane.resize(self.stack.size())
        if isinstance(pane, BrowserPane):
            pane.park(self.stack.size())
        else:
            self.stack.addWidget(pane)
        return pane

    def restart_terminal(self, tab):
        pane = self.panes.get(tab.id)
        if pane and pane.terminal.status in {"starting", "running"}:
            return
        if pane:
            pane.close_pane(); self.stack.removeWidget(pane); pane.deleteLater()
            del self.panes[tab.id]
        self.store.set_owner(tab.session_id, tab.id, "available")
        self.ensure_pane(tab)
        if self.session_id() == tab.session_id and self.active_tab() is tab:
            self.show_tab(tab)

    def close_tab(self, key, *, session_id=None, agent=False, request=None):
        session_id = session_id or self.session_id()
        tab = self.resolve_tab(session_id, key)
        active = self.active_tab(session_id) is tab
        if tab.kind == "file" and active and session_id == self.session_id():
            if agent and self.file_status(tab)["unsaved"]:
                raise WorkbenchError("unsaved_changes", "文件有用户未保存修改，不能关闭。")
            if not agent and not self.window.confirm_leave_deliverable_edit("关闭标签"):
                return False
        pane = self.panes.get(tab.id)
        self.bridge.cancel_tab(session_id, tab.id, exclude_request=request)
        if pane:
            pane.close_pane(); self.stack.removeWidget(pane); pane.deleteLater()
            del self.panes[tab.id]
        self.store.close(session_id, tab.id)
        self._compat_state(session_id)
        if active and session_id == self.session_id():
            next_tab = self.active_tab(session_id)
            if next_tab is None:
                next_tab = self.store.open(session_id, "new", activate=True)
            self.show_tab(next_tab)
        self.sync()
        return True

    def file_status(self, tab):
        session = getattr(self.window, "deliverable_edit_session", None)
        editing = bool(session and self.session_id() == tab.session_id and path_key(session.path) == path_key(tab.path))
        fingerprint = ""
        if os.path.isfile(tab.path):
            from core.deliverable_editing import sha256_file
            fingerprint = sha256_file(tab.path)
        return {"path": tab.path, "exists": os.path.isfile(tab.path), "file_version": fingerprint,
                "unsaved": bool(editing and self.window.deliverable_edit_dirty),
                "version_changed": bool(editing and fingerprint != session.initial_fingerprint),
                "conflict": bool(editing and self.window.deliverable_edit_dirty and fingerprint != session.initial_fingerprint)}

    def handle_request(self, request):
        if request.event.is_set():
            return
        try:
            request.check()
            if self.closed or self.window.get_session(request.session_id) is None:
                raise WorkbenchError("session_unavailable", "原聊天或桌面浏览器已关闭。")
            args = request.arguments
            action = request.action
            if action == "list":
                result = [dict(tab.snapshot(), **(self.file_status(tab) if tab.kind == "file" else {})) for tab in self.store.list(request.session_id)]
                request.finish({"ok": True, "tabs": result, "active_tab_id": self.store.active.get(request.session_id, "")})
                return
            if action == "open":
                kind = args.get("kind")
                request.started = True
                if kind == "web":
                    tab = self.open_web(request.session_id, args.get("url", ""))
                elif kind == "terminal":
                    tab = self.open_terminal(request.session_id, args.get("shell", ""), args.get("cwd", ""))
                elif kind == "file":
                    path = args.get("path") or ""
                    if not os.path.isabs(path):
                        path = os.path.join(self.window._workspace_dir_for_state(self.window.get_session(request.session_id)), path)
                    if not os.path.isfile(path):
                        raise WorkbenchError("file_unavailable", "文件不存在或无法访问。")
                    tab = self.store.open(request.session_id, "file", path=path)
                    self._compat_state(request.session_id)
                    self.sync()
                else:
                    raise WorkbenchError("invalid_kind", "请选择 file、web 或 terminal。")
                request.finish({"ok": True, "tab": tab.snapshot()})
                return
            tab = self.store.get(request.session_id, args.get("tab_id"))
            pane = self.panes.get(tab.id)
            if action == "terminal_read":
                if not isinstance(pane, TerminalPane):
                    raise WorkbenchError("invalid_kind", "目标不是终端。")
                request.finish({"ok": True, **pane.terminal.read(args.get("cursor", 0), args.get("limit", 32768))})
                return
            if action == "observe":
                if not isinstance(pane, BrowserPane):
                    raise WorkbenchError("invalid_kind", "目标不是网页。")
                pane.observe(request, lambda result: self.finish_observation(request, pane, result))
                return
            self.store.check_control(request.session_id, tab.id, request.revision)
            request.revision = self.store.set_owner(request.session_id, tab.id, "agent")
            if pane:
                pane.refresh_control()
            request.started = True
            if action == "close":
                # Do not cancel this request together with other pending tab requests.
                request.finish({"ok": self.close_tab(tab.id, session_id=request.session_id, agent=True, request=request)})
            elif action == "terminal_input" and isinstance(pane, TerminalPane):
                pane.terminal.write(str(args.get("text", "")))
                request.finish({"ok": True})
            elif action == "terminal_resize" and isinstance(pane, TerminalPane):
                pane.terminal.resize(args.get("rows", 24), args.get("cols", 80))
                request.finish({"ok": True})
            elif action == "terminal_interrupt" and isinstance(pane, TerminalPane):
                pane.terminal.write("\x03")
                request.finish({"ok": True})
            elif action == "navigate" and isinstance(pane, BrowserPane):
                pane.view.setUrl(input_url(args.get("url", "")))
                request.finish({"ok": True, "status": "loading", "tab_id": tab.id})
            elif action == "browser_action" and isinstance(pane, BrowserPane):
                request.started = False
                pane.act(request, request.finish)
            else:
                raise WorkbenchError("invalid_action", "操作与标签类型不匹配。")
        except Exception as exc:
            log.warning("workbench.action_error session=%s request=%s code=%s", request.session_id, request.id, getattr(exc, "code", "failed"))
            request.fail(exc)

    def finish_observation(self, request, pane, result):
        try:
            request.check()
            # Store images in the existing durable tool-image attachment pipeline.
            image = pane.view.grab()
            if not image.isNull():
                from core.tool_images import persist_tool_image
                handle, path = tempfile.mkstemp(suffix=".png")
                os.close(handle)
                try:
                    if image.save(path, "PNG"):
                        part = persist_tool_image(path, self.window.config_manager.get_chat_history_dir(), request.session_id)
                        result["screenshot"] = part
                        if request.context.get("model_supports_vision") and request.context.get("model_api_protocol") == "responses":
                            result["content_parts"] = [part]
                        else:
                            result["screenshot_note"] = "截图已保存；当前模型接口不支持工具图片输入，可依据页面元素和文字操作。"
                        result["screenshot_size"] = {"width": image.width(), "height": image.height(), "device_pixel_ratio": image.devicePixelRatio()}
                finally:
                    os.unlink(path)
            request.finish(result)
        except Exception as exc:
            request.fail(exc)

    def handle_download(self, item):
        pane = next((pane for pane in self.panes.values() if isinstance(pane, BrowserPane) and pane.page is item.page()), None)
        if pane is None or self.closed:
            item.cancel(); return
        path, _ = QFileDialog.getSaveFileName(self.window, "保存下载文件", item.downloadFileName())
        if not path:
            item.cancel(); return
        item.setDownloadDirectory(os.path.dirname(path))
        item.setDownloadFileName(os.path.basename(path))
        entry = {"item": item, "path": path, "session_id": pane.tab.session_id, "status": "下载中"}
        self.downloads.append(entry)
        pane.download = item
        pane.download_cancel.show()
        def update():
            total, received = item.totalBytes(), item.receivedBytes()
            state = item.state().name
            entry["status"] = {"DownloadCompleted": "已完成", "DownloadCancelled": "已取消", "DownloadInterrupted": "失败"}.get(state, "下载中")
            suffix = item.interruptReasonString() if state == "DownloadInterrupted" else f"{received // 1024} / {max(0, total) // 1024} KiB"
            if not self.closed and pane.tab.id in self.panes:
                pane.download_label.setText(f"{os.path.basename(path)} · {entry['status']} · {suffix}")
                pane.download_cancel.setVisible(not item.isFinished())
                pane.download_open.setVisible(state == "DownloadCompleted")
        pane.download_open.clicked.disconnect()
        pane.download_open.clicked.connect(lambda: self.open_download(entry))
        item.receivedBytesChanged.connect(update)
        item.stateChanged.connect(update)
        item.accept(); update()

    def open_download(self, entry):
        state = self.window.get_session(entry["session_id"])
        if state is None or not os.path.isfile(entry["path"]):
            return
        tab = self.store.open(state.session_id, "file", path=entry["path"])
        if self.session_id() == state.session_id:
            self.activate(tab.id)
        self.sync()

    def settings_menu(self):
        menu = QMenu(self.browser_settings)
        menu.addAction("清除网页登录和缓存…", self.clear_browser_data)
        if self.downloads:
            menu.addSeparator()
            for entry in self.downloads[-20:]:
                action = menu.addAction(os.path.basename(entry["path"]) + " · " + entry["status"])
                action.setEnabled(entry["status"] == "已完成")
                action.triggered.connect(lambda checked=False, current=entry: self.open_download(current))
        menu.exec(self.browser_settings.mapToGlobal(self.browser_settings.rect().topLeft()))

    def clear_browser_data(self):
        if ProductMessageBox.question(self.window, "清除浏览数据", "这会清除应用内共享的网页登录、网站存储和缓存，并关闭所有网页标签。文件和终端会保留。",
                                      ProductMessageBox.Yes | ProductMessageBox.No) != ProductMessageBox.Yes:
            return
        for tab in list(self.store.tabs.values()):
            if tab.kind == "web":
                self.close_tab(tab.id, session_id=tab.session_id)
        # Clearing before opening the first web tab must also clear the saved
        # profile from the previous application run.
        self.browser_profile()
        if self._profile:
            self._profile.cookieStore().deleteAllCookies()
            self._profile.clearHttpCache()
            # Recreate the profile against a fresh storage directory; old storage is
            # removed only after its profile has been released by Qt.
            profile = self._profile
            self._profile = None
            old_path = profile.persistentStoragePath()
            old_cache = profile.cachePath()
            import uuid
            fresh = uuid.uuid4().hex
            self.window.config_manager.set("browser_storage_generation", fresh)
            profile.deleteLater()
            def cleanup():
                import shutil
                base = Path(get_app_data_dir()).resolve() / "browser"
                for candidate in (old_path, old_cache):
                    resolved = Path(candidate).resolve()
                    if base in resolved.parents:
                        try:
                            shutil.rmtree(resolved)
                        except OSError:
                            log.exception("browser.clear_storage_error")
                            if not self.closed:
                                self.start_notice.setText("已退出网页登录。旧缓存未能完全清理，请重启应用后重试。")
                                self.start_notice.show()
            profile.destroyed.connect(cleanup)
        self.start_notice.setText("已清除网页登录和缓存。")
        self.start_notice.show()

    def dispose(self):
        if self.closed:
            return
        self.closed = True
        self.bridge.shutdown()
        try:
            self.bridge.requested.disconnect(self.handle_request)
        except RuntimeError:
            pass
        for pane in self.panes.values():
            pane.close_pane()
            pane.deleteLater()
        self.panes.clear()

    def dispose_session(self, session_id):
        for tab in list(self.store.list(session_id)):
            self.bridge.cancel_tab(session_id, tab.id)
            pane = self.panes.pop(tab.id, None)
            if pane:
                pane.close_pane(); self.stack.removeWidget(pane); pane.deleteLater()
            self.store.close(session_id, tab.id)
        self.store.order.pop(session_id, None)
        self.store.active.pop(session_id, None)
