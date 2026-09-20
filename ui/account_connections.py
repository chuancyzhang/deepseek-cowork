"""Optional connection management. Network work never runs on the Qt UI thread."""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QCheckBox, QFileDialog, QMessageBox, QPlainTextEdit)

from core.connections.broker import ConnectionBroker
from core.connections.errors import ConnectionError, MESSAGES
from core.theme import bind_theme, DesignTokens
from ui.knowledge_library import LibraryJobs, friendly_date
from ui.primitives import product_button_style, product_field_style, ProductMessageDialog

STATES = {"not_configured": "未配置", "authenticating": "认证中", "ready": "可用", "saved": "已保存 · 尚未验证",
          "authentication_required": "等待认证", "disconnected": "已断开", "forbidden": "无权限", "unavailable": "不可达"}
SOURCES = {"weknora": "WeKnora", "tencent-docs": "腾讯文档", "lexiang": "乐享知识库"}


class AccountConnectionsPage(QWidget):
    progress = Signal(object)

    def __init__(self, parent=None, *, config_manager=None, broker=None):
        super().__init__(parent)
        self.setObjectName("AccountConnectionsPage")
        self.config_manager = config_manager
        self.broker = broker or ConnectionBroker(data_dir=getattr(config_manager, "data_dir", None))
        self.jobs = LibraryJobs(self)
        self.cancel_event = None
        self.confirm_event = None
        self.busy = False
        self.fields = {}
        self.current_id = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("AccountConnectionsBody")
        self.body = body
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        title = QLabel("账号与连接")
        title.setObjectName("ConnectionsHeading")
        layout.addWidget(title)
        intro = QLabel("登录后，可在资料库和对话中使用已连接的服务。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.notice)
        self.templates = QComboBox()
        self.templates.setMinimumContentsLength(12)
        self.templates.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.other_account_button = QPushButton("添加其他账号")
        self.other_account_button.setCheckable(True)
        self.other_account_button.toggled.connect(self.show_service_picker)
        layout.addWidget(self.other_account_button)
        self.service_label = QLabel("选择公司或服务")
        layout.addWidget(self.service_label)
        layout.addWidget(self.templates)
        self.add_button = QPushButton("添加账号")
        self.add_button.clicked.connect(self.add)
        layout.addWidget(self.add_button)
        self.template_tools_button = QPushButton("管理登录配置")
        self.template_tools_button.setCheckable(True)
        layout.addWidget(self.template_tools_button)
        self.template_tools = QWidget()
        template_layout = QVBoxLayout(self.template_tools)
        template_layout.setContentsMargins(0, 0, 0, 0)
        self.template_tools.hide()
        self.template_tools_button.toggled.connect(self.template_tools.setVisible)
        actions = QGridLayout()
        self.import_button = self.button("导入管理员配置", self.import_templates, actions, 0, 0)
        self.export_button = self.button("导出登录配置", self.export_templates, actions, 0, 1)
        self.remove_template_button = self.button("移除本机配置", self.remove_template, actions, 1, 0)
        template_layout.addLayout(actions)
        self.edit_template_button = QPushButton("高级：编辑登录参数")
        self.edit_template_button.clicked.connect(self.edit_template)
        template_layout.addWidget(self.edit_template_button)
        self.template_editor = QPlainTextEdit()
        self.template_editor.setPlaceholderText("粘贴声明式模板 JSON；不接受密码、令牌或执行脚本。")
        self.template_editor.setMinimumHeight(180)
        self.template_editor.hide()
        template_layout.addWidget(self.template_editor)
        self.save_template_button = QPushButton("校验并保存模板")
        self.save_template_button.clicked.connect(self.save_template)
        self.save_template_button.hide()
        template_layout.addWidget(self.save_template_button)
        layout.addWidget(self.template_tools)
        self.list = QListWidget()
        self.list.setMinimumHeight(90)
        self.list.setMaximumHeight(130)
        self.list.currentItemChanged.connect(self.select_item)
        layout.addWidget(self.list)
        self.details = QLabel("选择上方服务，添加账号后即可登录。")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.form = QWidget()
        self.form_layout = QVBoxLayout(self.form)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form)
        self.login_button = QPushButton("登录")
        self.login_button.clicked.connect(self.login)
        layout.addWidget(self.login_button)
        login_actions = QGridLayout()
        self.cancel_button = self.button("取消登录", self.cancel_login, login_actions, 0, 0)
        self.confirm_button = self.button("已完成浏览器授权", self.confirm_login, login_actions, 0, 1)
        self.cancel_button.hide()
        self.confirm_button.hide()
        layout.addLayout(login_actions)
        self.permissions = QWidget()
        permission_layout = QVBoxLayout(self.permissions)
        permission_layout.setContentsMargins(0, 0, 0, 0)
        permission_layout.setSpacing(10)
        permission_layout.addWidget(QLabel("用于哪些功能"))
        self.capability = QComboBox()
        self.capability.setEditable(True)
        self.capability.currentIndexChanged.connect(self.capability_changed)
        self.capability.editTextChanged.connect(self.capability_changed)
        permission_layout.addWidget(self.capability)
        self.permission_hint = QLabel("")
        self.permission_hint.setWordWrap(True)
        permission_layout.addWidget(self.permission_hint)
        self.read_permission = QCheckBox("允许浏览和读取服务中的资料")
        self.read_permission.setChecked(True)
        self.write_permission = QCheckBox("允许保存成果到资料库")
        permission_layout.addWidget(self.read_permission)
        permission_layout.addWidget(self.write_permission)
        self.with_skill = QCheckBox("允许对话读取我选定的资料")
        self.with_skill.setChecked(True)
        permission_layout.addWidget(self.with_skill)
        self.scope_button = QPushButton("更多授权选项")
        self.scope_button.setCheckable(True)
        permission_layout.addWidget(self.scope_button)
        self.scope_options = QWidget()
        scope_layout = QVBoxLayout(self.scope_options)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        self.scope_options.hide()
        self.scope_button.toggled.connect(self.scope_options.setVisible)
        self.operations = QLineEdit()
        self.operations.setPlaceholderText("操作标识，多个用英文逗号分隔")
        scope_layout.addWidget(self.operations)
        self.resources = QLineEdit()
        self.resources.setPlaceholderText("对话可用的资料库 ID（可选，用英文逗号分隔）")
        scope_layout.addWidget(self.resources)
        self.requirement_id = QLineEdit("default")
        self.requirement_id.setPlaceholderText("能力声明中的连接需求 ID")
        scope_layout.addWidget(self.requirement_id)
        self.adopt = QCheckBox("身份完全一致时，沿用原有资料引用")
        scope_layout.addWidget(self.adopt)
        permission_layout.addWidget(self.scope_options)
        self.use_button = QPushButton("确认使用")
        self.use_button.clicked.connect(self.use_connection)
        permission_layout.addWidget(self.use_button)
        self.mode_label = QLabel()
        self.mode_label.setWordWrap(True)
        permission_layout.addWidget(self.mode_label)
        layout.addWidget(self.permissions)
        self.more_button = QPushButton("连接详情与更多操作")
        self.more_button.setCheckable(True)
        layout.addWidget(self.more_button)
        self.more_options = QWidget()
        more_layout = QVBoxLayout(self.more_options)
        more_layout.setContentsMargins(0, 0, 0, 0)
        self.more_options.hide()
        self.more_button.toggled.connect(self.more_options.setVisible)
        self.technical_details = QLabel()
        self.technical_details.setWordWrap(True)
        self.technical_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        more_layout.addWidget(self.technical_details)
        manage = QGridLayout()
        self.verify_button = self.button("检查连接", self.verify, manage, 0, 0)
        self.disconnect_button = self.button("断开此连接", self.disconnect, manage, 0, 1)
        self.logout_button = self.button("退出关联账号", self.logout_account, manage, 1, 0)
        self.delete_button = self.button("删除连接", self.delete, manage, 1, 1)
        self.legacy_button = self.button("切回原有配置", self.use_legacy, manage, 2, 0)
        self.revoke_button = self.button("撤销所选功能授权", self.revoke, manage, 2, 1)
        self.refresh_button = self.button("刷新状态", self.refresh, manage, 3, 0)
        more_layout.addLayout(manage)
        self.grants_label = QLabel("")
        self.grants_label.setWordWrap(True)
        more_layout.addWidget(self.grants_label)
        layout.addWidget(self.more_options)
        # Administrator controls follow the user's login and authorization task.
        layout.removeWidget(self.template_tools_button)
        layout.removeWidget(self.template_tools)
        layout.addWidget(self.template_tools_button)
        layout.addWidget(self.template_tools)
        self.waits_label = QLabel("")
        self.waits_label.setWordWrap(True)
        layout.addWidget(self.waits_label)
        layout.addStretch()
        self.progress.connect(self.show_progress)
        self.destroyed.connect(self._cancel_on_destroy)
        self.poll = QTimer(self)
        self.poll.setInterval(2000)
        self.poll.timeout.connect(self.refresh_waits)
        self.poll.start()
        bind_theme(self, self.refresh_theme, surface="management")
        self.refresh()

    def button(self, label, callback, layout, row, column):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button, row, column)
        return button

    def show_service_picker(self, expanded):
        visible = bool(expanded or not self.current_id)
        self.service_label.setVisible(visible and bool(self.templates.count()))
        self.templates.setVisible(visible and bool(self.templates.count()))
        self.add_button.setVisible(visible and bool(self.templates.count()))

    def refresh_theme(self, *_):
        self.setStyleSheet(f"QWidget#AccountConnectionsBody {{ background: {DesignTokens.bg_app}; }} QWidget#AccountConnectionsBody QLabel, QWidget#AccountConnectionsBody QCheckBox {{ color: {DesignTokens.text_primary}; }} QLabel#ConnectionsHeading {{ font-size: 20px; font-weight: 600; }}")
        for field in [self.templates, self.capability, self.operations, self.resources, self.requirement_id, self.template_editor, *self.fields.values()]:
            field.setStyleSheet(product_field_style())
        for label in self.findChildren(QLabel):
            label.setTextFormat(Qt.PlainText)
        ready = bool(self.current_id and self.broker.store.get(self.current_id)["state"] == "ready")
        primary = self.use_button if ready else self.login_button if self.current_id else self.add_button
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(product_button_style("primary" if button is primary else "secondary"))

    def refresh(self):
        template_id = self.templates.currentData()
        templates, errors = self.broker.catalog.load()
        self.templates.clear()
        for item in templates:
            self.templates.addItem(item["name"] + (" · 管理员预置" if item["locked"] else ""), item["id"])
        self.templates.setCurrentIndex(max(0, self.templates.findData(template_id)))
        self.add_button.setEnabled(bool(templates) and not self.busy)
        self.templates.setVisible(bool(templates))
        self.add_button.setVisible(bool(templates))
        if not templates:
            self.template_tools_button.setChecked(True)
            self.details.setText("还没有登录配置。请导入管理员提供的配置，再选择服务登录。")
        if errors:
            self.notice.setText("部分模板未加载：" + "；".join(MESSAGES.get(e["code"], MESSAGES["invalid_template"]) for e in errors))
        selected = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        for item in self.broker.store.list():
            who = item.get("profile", {}).get("label") or (item.get("identity") or {}).get("subject", "未登录")
            row = QListWidgetItem(f"{item['name']} · {STATES.get(item['state'], item['state'])}\n{who}")
            row.setData(Qt.UserRole, item["id"])
            self.list.addItem(row)
            if item["id"] == selected:
                self.list.setCurrentItem(row)
        self.list.blockSignals(False)
        self.list.setVisible(self.list.count() > 1)
        self.list.setFixedHeight(min(130, max(60, self.list.count() * 48)))
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        elif not self.busy:
            self.select_item(self.list.currentItem())
        self.refresh_waits()

    def selected(self):
        if not self.current_id:
            raise ConnectionError("not_connected")
        return self.broker.store.get(self.current_id)

    def select_item(self, current, _previous=None):
        if self.busy:
            return
        next_id = current.data(Qt.UserRole) if current else None
        if next_id != self.current_id and any(field.text() for field in self.fields.values()) and not self.confirm_leave():
            self.list.blockSignals(True)
            if _previous:
                self.list.setCurrentItem(_previous)
            self.list.blockSignals(False)
            return
        self.current_id = current.data(Qt.UserRole) if current else None
        self.other_account_button.setVisible(bool(current))
        self.show_service_picker(self.other_account_button.isChecked())
        self.fields.clear()
        while self.form_layout.count():
            widget = self.form_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        for button in (self.login_button, self.verify_button, self.disconnect_button, self.logout_button,
                       self.delete_button, self.use_button, self.revoke_button, self.legacy_button):
            button.setEnabled(bool(current))
        self.login_button.setVisible(bool(current))
        self.more_button.setVisible(bool(current))
        self.more_options.setVisible(bool(current) and self.more_button.isChecked())
        self.permissions.setVisible(False)
        self.form.setVisible(bool(current))
        if not current:
            self.details.setText("选择上方服务，添加账号后即可登录。" if self.templates.count() else "还没有登录配置。请导入管理员提供的配置，再选择服务登录。")
            self.refresh_theme()
            return
        item = self.selected()
        template = item["template"]
        who = item.get("identity") or {}
        self.details.setText(f"{item['name']} · {STATES.get(item['state'], item['state'])}" +
                             (f"\n{item.get('profile', {}).get('label') or who.get('subject', '')}" if who else ""))
        self.technical_details.setText(f"服务地址：{template.get('base_url', template['parameters'].get('host', ''))}\n"
                                      f"账号标识：{who.get('subject', '未登录')}　组织：{who.get('tenant') or '—'}\n"
                                      f"最近检查：{friendly_date(item.get('verified_at'))}")
        adapter = template["adapter"]
        fields = {"api_key": [("token", "访问令牌", True)], "superset": [("username", "用户名", False), ("password", "密码", True)],
                  "weknora": [("username", "邮箱", False), ("password", "密码", True)],
                  "ldap": [("username", "公司账号", False), ("password", "密码", True)],
                  "lexiang": [("token", "访问令牌", True)], "wecom_app": [("secret", "企业应用 Secret", True)]}.get(adapter, [])
        if adapter == "ldap" and template["parameters"].get("bind_dn"):
            fields.append(("bind_password", "目录查询账号密码", True))
        if adapter == "oauth2" and template["parameters"].get("flow") == "client_credentials":
            fields = [("client_secret", "本机应用 Secret", True)]
        for key, label, sensitive in fields:
            field = QLineEdit()
            caption = QLabel(label)
            caption.setBuddy(field)
            self.form_layout.addWidget(caption)
            field.setAccessibleName(label)
            field.returnPressed.connect(self.login)
            if sensitive:
                field.setEchoMode(QLineEdit.Password)
            self.form_layout.addWidget(field)
            self.fields[key] = field
        ordered_fields = list(self.fields.values())
        for before, after in zip(ordered_fields, ordered_fields[1:]):
            QWidget.setTabOrder(before, after)
        if ordered_fields:
            QWidget.setTabOrder(ordered_fields[-1], self.login_button)
        self.form.setVisible(item["state"] != "ready")
        self.login_button.setText("重新登录" if item["state"] == "ready" else "登录")
        self.permissions.setVisible(item["state"] == "ready" and not item.get("profile", {}).get("directory_only"))
        if item.get("profile", {}).get("directory_only"):
            self.details.setText(self.details.text() + "\n公司目录身份已确认。使用业务服务时，请另外添加对应服务账号。")
        self.logout_button.setEnabled(bool(item.get("login_session_id")))
        self.capability.blockSignals(True)
        self.capability.clear()
        source = template["service"]
        if source in SOURCES:
            self.capability.addItem("资料库 · " + SOURCES[source], "library:" + source)
            self.capability.addItem("资料库 Skill · " + SOURCES[source], "knowledge-library")
        if adapter == "superset":
            self.capability.addItem("Superset MCP", "mcp:superset-mcp")
        if self.config_manager:
            for server in self.config_manager.get_mcp_servers():
                cap = "mcp:" + server["id"]
                if self.capability.findData(cap) < 0:
                    self.capability.addItem("MCP · " + server["name"], cap)
        for cap in template.get("suggested_capabilities", []):
            if self.capability.findData(cap) < 0:
                self.capability.addItem(cap, cap)
        self.capability.blockSignals(False)
        self.capability_changed()
        self.grants_label.setText("已授权：" + ("；".join(g["capability"] + "（" + ", ".join(g["operations"]) + "）" for g in self.broker.store.grants(item["id"])) or "无"))
        self.refresh_theme()

    def capability_id(self):
        # A typed value is a stable capability ID, not a translated label.
        index = self.capability.currentIndex()
        return str(self.capability.itemData(index) if index >= 0 and self.capability.currentText() == self.capability.itemText(index) else self.capability.currentText()).strip()

    def capability_changed(self):
        cap = self.capability_id()
        is_library = cap.startswith("library:") or cap == "knowledge-library"
        self.operations.setText("discover, call" if cap.startswith("mcp:") else "read")
        self.operations.setVisible(not is_library and not cap.startswith("mcp:"))
        self.read_permission.setVisible(is_library)
        self.read_permission.setText("允许浏览和读取服务中的资料" if cap.startswith("library:") else "允许读取本次任务选定的资料")
        self.write_permission.setVisible(cap.startswith("library:"))
        self.read_permission.setChecked(True)
        self.write_permission.setChecked(False)
        self.permission_hint.setText("允许读取工具列表并调用工具；每次执行仍需满足现有操作权限。" if cap.startswith("mcp:") else
                                     "按需允许读取或保存成果。本次任务仍只能使用已选定的资料。" if is_library else
                                     "填写能力声明中的稳定 ID 和操作；新能力必须单独授权。")
        self.with_skill.setVisible(cap.startswith("library:"))
        self.adopt.setVisible(is_library)
        self.resources.setVisible(is_library)
        self.requirement_id.setVisible(not is_library and not cap.startswith("mcp:"))
        self.adopt.setChecked(False)
        if self.current_id:
            source = self.selected()["template"]["service"]
            requirement = source if is_library else "default" if cap.startswith("mcp:") else self.requirement_id.text().strip()
            selected = self.broker.store.selection(cap, requirement)
            self.mode_label.setText("此功能正在使用本账号。" if selected == self.current_id else
                                    "此功能正在使用其他账号。确认后仅新任务切换。" if selected else
                                    "确认后，此功能的新任务使用本账号。")

    def run(self, work, done=None):
        self.busy = True
        self.list.setEnabled(False)
        for button in (self.login_button, self.verify_button, self.use_button, self.add_button, self.legacy_button):
            button.setEnabled(False)
        def finish(value=None, error=None):
            self.busy = False
            self.list.setEnabled(True)
            self.cancel_button.hide()
            self.confirm_button.hide()
            self.cancel_event = None
            self.confirm_event = None
            if error:
                self.notice.setText(str(error) if isinstance(error, ConnectionError) else "操作未完成，输入已保留。请检查连接配置后重试。")
                # Keep sensitive form inputs in the existing UI on failure; never serialize them.
                for button in (self.login_button, self.verify_button, self.use_button, self.add_button, self.legacy_button):
                    button.setEnabled(True)
            else:
                try:
                    if done:
                        done(value)
                except Exception as exc:
                    self.notice.setText(str(exc) if isinstance(exc, ConnectionError) else "保存失败，原有模式已保留，请检查配置后重试。")
                    for button in (self.login_button, self.verify_button, self.use_button, self.add_button, self.legacy_button):
                        button.setEnabled(True)
                    return
                self.refresh()
        self.jobs.submit(work, lambda value: finish(value), lambda error: finish(error=error))

    def add(self):
        if self.busy or not self.confirm_leave():
            return
        try:
            item = self.broker.create(self.templates.currentData())
            self.current_id = item["id"]
            self.other_account_button.setChecked(False)
            self.notice.setText("连接已创建，请完成登录。")
            self.refresh()
            if self.fields:
                next(iter(self.fields.values())).setFocus()
        except ConnectionError as exc:
            self.notice.setText(str(exc))

    def login(self):
        if self.busy or not self.current_id:
            return
        if not self.form.isVisible() and self.fields:
            self.form.show()
            self.login_button.setText("使用原账号重新认证")
            return
        inputs = {key: field.text() for key, field in self.fields.items()}
        if any(not value.strip() for value in inputs.values()):
            self.notice.setText("请填写登录信息后继续。")
            next(field for field in self.fields.values() if not field.text().strip()).setFocus()
            return
        connection_id = self.current_id
        event = threading.Event()
        self.cancel_event = event
        self.confirm_button.setEnabled(True)
        self.destroyed.connect(lambda _=None, pending=event: pending.set())
        self.notice.setText("正在认证，请完成服务要求的登录步骤。")
        self.run(lambda: self.broker.authenticate(connection_id, inputs, cancelled=event.is_set, progress=self.progress.emit),
                 lambda value: self.notice.setText("登录完成。请选择允许使用此连接的能力。" if value["state"] == "ready" else "凭据已保存。此模板没有验证接口，请配置验证地址后验证，或通过明确适配的服务接入。"))
        self.cancel_button.show()

    def show_progress(self, progress):
        if not self.busy or (self.cancel_event and self.cancel_event.is_set()):
            return
        self.notice.setText("请在系统浏览器中完成登录。" + (" 验证码：" + progress["user_code"] if progress.get("user_code") else ""))
        if progress.get("url"):
            QDesktopServices.openUrl(QUrl(progress["url"]))
        if progress.get("confirm"):
            self.confirm_event = progress["confirm"]
            self.confirm_button.show()

    def confirm_login(self):
        if self.confirm_event:
            self.confirm_event.set()
            self.confirm_button.setEnabled(False)

    def cancel_login(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.notice.setText("正在取消认证，已完成的任务内容已保留。")

    def _cancel_on_destroy(self, *_):
        if self.cancel_event:
            self.cancel_event.set()

    def verify(self):
        connection_id = self.current_id
        self.run(lambda: self.broker.verify(connection_id), lambda item: self.notice.setText(STATES[item["state"]]))

    def impact(self, item):
        caps = "、".join(g["capability"] for g in self.broker.store.grants(item["id"])) or "暂无已授权能力"
        tasks = [w for w in self.broker.store.waits() if w["connection_id"] == item["id"] and w["status"] == "waiting"]
        return f"{item['name']}\n影响能力：{caps}\n等待认证的任务：{len(tasks)}\n后续访问将被阻止，已完成内容保留。"

    def disconnect(self):
        item = self.selected()
        if QMessageBox.question(self, "断开连接", self.impact(item)) == QMessageBox.Yes:
            self.broker.disconnect(item["id"])
            self.refresh()

    def logout_account(self):
        item = self.selected()
        session = item.get("login_session_id")
        if not session:
            return
        related = [c for c in self.broker.store.list() if c.get("login_session_id") == session]
        text = "退出将断开以下本机连接：\n" + "\n".join(self.impact(c) for c in related)
        if QMessageBox.question(self, "退出关联账号", text) == QMessageBox.Yes:
            self.cancel_login()
            self.broker.store.logout_session(session)
            self.notice.setText("关联的本机连接已断开；其他客户端的登录状态不受此操作控制。")
            self.refresh()

    def delete(self):
        item = self.selected()
        if QMessageBox.question(self, "删除连接", self.impact(item) + "\n本机凭据将被删除；原有配置不会删除。") == QMessageBox.Yes:
            self.cancel_login()
            self.broker.store.delete(item["id"])
            self.current_id = None
            self.refresh()

    def use_connection(self):
        item, cap = self.selected(), self.capability_id()
        operations = [s.strip() for s in self.operations.text().split(",") if s.strip()]
        if cap.startswith("library:") or cap == "knowledge-library":
            operations = (["read"] if self.read_permission.isChecked() else []) + (["write"] if not self.write_permission.isHidden() and self.write_permission.isChecked() else [])
        resources = [s.strip() for s in self.resources.text().split(",") if s.strip()] if not self.resources.isHidden() else []
        source = item["template"]["service"]
        if (not cap or not operations or ((cap.startswith("library:") or cap == "knowledge-library") and
                (item["template"]["adapter"] != {"weknora": "weknora", "tencent-docs": "tencent_docs", "lexiang": "lexiang"}.get(source)
                 or cap.startswith("library:") and cap != "library:" + source))):
            self.notice.setText("请选择与此服务匹配的功能，并至少允许一项操作。")
            return
        requirement = source if cap.startswith("library:") or cap == "knowledge-library" else ("default" if cap.startswith("mcp:") else self.requirement_id.text().strip())
        with_skill, adopt = not self.with_skill.isHidden() and self.with_skill.isChecked(), not self.adopt.isHidden() and self.adopt.isChecked()
        def work():
            verified = self.broker.verify(item["id"])
            if verified["state"] != "ready":
                raise ConnectionError("not_connected")
            if adopt:
                from core.connections.knowledge import adopt_references
                from core.knowledge_library import KnowledgeStore
                adopt_references(self.broker, KnowledgeStore(self.broker.store.data_dir), source, item["id"])
            # Browsing remains within the service's permissions. Optional ID limits
            # apply to the separately listed conversational capability.
            usages = [(cap, requirement, operations, [] if cap.startswith("library:") else resources)]
            if with_skill:
                usages.append(("knowledge-library", requirement, ["read"], resources))
            return usages
        def done(_):
            if cap == "mcp:superset-mcp" and self.config_manager:
                servers = self.config_manager.get_mcp_servers()
                if not any(s["id"] == "superset-mcp" for s in servers):
                    self.config_manager.upsert_mcp_servers([{"id": "superset-mcp", "name": "Superset MCP", "enabled": True,
                        "transport": "streamable-http", "url": item["template"]["parameters"].get("mcp_url") or item["template"]["base_url"] + "/mcp"}])
            self.broker.store.activate(item["id"], _)
            self.notice.setText("授权已保存，此能力的新任务将使用所选连接。已有任务保留原连接。")
        self.run(work, done)

    def use_legacy(self):
        cap = self.capability_id()
        source = self.selected()["template"]["service"]
        requirement = source if cap.startswith("library:") or cap == "knowledge-library" else ("default" if cap.startswith("mcp:") else self.requirement_id.text().strip())
        self.broker.store.select(cap, requirement, None)
        self.notice.setText("新任务已切回原有配置；运行中的任务继续使用原绑定。")
        self.capability_changed()

    def revoke(self):
        self.broker.revoke(self.current_id, self.capability_id())
        self.notice.setText("所选能力的连接授权已撤销。")
        self.refresh()

    def refresh_waits(self):
        if not self.isVisible():
            return
        waits = [w for w in self.broker.store.waits() if w["status"] == "waiting"]
        self.waits_label.setVisible(bool(waits))
        self.waits_label.setText("等待认证：" + ("；".join(w["task_id"] + " · " + w["operation"] for w in waits) if waits else "无") +
                               ("\n认证完成后，请回到对应任务选择继续。重新打开历史不会自动执行操作。" if waits else ""))

    def import_templates(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入连接模板", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.broker.catalog.import_file(path)
        except ConnectionError as exc:
            if exc.code == "template_conflict" and QMessageBox.question(self, "模板已存在", "仅接受更高版本；更新模板不会更换现有连接的身份或地址。是否更新本机模板？") == QMessageBox.Yes:
                try:
                    self.broker.catalog.import_file(path, replace=True)
                except ConnectionError as error:
                    self.notice.setText(str(error))
                    return
            else:
                self.notice.setText(str(exc))
                return
        self.notice.setText("模板已导入。现有连接保留其原模板快照；使用更新的目标时请添加新连接。")
        self.refresh()

    def export_templates(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出登录配置（不含凭据）", "connection_templates.json", "JSON (*.json)")
        if path:
            try:
                items = [{k: v for k, v in t.items() if k != "origin"} for t in self.broker.catalog.load()[0]]
                self.broker.catalog.write(path, items)
                self.notice.setText("模板已导出，可与程序一起分发；未导出账号凭据和授权。")
            except (OSError, ConnectionError):
                self.notice.setText("导出失败，请检查目标目录权限。")

    def remove_template(self):
        template_id = self.templates.currentData()
        if template_id and QMessageBox.question(self, "移除本机模板", "仅移除本机模板；已有连接保留原配置，分发方模板会重新显示。") == QMessageBox.Yes:
            try:
                self.broker.catalog.remove_local(template_id)
                self.refresh()
            except (OSError, ConnectionError):
                self.notice.setText("移除失败，请检查模板文件。")

    def edit_template(self):
        self.template_tools_button.setChecked(True)
        if not self.template_editor.isHidden():
            self.template_editor.hide()
            self.save_template_button.hide()
            return
        if self.template_editor.toPlainText():
            self.template_editor.show()
            self.save_template_button.show()
            return
        template_id = self.templates.currentData()
        if template_id:
            item = self.broker.catalog.get(template_id)
            if item["locked"]:
                self.notice.setText("此模板由管理员管理；可复制为新的模板 ID。")
                item.update(id=item["id"] + "-local", locked=False, version=1)
            else:
                item["version"] += 1
        else:
            item = {"id": "my-service", "version": 1, "name": "我的服务", "service": "my-service",
                    "adapter": "api_key", "base_url": "https://service.example.com",
                    "parameters": {"header": "Authorization", "prefix": "Bearer ", "verify_url": "https://service.example.com/me"},
                    "suggested_capabilities": []}
        self.template_editor.setPlainText(json.dumps(item, ensure_ascii=False, indent=2))
        self.template_editor.show()
        self.save_template_button.show()

    def save_template(self):
        try:
            item = json.loads(self.template_editor.toPlainText())
            current = {t["id"] for t in self.broker.catalog.load()[0]}
            replace = item.get("id") in current
            if replace and QMessageBox.question(self, "更新本机模板", "现有连接保留原模板快照。使用新目标时请添加新连接。是否保存此新版本？") != QMessageBox.Yes:
                return
            self.broker.catalog.import_templates([item], replace=replace)
            self.template_editor.clear()
            self.template_editor.hide()
            self.save_template_button.hide()
            self.notice.setText("模板已保存；可以添加连接并登录。")
            self.refresh()
        except (ValueError, TypeError, AttributeError, OSError, ConnectionError):
            self.notice.setText("模板未保存，输入已保留。请检查 ID、递增版本、受支持参数，并移除所有凭据字段。")

    def has_pending_input(self):
        return self.busy or any(field.text() for field in self.fields.values()) or bool(self.template_editor.toPlainText())

    def confirm_leave(self):
        if not self.has_pending_input():
            return True
        choice = ProductMessageDialog("连接操作尚未完成", "离开将取消本次认证并清空尚未提交的登录输入；已保存的连接保留。", "confirm",
            [("离开并清空输入", "discard", "secondary", False), ("继续配置", "stay", "primary", True)], parent=self).exec_result("stay")
        if choice != "discard":
            return False
        self.cancel_login()
        for field in self.fields.values():
            field.clear()
        self.template_editor.clear()
        return True
