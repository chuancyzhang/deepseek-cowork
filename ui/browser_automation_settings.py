"""Browser choice with automatic recovery and task-oriented status."""
import logging

from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton, QMenu, QSizePolicy

from core.browser_backend import BrowserBackendCoordinator, INTERNAL
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style, ProductMessageDialog

log = logging.getLogger(__name__)


class BrowserAutomationSettings(QWidget):
    taskRequested = Signal(str)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("BrowserAutomationSettings")
        self.setMaximumWidth(720)
        self.coordinator = BrowserBackendCoordinator(config_manager)
        self.saved = self.coordinator.snapshot()["desired"] == INTERNAL
        self.recovering = False
        self._details_expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        self.title = QLabel("浏览器", self)
        self.title.setObjectName("BrowserAutomationHeading")
        layout.addWidget(self.title)
        self.enabled = QCheckBox("使用内置浏览器", self)
        self.enabled.setChecked(self.saved)
        layout.addWidget(self.enabled)
        note = QLabel("在右侧查看网页操作并随时接管。关闭后使用 Chrome 或 Edge 的已有登录状态。", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel(self)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.status_actions = QLabel(self)
        self.status_actions.setTextFormat(Qt.RichText)
        self.status_actions.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.status_actions.setWordWrap(True)
        self.status_actions.linkActivated.connect(self._status_action)
        layout.addWidget(self.status_actions)
        self.details = QLabel(self)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.hide()
        layout.addWidget(self.details)
        row = QHBoxLayout()
        self.apply_button = QPushButton("保存设置", self)
        self.apply_button.clicked.connect(self.save)
        row.addWidget(self.apply_button)
        self.recover_button = QPushButton("重试连接", self)
        self.recover_button.clicked.connect(self.recover)
        row.addWidget(self.recover_button)
        row.addStretch()
        layout.addLayout(row)
        self.result = QLabel(self)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.result)
        self.enabled.toggled.connect(self._refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._refresh_if_visible)
        self._refresh()
        bind_theme(self, self.refresh_theme, surface="settings")
        self.refresh_theme()

    def has_pending_input(self):
        return self.enabled.isChecked() != self.saved

    def _refresh_if_visible(self):
        if self.isVisible():
            self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        # Defer the initial status read until the show event has returned.
        QTimer.singleShot(0, self, self._recover_on_open)

    def _recover_on_open(self):
        if not self.isVisible():
            return
        if self.coordinator.snapshot()["recoverable"]:
            self.coordinator.request_recovery()
        self._refresh()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _refresh(self, *_args):
        try:
            state = self.coordinator.snapshot()
        except Exception:
            self.status.setText("无法读取浏览器状态，请检查配置目录后重试。")
            return
        self.recovering = state.get("recovering", False)
        backend = "内置浏览器" if state["backend"] == INTERNAL else "Chrome / Edge"
        text = f"当前使用{backend}"
        if state["pending"]:
            target = "内置浏览器" if state["desired"] == INTERNAL else "Chrome / Edge"
            text = f"正在切换至{target}"
            if state.get("running_runs", 0):
                text += f"\n还有 {state['running_runs']} 个任务正在使用浏览器，结束后自动切换。"
        if self.recovering:
            text += "\n正在恢复连接…聊天和已有内容不受影响。"
        elif state["recoverable"]:
            text += "\n连接尚未恢复，已有内容已保留。请打开使用过的浏览器后重试。"
        if self.has_pending_input():
            text += "\n有未保存的更改。"
        if self.status.text() != text:
            self.status.setText(text)
        self._fit_status()
        self.apply_button.setVisible(self.has_pending_input())
        self.apply_button.setEnabled(not self.recovering)
        self.recover_button.setVisible(state["recoverable"] and not self.recovering and not self.has_pending_input())
        self.recover_button.setEnabled(not self.recovering)
        actions = []
        if state.get("running_tasks"):
            actions.append(("tasks", "查看任务"))
        if state["pending"]:
            actions.append(("cancel", "取消切换"))
        if state["cleanup_errors"]:
            actions.append(("details", "收起详情" if self._details_expanded else "详情"))
        self.status_actions.setText(" &nbsp;·&nbsp; ".join(
            f'<a href="{action}" style="color:{DesignTokens.chat_link}">{label}</a>' for action, label in actions))
        self.status_actions.setVisible(bool(actions))
        self.details.setText("\n".join(state["cleanup_errors"]))
        self.details.setVisible(bool(state["cleanup_errors"]) and self._details_expanded)
        self.result.setVisible(bool(self.result.text()))

    def _status_action(self, action):
        if action == "tasks":
            self.show_tasks()
        elif action == "cancel":
            self.cancel_switch()
        elif action == "details":
            self._details_expanded = not self._details_expanded
            self._refresh()

    def _fit_status(self):
        # Status grows asynchronously; reserve its actual wrapped height before
        # the outer scroll area computes its minimum content size.
        if self.status.width() > 0:
            self.status.setMinimumHeight(self.status.heightForWidth(self.status.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_status()

    def show_tasks(self):
        tasks = self.coordinator.snapshot().get("running_tasks", [])
        if len(tasks) == 1:
            self.taskRequested.emit(tasks[0]["session"])
            return
        menu = QMenu(self)
        for index, task in enumerate(tasks, 1):
            title = f"正在进行的任务 {index}"
            storage = getattr(self.window(), "chat_storage", None)
            if storage:
                meta = storage.get_conversation_meta(task["session"]) or {}
                title = meta.get("title") or title
            action = menu.addAction(title)
            action.triggered.connect(lambda _checked=False, sid=task["session"]: self.taskRequested.emit(sid))
        menu.exec(self.status_actions.mapToGlobal(self.status_actions.rect().bottomLeft()))
        menu.deleteLater()

    def save(self):
        enabled = self.enabled.isChecked()
        log.info("browser.settings_save_started internal=%s", enabled)
        try:
            state = self.coordinator.save(enabled)
        except Exception as exc:
            log.error("browser.settings_save_failed error_type=%s", type(exc).__name__)
            self.result.setText(str(exc) if getattr(exc, "code", "") == "browser_save_uncertain" else
                                "保存失败，原设置继续生效，当前选择已保留。请检查磁盘空间和目录权限后重试。")
            self.result.show()
            return False
        self.saved = enabled
        self.result.clear()
        if state["pending"] and self.coordinator.snapshot()["recoverable"]:
            self.coordinator.request_recovery()
        self._refresh()
        return True

    def reset(self):
        self.enabled.setChecked(self.saved)
        self.result.clear()
        self._refresh()

    def cancel_switch(self):
        self.enabled.setChecked(self.coordinator.snapshot()["backend"] == INTERNAL)
        self.save()

    def recover(self):
        if self.recovering:
            return
        self.result.clear()
        self.coordinator.request_recovery()
        self._refresh()

    def confirm_leave(self):
        if not self.has_pending_input():
            return True
        choice = ProductMessageDialog(
            "浏览器设置尚未保存", "保存后离开，或放弃这些更改。", "confirm",
            [("保存并离开", "save", "primary", False), ("放弃更改", "discard", "secondary", False),
             ("继续编辑", "stay", "secondary", True)], parent=self).exec_result("stay")
        if choice == "save":
            return self.save()
        if choice == "discard":
            self.reset()
            return True
        return False

    def refresh_theme(self, _resolved=None):
        t = DesignTokens
        self.setStyleSheet(
            f"QWidget#BrowserAutomationSettings {{ background:transparent; }}"
            f"QWidget#BrowserAutomationSettings > QLabel {{ color:{t.text_secondary}; font-size:{t.font_size_body}px; }}"
            f"QWidget#BrowserAutomationSettings > QLabel#BrowserAutomationHeading {{ color:{t.text_primary}; font-size:{t.font_size_section}px; font-weight:600; }}"
            f"QWidget#BrowserAutomationSettings > QCheckBox {{ color:{t.text_primary}; font-size:{t.font_size_body}px; spacing:8px; min-height:{t.control_height}px; }}")
        for widget in (self.apply_button, self.recover_button):
            widget.setStyleSheet(product_button_style("primary"))
        self._refresh()
