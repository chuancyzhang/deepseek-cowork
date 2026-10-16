"""Opt-in browser backend selection, with visible pending-switch recovery."""
import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot, QTimer, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton

from core.browser_backend import BrowserBackendCoordinator, INTERNAL
from core.theme import DesignTokens, bind_theme
from ui.primitives import product_button_style, ProductMessageDialog

log = logging.getLogger(__name__)


class RecoverySignals(QObject):
    finished = Signal(object)


class BrowserAutomationSettings(QWidget):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("BrowserAutomationSettings")
        self.setMaximumWidth(720)
        self.coordinator = BrowserBackendCoordinator(config_manager)
        self.saved = self.coordinator.snapshot()["desired"] == INTERNAL
        self.recovering = False
        self.signals = RecoverySignals()
        self.signals.finished.connect(self._recovered)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        self.title = QLabel("内置浏览器自动化 · 实验功能")
        self.title.setObjectName("BrowserAutomationHeading")
        layout.addWidget(self.title)
        note = QLabel("让 AI 在右侧浏览器中阅读和操作网页，你可以查看过程并随时接管。关闭时使用外部 BrowserSkill，开启时只使用内置网页工具。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled = QCheckBox("启用内置浏览器自动化")
        self.enabled.setChecked(self.saved)
        layout.addWidget(self.enabled)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        timing = QLabel("默认关闭。保存后立即切换；有浏览器任务运行时，等待已有任务结束再切换，期间不启动新的浏览器任务。手动浏览、文件和终端不受影响。")
        timing.setWordWrap(True)
        layout.addWidget(timing)
        row = QHBoxLayout()
        self.apply_button = QPushButton("保存设置")
        self.apply_button.clicked.connect(self.save)
        self.reset_button = QPushButton("撤销更改")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.apply_button)
        row.addWidget(self.reset_button)
        row.addStretch()
        layout.addLayout(row)
        self.cancel_switch_button = QPushButton("取消待执行的切换")
        self.cancel_switch_button.clicked.connect(self.cancel_switch)
        layout.addWidget(self.cancel_switch_button, 0, Qt.AlignLeft)
        self.recover_button = QPushButton("重试释放已结束的浏览器任务")
        self.recover_button.clicked.connect(self.recover)
        layout.addWidget(self.recover_button, 0, Qt.AlignLeft)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.result)
        self.enabled.toggled.connect(self._refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        self._refresh()
        bind_theme(self, self.refresh_theme, surface="settings")
        self.refresh_theme()

    def has_pending_input(self):
        return self.enabled.isChecked() != self.saved

    def _refresh(self, *_args):
        try:
            state = self.coordinator.snapshot()
        except Exception:
            self.status.setText("无法读取浏览器状态，请检查配置目录后重试。")
            return
        backend = "内置浏览器" if state["backend"] == INTERNAL else "外部 BrowserSkill"
        text = f"当前使用：{backend}"
        if state["pending"]:
            target = "内置浏览器" if state["desired"] == INTERNAL else "外部 BrowserSkill"
            text += f"\n等待当前 {state['active_runs']} 个浏览器任务结束后切换至{target}；暂不启动新的浏览器任务。"
        if state["cleanup_errors"]:
            text += "\n任务已结束，但浏览器会话尚未释放。请检查外部浏览器连接后重试释放。"
        elif state["recoverable"]:
            text += "\n有已结束或中断的浏览器任务，可重试释放后继续切换。"
        if self.has_pending_input():
            text += "\n有未保存的更改。"
        self.status.setText(text)
        self.apply_button.setEnabled(self.has_pending_input() and not self.recovering)
        self.reset_button.setEnabled(self.has_pending_input())
        self.cancel_switch_button.setVisible(state["pending"])
        self.cancel_switch_button.setEnabled(not self.recovering)
        self.recover_button.setVisible(state["recoverable"])
        self.recover_button.setEnabled(not self.recovering)

    def save(self):
        enabled = self.enabled.isChecked()
        log.info("browser.settings_save_started internal=%s", enabled)
        try:
            state = self.coordinator.save(enabled)
        except Exception as exc:
            log.error("browser.settings_save_failed error_type=%s", type(exc).__name__)
            self.result.setText(str(exc) if getattr(exc, "code", "") == "browser_save_uncertain" else
                                "保存失败，原设置继续生效，当前选择已保留。请检查磁盘空间和目录权限后重试。")
            return False
        self.saved = enabled
        self.result.setText("已保存，等待当前浏览器任务结束后切换。" if state["pending"] else "已保存，浏览器模式已切换。")
        self._refresh()
        return True

    def reset(self):
        self.enabled.setChecked(self.saved)
        self.result.clear()
        self._refresh()

    def cancel_switch(self):
        self.enabled.setChecked(self.coordinator.snapshot()["backend"] == INTERNAL)
        if self.save():
            self.result.setText("已取消待执行的切换，继续使用当前浏览器。")

    def recover(self):
        if self.recovering:
            return
        self.recovering = True
        self.result.setText("正在释放本应用已结束的浏览器任务…")
        self._refresh()
        coordinator, signals = self.coordinator, self.signals
        def work():
            try:
                value = coordinator.recover()
            except Exception as exc:
                value = {"error": str(exc)}
            signals.finished.emit(value)
        threading.Thread(target=work, name="browser-backend-cleanup", daemon=True).start()

    @Slot(object)
    def _recovered(self, value):
        self.recovering = False
        failed = value.get("error") or value.get("cleanup_errors")
        self.result.setText("释放未完成，原模式保持。请检查浏览器连接后重试。" if failed else "已释放结束的任务。")
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
        for widget in (self.apply_button, self.reset_button, self.cancel_switch_button, self.recover_button):
            widget.setStyleSheet(product_button_style("primary" if widget is self.apply_button else "secondary"))
