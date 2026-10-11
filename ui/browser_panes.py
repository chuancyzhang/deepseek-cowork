"""Browser and terminal surfaces used by the unified right-hand workbench."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import quote_plus

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QUrl, Qt, QEvent, QPoint
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QFocusEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
                              QToolButton, QApplication, QMenu)

from core.env_utils import get_resource_dir
from core.theme import DesignTokens, bind_theme
from core.workbench import WorkbenchError
from ui.primitives import product_button_style, product_field_style

log = logging.getLogger(__name__)


def asset_path(name):
    path = os.path.join(get_resource_dir(), "web", "editors", "dist", name)
    if not os.path.isfile(path):
        raise WorkbenchError("assets_missing", "浏览器离线资源缺失，请重新构建 web/editors 后重试。")
    return path


def input_url(value):
    value = str(value or "").strip()
    if not value:
        return QUrl("about:blank")
    if value == "about:blank":
        return QUrl(value)
    candidate = QUrl.fromUserInput(value)
    if candidate.scheme() not in {"http", "https"} or (" " in value) or (":" not in value and "." not in value and value != "localhost"):
        if "://" in value:
            raise WorkbenchError("unsupported_url", "这里只打开 HTTP 或 HTTPS 网页；本地文件请从文件目录打开。")
        return QUrl("https://www.bing.com/search?q=" + quote_plus(value))
    return candidate


def button(text, tooltip, slot, parent=None):
    widget = QToolButton(parent)
    widget.setText(text)
    widget.setToolTip(tooltip)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(slot)
    widget.setProperty("browserIcon", {"+": "fa5s.plus", "‹": "fa5s.chevron-left", "›": "fa5s.chevron-right",
                                      "↻": "fa5s.redo-alt", "⋯": "fa5s.ellipsis-h"}.get(text))
    style_button(widget)
    return widget


def style_button(widget):
    style = product_button_style("ghost").replace("QPushButton", "QToolButton")
    style = style.replace(f"{DesignTokens.control_height}px", "24px").replace("0 12px", "0 6px")
    widget.setStyleSheet(style)
    icon = widget.property("browserIcon")
    if icon:
        import qtawesome as qta
        widget.setText("")
        widget.setIcon(qta.icon(icon, color=DesignTokens.text_secondary))
        widget.setMinimumWidth(26)


class ControlledPane(QWidget):
    changed = Signal()

    def __init__(self, tab, store, bridge, parent=None):
        super().__init__(parent)
        self.tab, self.store, self.bridge = tab, store, bridge
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(8, 6, 8, 8)
        self.outer.setSpacing(6)
        self.control = QWidget(self)
        # Ownership must not resize the page between observation and its action.
        self.control.setFixedHeight(28)
        policy = self.control.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.control.setSizePolicy(policy)
        layout = QHBoxLayout(self.control)
        layout.setContentsMargins(0, 0, 0, 0)
        self.control_label = QLabel()
        layout.addWidget(self.control_label, 1)
        self.takeover_button = button("接管", "接管后停止 Agent 后续操作", self.take_over)
        self.return_button = button("交还 Agent", "允许 Agent 继续操作，不重放先前动作", self.give_back)
        layout.addWidget(self.takeover_button)
        layout.addWidget(self.return_button)
        self.outer.addWidget(self.control)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.refresh_control()
        bind_theme(self, self.refresh_theme, surface="preview_shell")

    def refresh_theme(self, _resolved=None):
        self.control_label.setStyleSheet(f"color:{DesignTokens.text_secondary}; background:transparent;")
        self.notice.setStyleSheet(f"color:{DesignTokens.text_secondary}; background:transparent;")
        for child in self.findChildren(QToolButton):
            style_button(child)
        for child in self.findChildren(QLineEdit):
            child.setStyleSheet(product_field_style())

    def show_notice(self, message):
        self.notice.setText(str(message or ""))
        self.notice.setVisible(bool(message))

    def take_over(self):
        self.store.set_owner(self.tab.session_id, self.tab.id, "human")
        self.bridge.cancel_tab(self.tab.session_id, self.tab.id)
        log.info("workbench.takeover session=%s tab=%s", self.tab.session_id, self.tab.id)
        self.refresh_control()

    def give_back(self):
        self.store.set_owner(self.tab.session_id, self.tab.id, "available")
        log.info("workbench.return_control session=%s tab=%s", self.tab.session_id, self.tab.id)
        self.refresh_control()

    def refresh_control(self):
        agent = self.tab.owner == "agent"
        human = self.tab.owner == "human"
        self.control.setVisible(agent or human)
        self.control_label.setText("Agent 正在操作" if agent else "由你控制")
        self.takeover_button.setVisible(agent)
        self.return_button.setVisible(human)
        self.changed.emit()


class BrowserPane(ControlledPane):
    openWindow = Signal(object)

    def __init__(self, tab, store, bridge, profile, parent=None):
        super().__init__(tab, store, bridge, parent)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.agent_input = False
        self.closed = False
        self.document_ready = False
        self.profile = profile
        pane = self
        class Page(QWebEnginePage):
            def createWindow(self, window_type):
                holder = []
                pane.openWindow.emit(holder)
                return holder[0] if holder else None

            def acceptNavigationRequest(self, url, kind, main_frame):
                if url.scheme() not in {"http", "https", "about", "blob", "data"}:
                    if main_frame:
                        pane.show_notice("此链接需要系统应用打开，请复制链接后使用对应应用。")
                    return False
                return True
        self.view = QWebEngineView(self)
        self.page = Page(profile, self.view)
        self.view.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        script = QWebEngineScript()
        script.setName("cowork-browser-observer")
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setSourceCode(Path(asset_path("browser-automation.js")).read_text(encoding="utf-8"))
        self.page.scripts().insert(script)
        self.toolbar = QWidget()
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        self.back_button = button("‹", "后退", lambda: self.user_navigate("back"))
        self.forward_button = button("›", "前进", lambda: self.user_navigate("forward"))
        self.reload_button = button("↻", "刷新", lambda: self.user_navigate("reload"))
        for item in (self.back_button, self.forward_button, self.reload_button):
            row.addWidget(item)
        self.address = QLineEdit(tab.url)
        self.address.setPlaceholderText("搜索或输入网址")
        self.address.returnPressed.connect(self.navigate_input)
        row.addWidget(self.address, 1)
        self.more = button("⋯", "网页操作", self.show_menu)
        row.addWidget(self.more)
        self.outer.addWidget(self.toolbar)
        self.find = QLineEdit()
        self.find.setPlaceholderText("在页面中查找 · Esc 关闭")
        self.find.textChanged.connect(self.page.findText)
        self.find.hide()
        self.outer.addWidget(self.find)
        self.outer.addWidget(self.notice)
        self.download_row = QHBoxLayout()
        self.download_label = QLabel()
        self.download_label.setWordWrap(True)
        self.download_cancel = button("取消", "取消下载", self.cancel_download)
        self.download_open = button("打开", "在文件标签中打开", lambda: None)
        self.download_row.addWidget(self.download_label, 1)
        self.download_row.addWidget(self.download_cancel)
        self.download_row.addWidget(self.download_open)
        self.download_cancel.hide(); self.download_open.hide()
        self.outer.addLayout(self.download_row)
        self.download = None
        self.outer.addWidget(self.view, 1)
        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self.show_find)
        self._escape_shortcut = QShortcut(QKeySequence("Escape"), self.find)
        self._escape_shortcut.activated.connect(self.find.hide)
        self.view.titleChanged.connect(self.title_changed)
        self.view.urlChanged.connect(self.url_changed)
        self.view.loadStarted.connect(self.load_started)
        self.view.loadFinished.connect(self.load_finished)
        self.view.loadProgress.connect(lambda value: self.show_notice(f"正在加载 {value}%") if self.tab.status == "loading" else None)
        self.view.installEventFilter(self)
        self.refresh_theme()
        self.view.setUrl(input_url(tab.url))

    def show_find(self):
        self.find.show(); self.find.setFocus(); self.find.selectAll()

    def park(self, size=None):
        """Keep Chromium painting a background tab without activating a window."""
        self.hide()
        self.setParent(None)
        self.setWindowFlag(Qt.Tool, True)
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if size is not None and size.width() > 0 and size.height() > 0:
            self.resize(size)
        self.show()

    def mount(self, stack):
        self.hide()
        self.setAttribute(Qt.WA_DontShowOnScreen, False)
        stack.addWidget(self)
        stack.setCurrentWidget(self)
        self.show()

    def show_menu(self):
        menu = QMenu(self)
        menu.addAction("在页面中查找", self.show_find)
        menu.addAction("复制网址", lambda: QApplication.clipboard().setText(self.tab.url))
        menu.addAction("在系统浏览器打开", lambda: QDesktopServices.openUrl(QUrl(self.tab.url)))
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def cancel_download(self):
        if self.download:
            self.download.cancel()

    def refresh_control(self):
        super().refresh_control()
        if hasattr(self, "address"):
            controlled = self.tab.owner == "agent"
            for widget in (self.address, self.back_button, self.forward_button, self.reload_button):
                widget.setEnabled(not controlled)
            self.page.runJavaScript("window.coworkNativeAction=false; window.coworkHumanLocked=" + str(controlled).lower(), 1)

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() in {QEvent.ChildAdded, QEvent.Show}:
            QTimer.singleShot(0, self.install_input_filter)
        if self.tab.owner == "agent" and not self.agent_input:
            if event.type() in {QEvent.ContextMenu, QEvent.Drop, QEvent.InputMethod}:
                return True
            if event.type() in {QEvent.KeyPress, QEvent.KeyRelease}:
                if event.matches(QKeySequence.Copy):
                    return False
                return True
        return super().eventFilter(obj, event)

    def install_input_filter(self):
        if not self.closed:
            proxy = self.view.focusProxy()
            if proxy:
                proxy.installEventFilter(self)

    def user_navigate(self, action):
        if self.tab.owner != "agent":
            getattr(self.view, action)()

    def navigate_input(self):
        if self.tab.owner == "agent":
            return
        try:
            self.view.setUrl(input_url(self.address.text()))
        except WorkbenchError as exc:
            self.show_notice(str(exc))

    def title_changed(self, title):
        self.tab.title = title or self.tab.url or "网页"
        self.changed.emit()

    def url_changed(self, url):
        self.tab.url = url.toString()
        self.tab.page_revision += 1
        if not self.address.hasFocus():
            self.address.setText(self.tab.url)
        self.back_button.setEnabled(self.view.history().canGoBack() and self.tab.owner != "agent")
        self.forward_button.setEnabled(self.view.history().canGoForward() and self.tab.owner != "agent")
        self.changed.emit()

    def load_started(self):
        self.document_ready = False
        self.tab.status = "loading"
        self.tab.page_revision += 1
        self.changed.emit()

    def load_finished(self, ok):
        if self.closed:
            return
        self.document_ready = bool(ok)
        self.tab.status = "ready" if ok else "failed"
        self.show_notice("" if ok else "网页加载失败。可刷新重试，或在系统浏览器打开。")
        self.install_input_filter()
        self.refresh_control()
        self.changed.emit()

    def observe(self, request, callback):
        request.check()
        if not self.document_ready:
            raise WorkbenchError("page_not_ready", "页面尚未加载完成，请稍后重新观察。")
        revision = self.tab.page_revision
        def done(value):
            try:
                value = json.loads(value) if isinstance(value, str) and value else value
                request.check()
                if self.closed or revision != self.tab.page_revision:
                    raise WorkbenchError("stale_page", "页面已经跳转，请重新观察。")
                if not isinstance(value, dict) or "dom_version" not in value:
                    raise WorkbenchError("observation_unavailable", "此页面不能提供网页元素；可由用户直接阅读或使用文件工具。")
                value.update(ok=True, tab_id=self.tab.id,
                             page_version=f"{revision}:{value.pop('dom_version')}")
                callback(value)
            except Exception as exc:
                request.fail(exc)
        self.page.runJavaScript("JSON.stringify(window.coworkObserve && window.coworkObserve())", 1, done)

    def act(self, request, callback):
        request.check()
        args = dict(request.arguments)
        expected = str(args.get("page_version") or "")
        prefix = str(self.tab.page_revision) + ":"
        if not expected.startswith(prefix) or not self.document_ready:
            raise WorkbenchError("stale_page", "页面已变化，请先重新观察。")
        args["dom_version"] = expected[len(prefix):]
        action = args.get("action")
        native = action in {"key", "click_at"}
        def done(value):
            try:
                value = json.loads(value) if isinstance(value, str) and value else value
                request.check()
                self.store.check_control(self.tab.session_id, self.tab.id, request.revision)
                if not str(request.arguments.get("page_version", "")).startswith(str(self.tab.page_revision) + ":"):
                    raise WorkbenchError("stale_page", "页面已变化，请重新观察。")
                if not isinstance(value, dict):
                    raise WorkbenchError("action_failed", "网页没有返回操作结果。")
                if native and value.get("ok"):
                    from PySide6.QtTest import QTest
                    self.agent_input = True
                    target = self.view.focusProxy() or self.view
                    temporary_focus = not target.hasFocus()
                    if temporary_focus:
                        # Chromium needs input focus even for a background page.
                        # Deliver it locally without activating a window or moving
                        # QApplication's focus away from the user's editor.
                        QApplication.sendEvent(target, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
                    try:
                        if action == "click_at":
                            x, y = int(args.get("x", -1)), int(args.get("y", -1))
                            if not self.view.rect().contains(QPoint(x, y)):
                                raise WorkbenchError("invalid_coordinate", "坐标不在当前网页视口中，请重新截图。")
                            QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(x, y))
                        else:
                            allowed = {"Enter": Qt.Key_Return, "Tab": Qt.Key_Tab, "Escape": Qt.Key_Escape,
                                       "Backspace": Qt.Key_Backspace, "ArrowDown": Qt.Key_Down,
                                       "ArrowUp": Qt.Key_Up, "ArrowLeft": Qt.Key_Left, "ArrowRight": Qt.Key_Right}
                            key = allowed.get(str(args.get("key")))
                            if key is None:
                                raise WorkbenchError("unsupported_key", "不支持此按键。")
                            QTest.keyClick(target, key)
                    finally:
                        if temporary_focus:
                            QApplication.sendEvent(target, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
                        self.agent_input = False
                        self.page.runJavaScript("window.coworkNativeAction=false", 1)
                    self.tab.page_revision += 1
                callback(value)
            except Exception as exc:
                if native:
                    self.page.runJavaScript("window.coworkNativeAction=false", 1)
                if request.started and self.tab.owner == "human":
                    self.show_notice("已接管；已发出的页面操作可能已经完成，请查看实际页面。后续操作已停止。")
                request.fail(exc)
        def validated(value):
            try:
                value = json.loads(value) if isinstance(value, str) and value else value
                request.check()
                self.store.check_control(self.tab.session_id, self.tab.id, request.revision)
                if not isinstance(value, dict) or not value.get("ok"):
                    callback(value if isinstance(value, dict) else {"ok": False, "status": "action_failed"})
                    return
                if not expected.startswith(str(self.tab.page_revision) + ":"):
                    raise WorkbenchError("stale_page", "页面已变化，请重新观察。")
                request.started = True
                if native:
                    self.page.runJavaScript("window.coworkNativeAction=true; JSON.stringify({ok:true})", 1, done)
                else:
                    self.page.runJavaScript("JSON.stringify(window.coworkAct(" + json.dumps(args, ensure_ascii=False) + "))", 1, done)
            except Exception as exc:
                request.fail(exc)
        check = dict(args, action="validate")
        self.page.runJavaScript("JSON.stringify(window.coworkAct && window.coworkAct(" + json.dumps(check, ensure_ascii=False) + "))", 1, validated)

    def close_pane(self):
        self.closed = True
        self.view.stop()
        self.page.deleteLater()
        self.view.deleteLater()


class TerminalBridge(QObject):
    def __init__(self, pane):
        super().__init__(pane)
        self.pane = pane

    @Slot()
    def ready(self):
        self.pane.ready = True
        self.pane.refresh_theme()
        self.pane.refresh_control()

    @Slot(str)
    def input(self, text):
        if self.pane.tab.owner != "agent":
            try:
                self.pane.terminal.write(text)
            except WorkbenchError as exc:
                self.pane.show_notice(str(exc))

    @Slot(str)
    def reply(self, text):
        # Protocol replies are generated by xterm even while user input is locked.
        # ConPTY otherwise waits for DA/DSR and stops processing subsequent input.
        import re
        if re.fullmatch(r"\x1b\[(?:\??[0-9;]*c|[0-9;]*[Rn])", text):
            try:
                self.pane.terminal.write(text)
            except WorkbenchError:
                pass  # Replies arriving after process exit need no recovery.

    @Slot(int, int)
    def resizeTerminal(self, rows, cols):
        self.pane.terminal.resize(rows, cols)

    @Slot(int)
    def ack(self, cursor):
        self.pane.cursor = cursor
        self.pane.inflight = False

    @Slot(str)
    def copy(self, text):
        QApplication.clipboard().setText(text)

    @Slot()
    def paste(self):
        self.input(QApplication.clipboard().text())


class TerminalPane(ControlledPane):
    restartRequested = Signal()

    def __init__(self, tab, store, bridge, terminal, parent=None):
        super().__init__(tab, store, bridge, parent)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebChannel import QWebChannel
        self.terminal = terminal
        self.ready = self.inflight = False
        self.cursor = 0
        self.font_size = int(tab.view_state.get("terminal_font_size", DesignTokens.font_size_body))
        self.toolbar = QWidget()
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        self.directory = QLabel(tab.cwd)
        self.directory.setToolTip(tab.cwd)
        self.directory.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.directory, 1)
        self.interrupt_button = button("中断", "向当前终端发送 Ctrl+C", self.interrupt)
        self.restart_button = button("重新启动", "启动新的终端进程，不重跑命令", self.restartRequested.emit)
        self.restart_button.hide()
        row.addWidget(self.interrupt_button)
        row.addWidget(self.restart_button)
        self.outer.addWidget(self.toolbar)
        self.outer.addWidget(self.notice)
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("terminalBridge", TerminalBridge(self))
        self.page.setWebChannel(self.channel)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.outer.addWidget(self.view, 1)
        self.view.loadFinished.connect(lambda ok: self.show_notice("终端界面加载失败，请检查离线资源。") if not ok else None)
        self.view.setUrl(QUrl.fromLocalFile(asset_path("terminal.html")))
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.flush_output)
        self.timer.start()
        self.zoom_shortcuts = []
        for sequence, delta in (("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda change=delta: self.change_font_size(change))
            self.zoom_shortcuts.append(shortcut)

    def change_font_size(self, delta):
        self.font_size = max(9, min(28, self.font_size + delta)) if delta else DesignTokens.font_size_body
        self.tab.view_state["terminal_font_size"] = self.font_size
        self.refresh_theme()

    def interrupt(self):
        self.take_over()
        try:
            self.terminal.write("\x03")
        except WorkbenchError as exc:
            self.show_notice(str(exc))

    def refresh_control(self):
        super().refresh_control()
        if getattr(self, "ready", False):
            self.page.runJavaScript("window.coworkTerminal.setInput(" + str(self.tab.owner != "agent").lower() + ")")

    def refresh_theme(self, _resolved=None):
        super().refresh_theme()
        if getattr(self, "ready", False):
            theme = {"background": DesignTokens.bg_main, "foreground": DesignTokens.text_primary,
                     "cursor": DesignTokens.text_primary, "selectionBackground": DesignTokens.selection_bg}
            # ANSI defaults target dark terminals. Use the active theme's
            # readable text colors for both regular and bright ANSI palettes.
            for name, color in {"black": DesignTokens.text_primary, "red": DesignTokens.error_text,
                                "green": DesignTokens.success_text, "yellow": DesignTokens.warning_text,
                                "blue": DesignTokens.info_text, "magenta": DesignTokens.primary,
                                "cyan": DesignTokens.info_text, "white": DesignTokens.text_primary}.items():
                theme[name] = color
                theme["bright" + name.title()] = color
            app = QApplication.instance()
            font = str(app.property("themeMonoFontFamily") or "Consolas")
            self.page.runJavaScript("window.coworkTerminal.setTheme(" + json.dumps(theme) + "," + json.dumps(font) + "," + str(self.font_size) + ")")

    def flush_output(self):
        status = self.terminal.status
        if self.tab.status != status:
            self.tab.status = status
            self.restart_button.setVisible(status in {"exited", "failed", "closed"})
            self.interrupt_button.setEnabled(status == "running")
            self.show_notice(self.terminal.error if status == "failed" else
                             f"进程已退出（{self.terminal.exit_code}），已有输出已保留。" if status == "exited" else
                             "正在启动终端…" if status == "starting" else "")
            self.changed.emit()
        if not self.ready or self.inflight:
            return
        data = self.terminal.read(self.cursor, 16384)
        if data["output"]:
            text = ("\r\n[早期输出已超出缓冲范围]\r\n" if data["truncated"] else "") + data["output"]
            self.inflight = True
            self.page.runJavaScript("window.coworkTerminal.write(" + json.dumps(text) + "," + str(data["cursor"]) + ")")

    def close_pane(self):
        self.timer.stop()
        self.terminal.close()
        self.page.setWebChannel(None)
        self.page.deleteLater()
        self.view.deleteLater()
