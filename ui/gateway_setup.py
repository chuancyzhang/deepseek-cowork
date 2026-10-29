"""Inline service connection and model selection, sharing settings' save boundary."""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                             QHBoxLayout, QListWidget, QListWidgetItem, QSpinBox, QDoubleSpinBox)

from core.llm.gateway_accounts import GatewayAccount, GatewayError, channels_from_selection, normalize_site_url
from core.llm.gateway_catalog import catalog_record, new_catalog_model
from core.llm.model_catalog import list_available_models, ModelCatalogError
from core.theme import DesignTokens, bind_theme
from ui.model_configuration import ModelFieldCombo, ModelPageStack, model_field_style, apply_model_combo_style
from ui.primitives import product_button_style

log = logging.getLogger(__name__)


class ConnectionWorker(QThread):
    """Application-owned thread; callbacks are delivered only after it exits."""
    def __init__(self, operation, task, account=None):
        super().__init__(QApplication.instance())
        self.operation, self.task, self.account = operation, task, account
        self.result = None
        self.dispose_session = False

    def run(self):
        log.info("gateway.operation.start stage=%s", self.operation)
        try:
            value = self.task()
            if self.isInterruptionRequested() and self.account is None:
                raise GatewayError("操作已取消，已有配置已保留。", code="cancelled")
            self.result = {"ok": True, "value": value}
        except (GatewayError, ModelCatalogError) as error:
            self.result = {"ok": False, "error": str(error), "code": getattr(error, "code", "catalog_error")}
            log.info("gateway.operation.error stage=%s code=%s", self.operation, self.result["code"])
        except Exception as error:
            # Do not log exception bodies: transports and credentials can occur there.
            log.error("gateway.operation.error stage=%s error_type=%s", self.operation, type(error).__name__)
            self.result = {"ok": False, "error": "操作未完成，请检查连接后重试。", "code": "unexpected"}
        finally:
            self.task = None
            log.info("gateway.operation.finish stage=%s ok=%s", self.operation, bool(self.result and self.result["ok"]))

    def release(self):
        # Runs after the native thread and its UI completion. Handles disposal
        # requested after run() returns but before the queued finished signal.
        if self.dispose_session and self.account:
            self.account.close()
        self.account = self.result = self.done = None


class ModelSelectionPanel(QWidget):
    """Shared picker for manual API keys, gateway login and later refresh."""
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.note = QLabel("站点返回的模型；能力声明未经实际调用验证。", self)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索模型、密钥或分组")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.model_list = QListWidget(self)
        self.model_list.setMinimumHeight(180)
        self.model_list.setMaximumHeight(380)
        self.model_list.setWordWrap(True)
        self.model_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.model_list.itemChanged.connect(self._changed)
        layout.addWidget(self.model_list)
        row = QHBoxLayout()
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.select_all = QPushButton("全选可添加", self)
        self.select_all.clicked.connect(self._select_all)
        row.addWidget(self.summary, 1)
        row.addWidget(self.select_all)
        layout.addLayout(row)
        bind_theme(self, self.refresh_theme, surface="settings")

    def set_records(self, results, existing=()):
        old = self.selected_pairs()
        existing = set(existing)
        self.model_list.blockSignals(True)
        self.model_list.clear()
        for key_id, result in results.items():
            info = result.get("key_info", {})
            for record in result.get("records", []):
                pair = (key_id, record["id"])
                evidence = record.get("discovery", {})
                unsupported = evidence.get("unsupported_reason", "")
                name = record["id"]
                detail = " · ".join(filter(None, (info.get("name"), info.get("group_name"),
                                                  unsupported or ("已添加" if pair in existing else ""))))
                item = QListWidgetItem(name + ("\n" + detail if detail else ""))
                item.setData(Qt.UserRole, (key_id, record))
                item.setData(Qt.UserRole + 1, (name + " " + detail).casefold())
                item.setToolTip("\n".join(filter(None, (name, detail, "调用方式未确认" if evidence.get("protocol_status") == "unknown" else ""))))
                item.setCheckState(Qt.Checked if pair in old or pair in existing else Qt.Unchecked)
                if unsupported or pair in existing:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                self.model_list.addItem(item)
        self.model_list.blockSignals(False)
        self._filter(self.search.text())
        self._changed()

    def selected_pairs(self):
        return {(item.data(Qt.UserRole)[0], item.data(Qt.UserRole)[1]["id"])
                for item in self._items() if item.flags() & Qt.ItemIsEnabled and item.checkState() == Qt.Checked}

    def selected_records(self):
        return [item.data(Qt.UserRole)[1] for item in self._items()
                if item.flags() & Qt.ItemIsEnabled and item.checkState() == Qt.Checked]

    def _items(self):
        return [self.model_list.item(index) for index in range(self.model_list.count())]

    def _filter(self, text):
        for item in self._items():
            item.setHidden(bool(text.strip() and text.strip().casefold() not in item.data(Qt.UserRole + 1)))

    def _select_all(self):
        for item in self._items():
            if not item.isHidden() and item.flags() & Qt.ItemIsEnabled:
                item.setCheckState(Qt.Checked)

    def _changed(self, *_):
        self.summary.setText(f"返回 {self.model_list.count()} 个 · 已选择 {len(self.selected_pairs())} 个")
        self.selectionChanged.emit()

    def refresh_theme(self, _resolved=None):
        self.search.setStyleSheet(model_field_style())
        self.select_all.setStyleSheet(product_button_style("secondary"))
        for label in (self.note, self.summary):
            label.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_secondary}; }}")
        self.model_list.setStyleSheet(f"QListWidget {{ background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border}; border-radius: 6px; }} QListWidget::item {{ padding: 10px 8px; }}")


class GatewaySetup(QWidget):
    channelsReady = Signal(list)
    cancelled = Signal()
    changed = Signal()

    def __init__(self, parent=None, existing=(), source=None, name=""):
        super().__init__(parent)
        self.account = None
        self.worker = None
        self.results = {}
        self.errors = {}
        self.keys = []
        self.existing = set(existing)
        self._expected_source = dict(source or {})
        self._generation = 0
        self._pending_input = False
        self._manual_channel_id = "openai-custom-" + uuid.uuid4().hex[:8]
        self._manual_profile = None
        self._buttons = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.title = QLabel("添加服务", self)
        layout.addWidget(self.title)
        self.stack = ModelPageStack(self)
        layout.addWidget(self.stack)
        self.login_page, form = self._page()
        self.name_input = QLineEdit(self.login_page)
        self.name_input.setText(name)
        self.name_input.setPlaceholderText("选填，默认使用站点名称")
        self._field(form, "服务名称", self.name_input)
        self.mode = ModelFieldCombo(self.login_page)
        self.mode.addItem("账号登录", "account")
        self.mode.addItem("填写 API Key", "manual")
        self._field(form, "连接方式", self.mode)
        self.url_input = QLineEdit(self.login_page)
        self.url_input.setPlaceholderText("https://example.com")
        self._field(form, "站点地址", self.url_input)
        self.account_fields = QWidget(self.login_page)
        fields = QVBoxLayout(self.account_fields)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        self.platform = ModelFieldCombo(self.account_fields)
        for title, value in (("自动识别", "auto"), ("sub2api", "sub2api"), ("New API", "new_api")):
            self.platform.addItem(title, value)
        self._field(fields, "站点类型", self.platform)
        self.login_credentials = QWidget(self.account_fields)
        fields.addWidget(self.login_credentials)
        fields = QVBoxLayout(self.login_credentials)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        self.username = QLineEdit(self.login_credentials)
        self.username.setPlaceholderText("sub2api 使用邮箱，New API 使用用户名")
        self.username_field = self._field(fields, "邮箱 / 用户名", self.username)
        self.password = QLineEdit(self.login_credentials)
        self.password.setEchoMode(QLineEdit.Password)
        self._field(fields, "密码（仅用于本次接入）", self.password)
        self.code = QLineEdit(self.login_credentials)
        self.code.setPlaceholderText("输入身份验证器中的验证码")
        self.code_field = self._field(fields, "双重验证码", self.code)
        self.code_field.hide()
        self.login_credentials.hide()
        form.addWidget(self.account_fields)
        self.manual_fields = QWidget(self.login_page)
        fields = QVBoxLayout(self.manual_fields)
        fields.setContentsMargins(0, 0, 0, 0)
        self.provider = ModelFieldCombo(self.manual_fields)
        self.provider.addItem("OpenAI 兼容", "openai")
        self.provider.addItem("Anthropic Messages", "anthropic")
        self._field(fields, "调用方式", self.provider)
        self.api_key = QLineEdit(self.manual_fields)
        self.api_key.setEchoMode(QLineEdit.Password)
        self._field(fields, "访问密钥（API Key）", self.api_key)
        form.addWidget(self.manual_fields)
        self.manual_fields.hide()
        self.connect_btn = self._button("识别站点", form, self.connect_or_probe, primary=True)
        self.manual_btn = self._button("直接配置模型", form, self.manual_without_discovery)
        self.manual_btn.hide()

        self.keys_page, key_layout = self._page()
        self.keys_title = QLabel("选择要接入的密钥", self.keys_page)
        key_layout.addWidget(self.keys_title)
        self.key_list = QListWidget(self.keys_page)
        self.key_list.setMinimumHeight(180)
        self.key_list.setMaximumHeight(330)
        self.key_list.setWordWrap(True)
        self.key_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        key_layout.addWidget(self.key_list)
        self.discover_btn = self._button("获取所选密钥的模型", key_layout, self.discover, primary=True)
        self.refresh_keys_btn = self._button("刷新密钥列表", key_layout, self.refresh_keys)
        self.edit_login_btn = self._button("返回编辑连接", key_layout, self.edit_login)
        self.create_btn = self._button("创建 Cowork 专用密钥", key_layout, self.prepare_creation)
        self.create_panel = QWidget(self.stack)
        create_layout = QVBoxLayout(self.create_panel)
        create_layout.setContentsMargins(0, 0, 0, 0)
        create_layout.setSpacing(12)
        create_layout.addWidget(QLabel("创建 Cowork 专用密钥", self.create_panel))
        self.key_name = QLineEdit("Cowork", self.create_panel)
        self._field(create_layout, "密钥名称", self.key_name)
        name_note = QLabel("名称会附加短标识，方便核对创建结果。", self.create_panel)
        name_note.setWordWrap(True)
        create_layout.addWidget(name_note)
        self.group = ModelFieldCombo(self.create_panel)
        self._field(create_layout, "使用分组", self.group)
        self.days = QSpinBox(self.create_panel)
        self.days.setRange(0, 3650)
        self.days.setSpecialValueText("不过期")
        self.days.setSuffix(" 天")
        self._field(create_layout, "有效期", self.days)
        self.quota = QDoubleSpinBox(self.create_panel)
        self.quota.setRange(0, 1000000)
        self.quota.setDecimals(2)
        self.quota.setSpecialValueText("不另设上限，使用账号可用额度")
        self.quota.setSuffix(" USD")
        self._field(create_layout, "密钥额度", self.quota)
        self.create_submit = self._button("创建密钥", create_layout, self.create_key, primary=True)
        self.reconcile_btn = self._button("核对创建结果", create_layout, self.reconcile_creation)
        self.reconcile_btn.hide()
        self._button("返回密钥列表", create_layout, lambda: self.stack.setCurrentWidget(self.keys_page))
        self.stack.addWidget(self.create_panel)
        self.create_panel.hide()

        self.models_page, model_layout = self._page()
        self.selection = ModelSelectionPanel(self.models_page)
        model_layout.addWidget(self.selection)
        self.selection.selectionChanged.connect(self._selection_changed)
        self.add_btn = self._button("添加所选模型", model_layout, self.finish_import, primary=True)
        self.retry_btn = self._button("重试失败的密钥", model_layout, self.retry_failed)
        self.retry_btn.hide()
        self.retry_rows = QWidget(self.models_page)
        self.retry_layout = QVBoxLayout(self.retry_rows)
        self.retry_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.retry_rows)
        self.back_keys_btn = self._button("返回选择密钥", model_layout, self.back_from_models)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.insertWidget(1, self.status)
        actions = QHBoxLayout()
        self.stop_btn = QPushButton("取消当前请求", self)
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        self.website_btn = QPushButton("前往站点", self)
        self.website_btn.clicked.connect(self.open_website)
        self.cancel_btn = QPushButton("返回服务列表", self)
        self.cancel_btn.clicked.connect(self.cancelled)
        for button in (self.stop_btn, self.website_btn, self.cancel_btn):
            self._buttons.append((button, False))
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for field in (self.url_input, self.username, self.password, self.code, self.api_key, self.name_input):
            field.textChanged.connect(self._input_changed)
        self.url_input.textEdited.connect(self._site_changed)
        self.username.textEdited.connect(self._account_changed)
        self.platform.activated.connect(self._site_changed)
        if source:
            self.url_input.setText(source["site_url"])
            self.platform.setCurrentIndex(self.platform.findData(source["platform"]))
        self._selection_changed()
        bind_theme(self, self.refresh_theme, surface="settings")

    def _page(self):
        page = QWidget(self.stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.stack.addWidget(page)
        return page, layout

    def _field(self, layout, text, widget):
        field = QWidget(widget.parentWidget())
        box = QVBoxLayout(field)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        label = QLabel(text, field)
        label.setBuddy(widget)
        box.addWidget(label)
        box.addWidget(widget)
        layout.addWidget(field)
        return field

    def _button(self, text, layout, callback, primary=False):
        button = QPushButton(text, layout.parentWidget())
        button.clicked.connect(callback)
        self._buttons.append((button, primary))
        layout.addWidget(button, 0, Qt.AlignLeft)
        return button

    def _input_changed(self, *_):
        self._pending_input = True
        self.changed.emit()

    def has_pending_input(self):
        return self._pending_input or bool(self.worker or self.results)

    def _credentials_changed(self, *_):
        self.close_session()
        self.results.clear()
        self.errors.clear()
        self.keys.clear()
        self.code_field.hide()
        self.code.clear()
        self.connect_btn.setText("识别站点")

    def _site_changed(self, *_):
        self._credentials_changed()
        self.login_credentials.hide()

    def _account_changed(self, *_):
        public = dict(self.account.public) if self.account else {}
        platform = self.account.platform if self.account else self.platform.currentData()
        self._credentials_changed()
        if public:
            self.account = GatewayAccount(self.url_input.text(), platform)
            self.account.public = public
            self.connect_btn.setText("连接并获取密钥")

    def _mode_changed(self, *_):
        self._credentials_changed()
        manual = self.mode.currentData() == "manual"
        self.account_fields.setVisible(not manual)
        self.manual_fields.setVisible(manual)
        self.manual_btn.setVisible(manual)
        self.code_field.hide()
        self.login_credentials.hide()
        self.connect_btn.setText("获取模型" if manual else "识别站点")

    def _run(self, operation, task, done):
        if self.worker:
            return
        if self.account:
            self.account.cancelled.clear()
        self._generation += 1
        worker = ConnectionWorker(operation, task, self.account)
        worker.generation = self._generation
        worker.done = done
        self.worker = worker
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.release)
        worker.finished.connect(worker.deleteLater)
        self._busy(True)
        self.status.setText({"login": "正在登录并读取密钥…", "verify": "正在验证…", "discover": "正在按密钥获取模型…",
                             "create": "正在创建密钥…", "reconcile": "正在核对远端创建结果…"}.get(operation, "正在读取站点信息…"))
        worker.start()

    def _finished(self):
        worker = self.sender()
        if worker is not self.worker or worker.generation != self._generation:
            return
        self.worker = None
        self._busy(False)
        result = worker.result or {}
        if result.get("ok"):
            self.status.clear()
            worker.done(result.get("value"))
        else:
            self.status.setText(result.get("error", "请求未完成"))
        if self.account and (self.account.created_keys or self.account.pending_creation):
            self.status.setText(self.status.text() + "\n远端密钥已创建或创建结果待确认；本地返回或放弃不会删除它，请在站点管理。")
        if self.account:
            self.reconcile_btn.setVisible(bool(self.account.pending_creation))
            self.create_submit.setEnabled(not self.account.pending_creation)

    def _busy(self, busy):
        self.login_page.setEnabled(not busy)
        self.keys_page.setEnabled(not busy)
        self.create_panel.setEnabled(not busy)
        self.models_page.setEnabled(not busy)
        self.stop_btn.setVisible(busy)
        if not busy:
            self._selection_changed()

    def stop(self):
        if self.worker:
            self.worker.requestInterruption()
            if self.account:
                self.account.cancel()
            self.status.setText("正在取消，已获取的结果会保留…")

    def close_session(self):
        self._generation += 1
        if self.worker:
            self.worker.dispose_session = True
            self.worker.requestInterruption()
            if self.account:
                self.account.cancel()
            self.worker = None
        elif self.account:
            self.account.close()
        self.account = None
        self._busy(False)

    def dispose(self):
        self.close_session()
        for field in (self.password, self.code, self.api_key):
            field.clear()
        self.results.clear()

    def connect_account(self):
        if self.mode.currentData() == "manual":
            return self.discover_manual()
        try:
            if self.account is None:
                self.account = GatewayAccount(self.url_input.text(), self.platform.currentData())
            account = self.account
            username, password, code = self.username.text(), self.password.text(), self.code.text()
            verify = bool(account.challenge)
            def task():
                auth = account.complete_2fa(code) if verify else account.login(username, password)
                return {"auth": auth, "keys": [] if auth.get("requires_2fa") else account.list_keys()}
            self._run("verify" if verify else "login", task, self._logged_in)
        except GatewayError as error:
            self.status.setText(str(error))

    def connect_or_probe(self):
        if self.mode.currentData() == "manual" or (self.account and self.account.public):
            self.connect_account()
            return
        try:
            self.account = GatewayAccount(self.url_input.text(), self.platform.currentData())
            self._run("probe", self.account.probe, self._probed)
        except GatewayError as error:
            self.status.setText(str(error))

    def _probed(self, result):
        platform = result["platform"]
        self.platform.setCurrentIndex(self.platform.findData(platform))
        self.username_field.findChild(QLabel).setText("邮箱" if platform == "sub2api" else "用户名")
        self.username.setPlaceholderText("输入站点邮箱" if platform == "sub2api" else "输入站点用户名")
        self.login_credentials.show()
        self.connect_btn.setText("连接并获取密钥")
        self.username.setFocus()

    def _logged_in(self, result):
        self.password.clear()
        self.code.clear()
        if result["auth"].get("requires_2fa"):
            self.code_field.show()
            self.connect_btn.setText("验证并获取密钥")
            self.code.setFocus()
        else:
            if self._expected_source and (self.account.account_id != self._expected_source.get("account_id")
                                          or self.account.site_url != self._expected_source.get("site_url")):
                self.existing.clear()
            self._show_keys(result["keys"])

    def refresh_keys(self):
        self._run("keys", self.account.list_keys, self._show_keys)

    def edit_login(self):
        self.stack.setCurrentWidget(self.login_page)

    def back_from_models(self):
        self.stack.setCurrentWidget(self.login_page if self.mode.currentData() == "manual" else self.keys_page)

    def _show_keys(self, keys):
        selected = {item.data(Qt.UserRole)["id"] for item in self._key_items() if item.checkState() == Qt.Checked}
        self.keys = keys
        self.key_list.clear()
        for key in keys:
            details = [key["group_name"], key["reason"] or "可接入", key.get("quota_label", "")]
            if key.get("ip_limited"):
                details.append("限制 IP")
            if key.get("model_limits"):
                details.append("限制模型：" + key["model_limits"])
            item = QListWidgetItem(key["name"] + "\n" + " · ".join(details))
            item.setToolTip("有效期：" + key["expires_at"])
            item.setData(Qt.UserRole, key)
            item.setCheckState(Qt.Checked if key["id"] in selected else Qt.Unchecked)
            if not key["usable"]:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.key_list.addItem(item)
        self.stack.setCurrentWidget(self.keys_page)
        if not keys:
            self.status.setText("此账号还没有密钥，可以创建 Cowork 专用密钥。")

    def _key_items(self):
        return [self.key_list.item(index) for index in range(self.key_list.count())]

    def prepare_creation(self):
        self._run("groups", self.account.groups, self._show_creation)

    def _show_creation(self, groups):
        self.quota.setSuffix(" USD" if self.account.platform == "sub2api" else " 站点计价单位")
        self.group.clear()
        for group in groups:
            self.group.addItem(group["name"], group["id"])
        if not groups:
            self.status.setText("当前账号没有可用分组，请前往站点检查账号权限。")
            return
        self.stack.setCurrentWidget(self.create_panel)

    def create_key(self):
        name, group, days, quota = self.key_name.text(), self.group.currentData(), self.days.value(), self.quota.value()
        account = self.account
        self._run("create", lambda: account.create_key(name, group or "", days, quota), self._created)

    def reconcile_creation(self):
        self._run("reconcile", self.account.reconcile_creation, self._created)

    def _created(self, key):
        self._show_keys([item for item in self.keys if item["id"] != key["id"]] + [key])
        self.key_list.item(self.key_list.count() - 1).setCheckState(Qt.Checked)
        self.create_panel.hide()
        self.status.setText("专用密钥已在站点创建，已选中。")

    def discover(self):
        keys = [item.data(Qt.UserRole) for item in self._key_items()
                if item.checkState() == Qt.Checked and item.flags() & Qt.ItemIsEnabled]
        if not keys:
            self.status.setText("请至少选择一个可用密钥。")
            return
        allowed = {key["id"] for key in keys}
        self.results = {key: result for key, result in self.results.items() if key in allowed}
        self.errors = {}
        self._discover_keys(keys)

    def _discover_keys(self, keys):
        account = self.account
        def task():
            results, errors = {}, {}
            for key in keys:
                if account.cancelled.is_set():
                    errors[key["id"]] = "已取消，可重试"
                    continue
                try:
                    results[key["id"]] = account.discover(key)
                except GatewayError as error:
                    errors[key["id"]] = str(error)
            return results, errors
        self._run("discover", task, self._discovered)

    def _discovered(self, value):
        results, errors = value
        self.results.update(results)
        for key in results:
            self.errors.pop(key, None)
        self.errors.update(errors)
        self.selection.set_records(self.results, self.existing)
        self.stack.setCurrentWidget(self.models_page)
        self.back_keys_btn.setText("返回编辑连接" if self.mode.currentData() == "manual" else "返回选择密钥")
        self.retry_btn.setVisible(bool(self.errors))
        while self.retry_layout.count():
            child = self.retry_layout.takeAt(0).widget()
            if child:
                child.hide()
                child.deleteLater()
        for key_id in self.errors:
            info = next((item for item in self.keys if item["id"] == key_id), None)
            if info:
                button = QPushButton("重试 “" + info["name"] + "”", self.retry_rows)
                button.setStyleSheet(product_button_style("secondary"))
                button.clicked.connect(lambda _checked=False, key=info: self._discover_keys([key]))
                self.retry_layout.addWidget(button, 0, Qt.AlignLeft)
        self.status.setText("\n".join(f"{next((item['name'] for item in self.keys if item['id'] == key), key)}：{error}" for key, error in self.errors.items()))
        if results and not any(result["records"] for result in results.values()):
            self.status.setText("站点返回的模型列表为空，已有配置已保留。可返回检查密钥分组。")
        warnings = {record.get("discovery", {}).get("metadata_warning", "") for result in self.results.values() for record in result["records"]}
        self.status.setText("\n".join(filter(None, [self.status.text(), *sorted(warnings)])))

    def retry_failed(self):
        self._discover_keys([key for key in self.keys if key["id"] in self.errors])

    def _manual_config(self):
        url = self.url_input.text().strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("请填写有效的服务地址。")
        if not self.api_key.text().strip():
            raise GatewayError("请填写 API Key。")
        if self.provider.currentData() == "openai" and not parsed.path.rstrip("/"):
            url = url.rstrip("/") + "/v1"
        return {"channel_id": self._manual_channel_id, "display_name": self.name_input.text().strip() or parsed.hostname,
                "provider_type": self.provider.currentData(), "base_url": url,
                "api_key": self.api_key.text().strip(), "models": []}

    def discover_manual(self):
        try:
            profile = self._manual_config()
            if self._manual_profile != profile:
                self.results.clear()
                self.errors.clear()
                self.selection.set_records({})
            self._manual_profile = profile
            def task():
                raw = list_available_models(profile["provider_type"], profile["base_url"], profile["api_key"])
                records = [record if record.get("discovery") else catalog_record(record, default_provider=profile["provider_type"]) for record in raw]
                return {"manual": {"records": records, "key_info": {"name": profile["display_name"]}}}, {}
            self._run("discover", task, self._discovered)
        except GatewayError as error:
            self.status.setText(str(error))

    def manual_without_discovery(self):
        try:
            self.channelsReady.emit([self._manual_config()])
        except GatewayError as error:
            self.status.setText(str(error))

    def _selection_changed(self):
        if hasattr(self, "add_btn"):
            self.add_btn.setEnabled(bool(self.selection.selected_pairs()))

    def finish_import(self):
        pairs = self.selection.selected_pairs()
        if not pairs:
            return
        if self.mode.currentData() == "manual":
            profile = dict(self._manual_profile)
            channels = []
            for provider in ("openai", "anthropic"):
                records = [record for record in self.selection.selected_records() if record["provider_type"] == provider]
                if records:
                    channel = dict(profile, provider_type=provider, channel_id=profile["channel_id"] + "-" + provider)
                    if provider != profile["provider_type"]:
                        channel["base_url"] = normalize_site_url(profile["base_url"]) + ("/v1" if provider == "openai" else "")
                    channel["models"] = [new_catalog_model(record, channel["channel_id"], channel["base_url"]) for record in records]
                    channels.append(channel)
        else:
            name = self.name_input.text().strip() or urlsplit(self.account.site_url).hostname
            channels = channels_from_selection(self.results, pairs, name)
        self.channelsReady.emit(channels)

    def open_website(self):
        try:
            QDesktopServices.openUrl(QUrl(normalize_site_url(self.url_input.text())))
        except GatewayError as error:
            self.status.setText(str(error))

    def refresh_theme(self, _resolved=None):
        self.title.setStyleSheet(f"QLabel {{ font-size: {DesignTokens.font_size_page}px; font-weight: 600; color: {DesignTokens.text_primary}; }}")
        self.status.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_secondary}; }}")
        for field in self.findChildren(QLineEdit) + self.findChildren(ModelFieldCombo) + self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox):
            field.setStyleSheet(model_field_style())
        for combo in self.findChildren(ModelFieldCombo):
            apply_model_combo_style(combo, popup_selection=DesignTokens.primary_soft)
        for button, primary in self._buttons:
            button.setStyleSheet(product_button_style("primary" if primary else "secondary"))
        for button in self.retry_rows.findChildren(QPushButton):
            button.setStyleSheet(product_button_style("secondary"))
        self.key_list.setStyleSheet(f"QListWidget {{ background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border}; border-radius: 6px; }} QListWidget::item {{ padding: 10px 8px; }}")
