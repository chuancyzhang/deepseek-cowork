"""Service-first model settings with a single settings transaction."""
from __future__ import annotations

import copy
import logging
import qtawesome as qta

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QListWidget, QListWidgetItem, QSizePolicy, QMenu, QComboBox)

from core.theme import DesignTokens, bind_theme
from ui.model_configuration import ModelPageStack, ModelFieldCombo, model_field_style, apply_model_combo_style
from ui.gateway_setup import GatewaySetup
from ui.primitives import product_button_style, ProductMessageDialog, ProductMessageBox as QMessageBox

log = logging.getLogger(__name__)


class ModelServicesPage(QWidget):
    changed = Signal()
    useModelRequested = Signal(str)

    def __init__(self, channels, editor_factory, parent=None, selected_model_id=""):
        super().__init__(parent)
        self.editor_factory = editor_factory
        self.editors = []
        self.last_edited_model_id = self.saved_model_id = self.origin_session_id = ""
        self._default_model_id = selected_model_id
        self._active_service = ""
        self.setup = None
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(20)
        self.layout.setAlignment(Qt.AlignTop)
        self.route_stack = ModelPageStack(self)
        self.layout.addWidget(self.route_stack)
        self.overview = QWidget(self.route_stack)
        overview = QVBoxLayout(self.overview)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(16)
        toolbar = QHBoxLayout()
        self.heading = QLabel("已连接的服务", self.overview)
        toolbar.addWidget(self.heading, 1)
        self.add_channel_btn = QPushButton("添加服务", self.overview)
        self.add_channel_btn.clicked.connect(self.add_channel)
        toolbar.addWidget(self.add_channel_btn)
        overview.addLayout(toolbar)
        self.empty_label = QLabel("连接一个服务，选择要使用的模型。", self.overview)
        self.empty_label.setWordWrap(True)
        overview.addWidget(self.empty_label)
        self.service_list = QListWidget(self.overview)
        self.service_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.service_list.itemActivated.connect(self._open_service_item)
        self.service_list.itemClicked.connect(self._open_service_item)
        overview.addWidget(self.service_list)
        self.route_stack.addWidget(self.overview)

        self.detail = QWidget(self.route_stack)
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(12)
        detail.setAlignment(Qt.AlignTop)
        header = QHBoxLayout()
        self.back_btn = QPushButton("", self.detail)
        self.back_btn.setFixedWidth(36)
        self.back_btn.clicked.connect(self._back_from_detail)
        header.addWidget(self.back_btn)
        self.detail_title = QLabel(self.detail)
        self.detail_title.setTextFormat(Qt.PlainText)
        self.detail_title.setWordWrap(True)
        header.addWidget(self.detail_title, 1)
        self.more_btn = QPushButton("更多", self.detail)
        self.more_menu = QMenu(self.more_btn)
        self.connection_action = self.more_menu.addAction("编辑连接")
        self.connection_action.setCheckable(True)
        self.connection_action.triggered.connect(self._toggle_connection)
        self.manage_keys_action = self.more_menu.addAction("管理接入密钥", self.manage_keys)
        self.more_menu.addSeparator()
        self.more_menu.addAction("删除此服务", self.delete_active_service)
        self.more_btn.setMenu(self.more_menu)
        header.addWidget(self.more_btn)
        detail.addLayout(header)
        self.address_label = QLabel(self.detail)
        self.address_label.setWordWrap(True)
        self.address_label.setTextFormat(Qt.PlainText)
        self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.addWidget(self.address_label)
        self.key_filter = QWidget(self.detail)
        filter_layout = QVBoxLayout(self.key_filter)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.addWidget(QLabel("密钥 / 分组", self.key_filter))
        self.channel_combo = ModelFieldCombo(self.key_filter)
        self.channel_combo.setAccessibleName("密钥和分组")
        self.channel_combo.currentIndexChanged.connect(self._filter_key)
        filter_layout.addWidget(self.channel_combo)
        detail.addWidget(self.key_filter)
        self.channel_detail_stack = ModelPageStack(self.detail)
        self.channel_detail_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        detail.addWidget(self.channel_detail_stack)
        self.route_stack.addWidget(self.detail)
        # Retain the existing editor selection API for restore and conversation entry.
        self.channel_list = QListWidget(self)
        self.channel_list.hide()
        self.channel_list.currentRowChanged.connect(self._on_channel_selection_changed)
        self.service_navigation_host = self.overview

        self.default_model_row = QWidget(self)
        self.default_model_layout = QGridLayout(self.default_model_row)
        self.default_model_layout.setContentsMargins(0, 12, 0, 0)
        self.default_model_layout.setSpacing(8)
        self.default_model_label = QLabel("新对话默认模型", self.default_model_row)
        self.default_model_hint = QLabel("仅用于新建对话；已有对话保留自己的模型。", self.default_model_row)
        self.default_model_hint.setWordWrap(True)
        self.default_model_combo = ModelFieldCombo(self.default_model_row)
        self.default_model_combo.setMinimumWidth(0)
        self.default_model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.default_model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.default_model_layout.addWidget(self.default_model_label, 0, 0)
        self.default_model_layout.addWidget(self.default_model_hint, 1, 0)
        self.default_model_layout.addWidget(self.default_model_combo, 0, 1, 2, 1)
        self.layout.addWidget(self.default_model_row)
        self.saved_notice = QWidget(self)
        saved = QHBoxLayout(self.saved_notice)
        saved.setContentsMargins(0, 0, 0, 0)
        self.saved_label = QLabel("模型已保存，可以返回对话使用。", self.saved_notice)
        self.saved_label.setWordWrap(True)
        self.use_model_btn = QPushButton("返回对话并使用", self.saved_notice)
        self.use_model_btn.clicked.connect(lambda: self.useModelRequested.emit(self.saved_model_id))
        saved.addWidget(self.saved_label, 1)
        saved.addWidget(self.use_model_btn)
        self.layout.addWidget(self.saved_notice)
        self.saved_notice.hide()
        for channel in channels or []:
            self._add_editor(channel)
        self._refresh_channel_choices()
        self._refresh_default_models()
        self.default_model_combo.currentIndexChanged.connect(self._on_default_model_changed)
        self.changed.connect(self._refresh_default_models)
        self.changed.connect(self._refresh_channel_choices)
        self.changed.connect(self.saved_notice.hide)
        self.show_overview()
        bind_theme(self, self.refresh_theme, surface="settings")

    def _service_id(self, editor):
        source = editor.gateway_source()
        return source.get("service_id") or editor.channel_config["channel_id"]

    def _service_editors(self):
        return [editor for editor in self.editors if self._service_id(editor) == self._active_service]

    def _refresh_channel_choices(self):
        current = self.channel_detail_stack.currentWidget()
        if current and self.route_stack.currentWidget() is self.detail:
            self._active_service = self._service_id(current)
        self.service_list.clear()
        grouped = {}
        for editor in self.editors:
            grouped.setdefault(self._service_id(editor), []).append(editor)
        for service_id, editors in grouped.items():
            first = editors[0]
            count = sum(len(editor._models()) for editor in editors)
            source = first.gateway_source()
            address = source.get("site_url") or first.base_url_input.text().strip()
            keys = len({editor.gateway_source().get("key_id", "manual") for editor in editors})
            status = "待填写密钥" if any(not editor._has_api_key() for editor in editors) else "待添加模型" if not count else ""
            item = QListWidgetItem(f"{first._channel_name()}    {count} 个模型" + (f" · {keys} 个密钥" if source else "") +
                                   f"\n{address}" + (f"\n{status}" if status else ""))
            item.setData(Qt.UserRole, service_id)
            item.setToolTip(f"{first._channel_name()}\n{address}\n点击管理模型与连接")
            item.setSizeHint(QSize(0, 96 if status else 78))
            self.service_list.addItem(item)
        self.service_list.setFixedHeight(max(80, min(6, len(grouped)) * 96 + 4))
        self.service_list.setVisible(bool(grouped))
        self.empty_label.setVisible(not grouped)
        self._refresh_key_filter()

    def _refresh_key_filter(self):
        selected = self.channel_detail_stack.currentWidget()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        editors = self._service_editors()
        for editor in editors:
            source = editor.gateway_source()
            label = " · ".join(filter(None, (source.get("key_name"), source.get("group_name"),
                                             "Anthropic" if editor._provider_type() == "anthropic" else "OpenAI 兼容")))
            self.channel_combo.addItem(label or editor._channel_name(), self.editors.index(editor))
        self.channel_combo.setCurrentIndex(editors.index(selected) if selected in editors else 0)
        self.channel_combo.blockSignals(False)
        if editors:
            first = editors[0]
            source = first.gateway_source()
            self.detail_title.setText(first._channel_name())
            self.address_label.setText(source.get("site_url") or first.base_url_input.text())
            self.manage_keys_action.setVisible(bool(source))
        self._sync_editor_route()

    def _back_from_detail(self):
        editor = self.channel_detail_stack.currentWidget()
        if editor and editor._active_form_id:
            editor.show_model_list()
        else:
            self.show_overview()

    def _toggle_connection(self, checked):
        editor = self.channel_detail_stack.currentWidget()
        if editor:
            editor.connection_button.setChecked(checked)
            self._sync_editor_route()

    def _open_service_item(self, item):
        self.open_service(item.data(Qt.UserRole))

    def open_service(self, service_id):
        self._active_service = service_id
        editor = next((item for item in self.editors if self._service_id(item) == service_id), None)
        if editor:
            index = self.editors.index(editor)
            self.channel_list.setCurrentRow(index)
            self._on_channel_selection_changed(index)

    def _filter_key(self, _index):
        index = self.channel_combo.currentData()
        if isinstance(index, int):
            self.channel_list.setCurrentRow(index)

    def _on_channel_selection_changed(self, index):
        if not 0 <= index < len(self.editors):
            return
        editor = self.editors[index]
        self._active_service = self._service_id(editor)
        self.channel_detail_stack.setCurrentWidget(editor)
        self.route_stack.setCurrentWidget(self.detail)
        self._refresh_key_filter()
        self._sync_editor_route()

    def _sync_editor_route(self, *_args):
        editor = self.channel_detail_stack.currentWidget()
        editing = bool(editor and editor._active_form_id and self.route_stack.currentWidget() is self.detail)
        self.back_btn.setToolTip("返回模型列表" if editing else "返回服务列表")
        self.back_btn.setAccessibleName(self.back_btn.toolTip())
        self.more_btn.setVisible(not editing)
        self.key_filter.setVisible(not editing and len(self._service_editors()) > 1)
        if editor:
            source = editor.gateway_source()
            self.detail_title.setText(editor.form_title.text() if editing else editor._channel_name())
            self.address_label.setText(" · ".join(filter(None, (editor._channel_name(), source.get("key_name"), source.get("group_name"))))
                                       if editing else source.get("site_url") or editor.base_url_input.text())
            self.connection_action.setChecked(editor.connection_button.isChecked())
            editor._sync_connection_visibility()
        self.default_model_row.setVisible(not editing and self.route_stack.currentWidget() is not self.setup)
        for item in self.editors:
            item.body.layout().setContentsMargins(0, 0, 0, 0)
            item.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred if item is editor else QSizePolicy.Ignored)
        self.channel_detail_stack.updateGeometry()
        self.route_stack.updateGeometry()

    def show_overview(self):
        self.route_stack.setCurrentWidget(self.overview)
        self._sync_editor_route()

    def _add_editor(self, channel, expanded=False):
        editor = self.editor_factory(channel, self.channel_detail_stack, expanded=True,
                                     on_expand=self._collapse_others, on_delete=self.delete_channel)
        self.editors.append(editor)
        self.channel_detail_stack.addWidget(editor)
        editor.set_management_detail_mode()
        editor.service_summary.hide()
        self.channel_list.addItem(QListWidgetItem(channel.get("display_name", "未命名服务")))
        editor.changed.connect(self.changed.emit)
        editor.modelEdited.connect(self._model_edited)
        editor.formRouteChanged.connect(self._sync_editor_route)
        if hasattr(editor, "channelsDiscovered"):
            editor.channelsDiscovered.connect(self.accept_channels)
        return editor

    def add_channel(self):
        self._open_setup()

    def _open_setup(self, source=None, name=""):
        if self.setup:
            self.route_stack.setCurrentWidget(self.setup)
            return
        existing = {(editor.gateway_source().get("key_id", ""), model["model_name"])
                    for editor in self.editors if source and self._service_id(editor) == source.get("service_id") for model in editor._models()}
        self.setup = GatewaySetup(self.route_stack, existing=existing, source=source, name=name)
        self.route_stack.addWidget(self.setup)
        self.setup.channelsReady.connect(self.accept_channels)
        self.setup.cancelled.connect(self.cancel_setup)
        self.setup.changed.connect(self.changed.emit)
        self.route_stack.setCurrentWidget(self.setup)
        self._sync_editor_route()

    def manage_keys(self):
        editors = self._service_editors()
        if editors:
            self._open_setup(editors[0].gateway_source(), editors[0]._channel_name())

    def cancel_setup(self):
        if self.setup and self.setup.has_pending_input():
            message = "返回后将放弃本次接入输入，已有服务配置保留。"
            if self.setup.account and (self.setup.account.created_keys or self.setup.account.pending_creation
                                       or (self.setup.worker and self.setup.worker.operation == "create")):
                message += "远端已创建或待确认的密钥不会删除，可前往站点管理。"
            if ProductMessageDialog("放弃本次接入", message, "confirm",
                                    [("放弃接入", "discard", "secondary", False),
                                     ("继续接入", "stay", "primary", True)], parent=self).exec_result("stay") != "discard":
                return
        self.discard_setup()

    def discard_setup(self):
        if self.setup:
            setup, self.setup = self.setup, None
            setup.dispose()
            self.route_stack.removeWidget(setup)
            setup.hide()
            setup.deleteLater()
        self.show_overview()
        self.changed.emit()

    def confirm_leave(self):
        if self.setup and self.setup.has_pending_input():
            self.cancel_setup()
            return self.setup is None
        self.discard_setup()
        return True

    def accept_channels(self, channels):
        active = None
        for channel in channels:
            editor = next((item for item in self.editors if item.channel_config["channel_id"] == channel["channel_id"]), None)
            if editor:
                # Keep manual overrides and existing identities; update evidence only.
                known = {item["model_name"]: item for item in editor._models()}
                for model in channel["models"]:
                    if model["model_name"] not in known:
                        editor._models().append(copy.deepcopy(model))
                    elif "discovery" in model:
                        previous = known[model["model_name"]]
                        manual = previous.get("discovery", {}).get("manual_fields", [])
                        previous["discovery"] = copy.deepcopy(model["discovery"])
                        previous["discovery"]["manual_fields"] = manual
                editor.channel_config["api_key"] = channel["api_key"]
                editor.channel_config["gateway_source"] = copy.deepcopy(channel.get("gateway_source", {}))
                editor.api_key_input.setText(channel["api_key"])
                editor.refresh_model_list()
            else:
                editor = self._add_editor(channel)
            active = self._service_id(editor)
            if channel["models"]:
                last_name = channel["models"][-1]["model_name"]
                self.last_edited_model_id = next(model["id"] for model in editor._models() if model["model_name"] == last_name)
        self.discard_setup()
        self.changed.emit()
        if active:
            self.open_service(active)
        log.info("gateway.import.draft channels=%s", len(channels))

    def open_new_model(self, channel_id=""):
        if not self.editors:
            # Conversation entry opens the same connection route before editing.
            self.add_channel()
            return None
        editor = next((item for item in self.editors if item.channel_config["channel_id"] == channel_id), None)
        editor = editor or self.channel_detail_stack.currentWidget() or self.editors[0]
        index = self.editors.index(editor)
        self.channel_list.setCurrentRow(index)
        self._on_channel_selection_changed(index)
        editor.add_model()
        return editor

    def validate_models(self):
        if self.setup and self.setup.has_pending_input():
            self.route_stack.setCurrentWidget(self.setup)
            self.setup.status.setText("请先完成模型选择并点击“添加所选模型”，或返回放弃本次接入。")
            return False
        for index, editor in enumerate(self.editors):
            if not editor.validate_model_forms():
                self._on_channel_selection_changed(index)
                return False
        return True

    def _model_edited(self, model_id):
        self.last_edited_model_id = model_id

    def mark_saved(self):
        ids = {model["id"] for editor in self.editors for model in editor._models()}
        self.saved_model_id = self.last_edited_model_id if self.last_edited_model_id in ids else ""
        for editor in self.editors:
            editor.mark_models_saved()
        self._sync_editor_route()
        self.saved_notice.setVisible(bool(self.saved_model_id and self.origin_session_id))

    def _refresh_default_models(self):
        combo = self.default_model_combo
        combo.blockSignals(True)
        combo.clear()
        for channel in self.get_channels():
            source = channel.get("gateway_source", {})
            for model in channel["models"]:
                label = " / ".join(filter(None, (channel["display_name"], source.get("key_name"), model.get("display_name") or model["model_name"])))
                combo.addItem(label, model["id"])
        index = combo.findData(self._default_model_id)
        combo.setPlaceholderText("请选择默认模型" if combo.count() else "尚未配置模型")
        combo.setCurrentIndex(index)
        combo.setEnabled(combo.count() > 0)
        combo.setToolTip(combo.currentText())
        self.default_model_hint.setText("默认模型未选择或已删除，请重新选择后保存。仅用于新建对话。" if combo.count() and index < 0 else "仅用于新建对话；已有对话保留自己的模型。")
        combo.blockSignals(False)
        for editor in self.editors:
            editor._default_model_id = self._default_model_id
            for model_id, row in editor._model_rows.items():
                row.default_badge.setVisible(model_id == self._default_model_id)

    def _on_default_model_changed(self, _index):
        self._default_model_id = self.default_model_combo.currentData() or ""
        self.changed.emit()

    def get_default_model_id(self):
        return self.default_model_combo.currentData() or ""

    def get_channels(self):
        return [editor.get_channel_config() for editor in self.editors]

    def _collapse_others(self, editor):
        self._on_channel_selection_changed(self.editors.index(editor))

    def delete_channel(self, editor):
        self._active_service = self._service_id(editor)
        self.delete_active_service()

    def delete_active_service(self):
        editors = self._service_editors()
        if any(worker and worker.isRunning() for editor in editors for worker in (editor.fetch_worker, editor.test_worker)):
            QMessageBox.information(self, "服务正在使用", "请等待模型获取或测试结束后再删除。")
            return
        if QMessageBox.question(self, "删除服务", "从本地移除此服务及其模型，保存后生效。站点上的密钥不会删除。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.channel_list.blockSignals(True)
        for editor in editors:
            index = self.editors.index(editor)
            self.editors.remove(editor)
            self.channel_list.takeItem(index)
            self.channel_detail_stack.removeWidget(editor)
            editor.hide()
            editor.deleteLater()
        self.channel_list.blockSignals(False)
        self.show_overview()
        self.changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "default_model_row"):
            return
        narrow = self.width() < 620
        self.default_model_layout.addWidget(self.default_model_combo, 2 if narrow else 0, 0 if narrow else 1, 1 if narrow else 2, 1)
        self.default_model_combo.setMaximumWidth(16777215 if narrow else 380)
        self._sync_editor_route()

    def refresh_theme(self, _resolved=None):
        for label in (self.heading, self.detail_title):
            label.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_section}px; font-weight: 600; }}")
        for label in (self.empty_label, self.address_label, self.default_model_hint, self.saved_label):
            label.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_secondary}; }}")
        for button in (self.back_btn, self.more_btn):
            button.setStyleSheet(product_button_style("ghost"))
        self.back_btn.setIcon(qta.icon("fa5s.chevron-left", color=DesignTokens.text_secondary))
        self.back_btn.setIconSize(QSize(14, 14))
        self.use_model_btn.setStyleSheet(product_button_style("secondary"))
        self.add_channel_btn.setStyleSheet(product_button_style("primary"))
        for combo in (self.channel_combo, self.default_model_combo):
            combo.setStyleSheet(model_field_style())
            apply_model_combo_style(combo, popup_selection=DesignTokens.primary_soft)
        self.service_list.setStyleSheet(f"QListWidget {{ background: {DesignTokens.bg_main}; color: {DesignTokens.text_primary}; border: none; }} QListWidget::item {{ padding: 12px 8px; border-bottom: 1px solid {DesignTokens.separator}; }} QListWidget::item:hover {{ background: {DesignTokens.bg_hover}; }} QListWidget::item:selected {{ background: {DesignTokens.bg_secondary}; color: {DesignTokens.text_primary}; }}")
