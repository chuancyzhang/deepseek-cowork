"""Application-wide, dismissible update readiness; independent of settings pages."""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QToolButton, QVBoxLayout, QHBoxLayout, QSizePolicy

from core.theme import DesignTokens, bind_theme
from core.update_coordinator import update_log
from ui.primitives import product_button_style


class UpdateReadyNotice(QFrame):
    dismissed = Signal()

    def __init__(self, install, parent):
        super().__init__(parent)
        self.setObjectName("UpdateReadyNotice")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(12)
        row = QHBoxLayout()
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.message, 1)
        self.close_button = QToolButton(self)
        self.close_button.setText("×")
        self.close_button.setToolTip("关闭提示，下次启动时再提醒")
        self.close_button.setAccessibleName("关闭更新提示")
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.dismissed)
        row.addWidget(self.close_button, 0, Qt.AlignTop)
        layout.addLayout(row)
        self.install_button = QPushButton("重启并更新", self)
        self.install_button.clicked.connect(install)
        layout.addWidget(self.install_button, alignment=Qt.AlignLeft)
        self.hide()
        bind_theme(self, self.apply_theme)
        self.apply_theme()

    def apply_theme(self, *_):
        self.setStyleSheet(f"QFrame#UpdateReadyNotice {{ background: {DesignTokens.bg_main}; border: 1px solid {DesignTokens.border}; border-radius: 10px; }}"
                           f"QFrame#UpdateReadyNotice QLabel {{ color: {DesignTokens.text_primary}; background: transparent; border: none; font-size: {DesignTokens.font_size_body}px; }}")
        self.install_button.setStyleSheet(product_button_style("primary"))
        self.close_button.setStyleSheet(f"QToolButton {{ color: {DesignTokens.text_secondary}; background: transparent; border: none; border-radius: 6px; }}"
                                       f"QToolButton:hover {{ background: {DesignTokens.bg_hover}; }}")
        self.updateGeometry()


class UpdateNoticeController(QObject):
    def __init__(self, coordinator, host, before):
        super().__init__(host)
        self.coordinator = coordinator
        self.dismissed_versions = set()
        self.version = ""
        self.eligible = False
        self.notice = UpdateReadyNotice(self.request_install, host)
        self.notice.dismissed.connect(self.dismiss)
        host.layout().insertWidget(host.layout().indexOf(before), self.notice)
        coordinator.changed.connect(self.render)
        self.render(coordinator.snapshot())

    def render(self, state):
        prepared = self.coordinator.prepared or {}
        self.version = str(prepared.get("release", {}).get("tag_name", "")).lstrip("vV")
        self.eligible = bool(self.coordinator.install_enabled and prepared.get("staged_app_dir") and self.version
                             and prepared.get("update_source") == "online"
                             and state.get("phase") not in {"planning", "installing"}
                             and not state.get("install_requested"))
        self.notice.message.setText(f"新版本 {self.version}\n已准备好")
        visible = self.eligible and self.version not in self.dismissed_versions
        if visible and self.notice.isHidden():
            update_log(self.coordinator.root, "ready_notice_shown", version=self.version, source=prepared.get("source_name"))
        self.notice.setVisible(visible)

    def dismiss(self):
        self.dismissed_versions.add(self.version)
        self.notice.hide()
        update_log(self.coordinator.root, "ready_notice_dismissed", version=self.version)

    def request_install(self):
        # The coordinator owns serialization and preserves the standard save/exit flow.
        if self.eligible:
            update_log(self.coordinator.root, "install_requested_from_sidebar", version=self.version)
            self.coordinator.request_install()
