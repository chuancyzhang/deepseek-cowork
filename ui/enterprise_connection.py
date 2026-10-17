"""One employee sign-in entry; enterprise authentication stays in the browser."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from core.enterprise_client import EnterpriseClient, EnterpriseStore
from core.enterprise_connection import read_connection
from core.theme import DesignTokens, bind_theme
from ui.knowledge_library import LibraryJobs
from ui.primitives import product_button_style, ProductMessageDialog


def offer_enterprise_onboarding(config_manager, parent):
    """One local invitation; actual authentication stays in the account page."""
    store = EnterpriseStore(getattr(config_manager, "data_dir", None))
    connection = store.connection()
    if not connection:
        return False
    if store.read().get("member"):
        return True
    if config_manager.get("enterprise_intro_seen") == connection["platform_id"]:
        return False
    choice = ProductMessageDialog(
        "连接" + connection["platform_name"],
        "登录后可使用企业分配的模型。企业模型的新提问在中心保留 30 天，供管理员查看；完整对话仍保存在本机。也可以暂不登录，继续个人使用。",
        "confirm", [("暂不登录", "later", "secondary", False), ("前往企业登录", "login", "primary", True)], parent=parent,
    ).exec_result("later")
    config_manager.set("enterprise_intro_seen", connection["platform_id"])
    if choice == "login":
        parent.open_settings("账号与连接")
    return True


class EnterpriseConnectionPanel(QWidget):
    changed = Signal()
    open_browser = Signal(str)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.store = EnterpriseStore(getattr(config_manager, "data_dir", None))
        self.client = EnterpriseClient(self.store)
        self.jobs = LibraryJobs(self)
        self.cancel_event = None
        self.busy = False
        self.connection = None
        self.seed = None
        self.browser_error = ""
        self.setObjectName("EnterpriseConnectionPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(16)
        self.company = QLabel()
        self.company.setObjectName("EnterpriseCompany")
        self.status = QLabel()
        self.status.setObjectName("EnterpriseStatus")
        self.guide = QLabel("在企业认证网页输入账号密码或扫码。登录完成后，客户端会自动连接。")
        self.guide.setObjectName("EnterpriseGuide")
        self.models = QLabel()
        self.models.setObjectName("EnterpriseModels")
        self.package_notice = QLabel()
        for label in (self.company, self.status, self.guide, self.models, self.package_notice):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            layout.addWidget(label)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.login_button = QPushButton("登录企业账号")
        self.cancel_button = QPushButton("取消登录")
        self.refresh_button = QPushButton("刷新模型")
        self.logout_button = QPushButton("退出登录")
        for button in (self.login_button, self.cancel_button, self.refresh_button, self.logout_button):
            row.addWidget(button)
        row.addStretch()
        self.connection_button = QPushButton("使用安装包中的企业连接")
        layout.addWidget(self.connection_button, 0, Qt.AlignLeft)
        layout.addSpacing(8)
        self.privacy = QLabel("使用企业模型时，新提问在中心保留 30 天，供管理员查看。完整对话保存在本机。未登录或使用个人模型的提问不进入中心记录。")
        self.privacy.setObjectName("EnterprisePrivacy")
        self.privacy.setWordWrap(True)
        layout.addWidget(self.privacy)
        self.login_button.clicked.connect(self.login)
        self.cancel_button.clicked.connect(self.cancel_login)
        self.refresh_button.clicked.connect(lambda: self.run(self.client.refresh, "正在刷新模型授权…"))
        self.logout_button.clicked.connect(lambda: self.run(self.client.logout, "正在退出登录…"))
        self.connection_button.clicked.connect(self.use_package_connection)
        self.open_browser.connect(self.launch_browser)
        bind_theme(self, self.refresh_theme)
        self.refresh_theme()
        self.refresh()

    def refresh_theme(self, *_):
        self.setStyleSheet(f"""
            QWidget#EnterpriseConnectionPanel {{ background: {DesignTokens.bg_app}; }}
            QWidget#EnterpriseConnectionPanel QLabel {{ color: {DesignTokens.text_primary}; }}
            QLabel#EnterpriseCompany {{ font-weight: 600; font-size: 16px; }}
            QLabel#EnterpriseGuide, QLabel#EnterprisePrivacy {{ color: {DesignTokens.text_secondary}; }}
        """)
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(product_button_style("primary" if button is self.login_button else "secondary"))

    def refresh(self, message=""):
        state, member = {}, None
        try:
            state = self.store.read()
            self.connection = state.get("connection") or read_connection()
            member = state.get("member") if state.get("expires_at", 0) > time.time() else None
            default_status = "已登录 · " + member["name"] if member else "登录已过期，请重新登录" if state.get("member") else "未登录"
            self.status.setText(message or default_status)
        except Exception:
            self.connection = None
            self.status.setText(message or "企业连接无法读取，请联系管理员检查客户端配置。")
        self.company.setText((self.connection or {}).get("platform_name", "尚未配置企业登录"))
        self.guide.setVisible(not member)
        self.guide.setText("在企业认证网页输入账号密码或扫码。登录完成后，客户端会自动连接。" if self.connection
                           else "请使用管理员提供的企业客户端。也可以继续使用个人模型。")
        models = state.get("models", [])
        self.models.setVisible(bool(member))
        self.models.setText("已授权模型：" + "、".join(m["name"] for m in models) if models
                            else "尚未分配企业模型，请联系管理员；分配后点击“刷新模型”。")
        self.seed = None
        package_message = ""
        if state.get("connection"):
            try:
                seed = read_connection()
                if seed and seed != state["connection"]:
                    self.seed = seed
                    package_message = "安装包提供了不同的企业连接，当前仍使用已确认的连接。"
            except Exception:
                package_message = "包内企业连接文件无效，已确认的连接保留。请联系管理员检查分发包。"
        self.package_notice.setText(package_message)
        self.package_notice.setVisible(bool(package_message))
        self.connection_button.setVisible(bool(self.seed))
        self.connection_button.setEnabled(not self.busy)
        self.login_button.setVisible(not member and not self.busy)
        self.login_button.setEnabled(bool(self.connection))
        self.cancel_button.setVisible(self.cancel_event is not None)
        self.cancel_button.setEnabled(bool(self.cancel_event and not self.cancel_event.is_set()))
        for control in (self.refresh_button, self.logout_button):
            control.setVisible(bool(member))
            control.setEnabled(not self.busy)

    def run(self, work, message):
        if self.busy:
            return
        self.busy = True
        self.refresh(message)
        def finish(value=None, error=None):
            self.busy = False
            self.cancel_event = None
            self.refresh(self.browser_error or (str(error) if error else ""))
            self.changed.emit()
        self.jobs.submit(work, lambda value: finish(value), lambda error: finish(error=error))

    def login(self):
        if self.busy or not self.connection:
            return
        self.cancel_event = threading.Event()
        self.browser_error = ""
        cancel, connection = self.cancel_event, dict(self.connection)
        def work():
            if not self.store.read().get("connection"):
                self.client.bind(connection)
            return self.client.login(None, cancel, self.open_browser.emit)
        self.run(work, "请在打开的企业认证网页完成登录；可随时取消。")

    def launch_browser(self, url):
        if self.cancel_event is None or self.cancel_event.is_set():
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.browser_error = "无法打开企业认证网页，请检查系统默认浏览器后重新登录。"
            self.cancel_event.set()
            self.refresh(self.browser_error)

    def cancel_login(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.refresh("正在取消登录…")

    def use_package_connection(self):
        if not self.seed or self.busy:
            return
        choice = ProductMessageDialog("更新企业连接", "此操作将退出当前企业账号。正在运行的企业任务需要重新登录后发起，本机对话仍保留。", "confirm",
            [("保留当前连接", "stay", "secondary", False), ("更新并重新登录", "change", "primary", True)], parent=self).exec_result("stay")
        if choice == "change":
            candidate = dict(self.seed)
            self.run(lambda: self.client.bind(candidate), "正在验证企业连接…")
