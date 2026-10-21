"""Enterprise account sign-in. Service logins belong to their own features."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea

from core.theme import bind_theme, DesignTokens
from ui.primitives import ProductMessageDialog


class AccountConnectionsPage(QWidget):
    """The employee account page has one enterprise sign-in flow."""

    def __init__(self, parent=None, *, config_manager=None):
        super().__init__(parent)
        self.setObjectName("AccountConnectionsPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("AccountConnectionsBody")
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel("企业账号")
        title.setObjectName("ConnectionsHeading")
        layout.addWidget(title)
        from ui.enterprise_connection import EnterpriseConnectionPanel
        self.enterprise = EnterpriseConnectionPanel(config_manager, self.body)
        layout.addWidget(self.enterprise)
        layout.addStretch()
        scroll.setWidget(self.body)
        outer.addWidget(scroll)
        bind_theme(self, self.refresh_theme, surface="management")
        self.refresh_theme()

    def refresh_theme(self, *_):
        self.setStyleSheet(f"QWidget#AccountConnectionsBody {{ background: {DesignTokens.bg_app}; }} QLabel#ConnectionsHeading {{ color: {DesignTokens.text_primary}; font-size: 20px; font-weight: 600; }}")
        self.enterprise.refresh_theme()

    def has_pending_input(self):
        return self.enterprise.cancel_event is not None

    def confirm_leave(self):
        if self.enterprise.cancel_event is None:
            return True
        choice = ProductMessageDialog("企业登录尚未完成", "离开将取消本次登录。已保存的账号和本机对话保留。", "confirm",
            [("取消登录并离开", "leave", "secondary", False), ("继续登录", "stay", "primary", True)], parent=self).exec_result("stay")
        if choice != "leave":
            return False
        self.enterprise.cancel_login()
        return True
