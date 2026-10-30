"""Model capabilities edited in the settings page, with one outer save boundary."""

import copy
import json
import qtawesome as qta

from PySide6.QtCore import Qt, Signal, QSize, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QCheckBox, QPushButton, QVBoxLayout,
    QGridLayout, QHBoxLayout, QSizePolicy, QStackedWidget, QFrame,
    QStyle, QStyleOptionButton, QStylePainter,
)

from bootstrap_plugins.deepseek_flash_minimal import DeepSeekFlashMinimalBootstrap
from core.llm.deepseek import (
    DEFAULT_DEEPSEEK_REASONING_EFFORT, SUPPORTED_REASONING_EFFORTS,
    normalize_reasoning_effort, normalize_reasoning_efforts,
)
from core.llm.model_catalog import build_new_model_defaults, is_deepseek_official_base_url
from core.llm.providers import (
    API_PROTOCOL_CHAT_COMPLETIONS, API_PROTOCOL_RESPONSES,
    is_gpt_5_6_model, normalize_openai_api_protocol,
)
from core.theme import DesignTokens, bind_theme
from ui.primitives import PageScrollSafeComboBox, product_button_style, product_field_style
from ui.workspace_presentation import ComposerFlowLayout


def apply_model_combo_style(combo, *, popup_selection=None):
    """Keep settings combo popup items legible across active and inactive states."""
    view = combo.view()
    view.setStyleSheet(
        f"""
        QAbstractItemView {{
            background: {DesignTokens.bg_panel_strong};
            color: {DesignTokens.text_primary};
            border: 1px solid {DesignTokens.border_subtle};
            outline: none;
            padding: 4px;
            selection-background-color: {popup_selection or DesignTokens.selection_bg};
            selection-color: {DesignTokens.selection_text};
        }}
        QAbstractItemView::item {{
            color: {DesignTokens.text_primary};
            min-height: 28px;
            padding: 4px 8px;
            border: none;
            border-radius: 8px;
        }}
        QAbstractItemView::item:hover {{
            background: {DesignTokens.bg_hover};
            color: {DesignTokens.text_primary};
        }}
        QAbstractItemView::item:selected,
        QAbstractItemView::item:selected:active,
        QAbstractItemView::item:selected:!active,
        QAbstractItemView::item:selected:!focus {{
            background: {DesignTokens.primary_soft};
            color: {DesignTokens.text_primary};
        }}
        """
    )
    palette = view.palette()
    palette.setColor(QPalette.Base, QColor(DesignTokens.bg_panel_strong))
    palette.setColor(QPalette.Text, QColor(DesignTokens.text_primary))
    palette.setColor(QPalette.Highlight, QColor(DesignTokens.primary_soft))
    palette.setColor(QPalette.HighlightedText, QColor(DesignTokens.text_primary))
    view.setPalette(palette)
    return combo


def model_field_style():
    return (product_field_style() +
            f"QComboBox {{ padding-right: 28px; selection-background-color: {DesignTokens.primary_soft}; selection-color: {DesignTokens.text_primary}; }}"
            "QComboBox::drop-down { border: none; width: 26px; background: transparent; }"
            "QComboBox::down-arrow { image: none; }")


class ModelFieldCombo(PageScrollSafeComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(DesignTokens.text_secondary if self.isEnabled() else DesignTokens.text_disabled), 1.5))
        x, y = self.width() - 17, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 3, y - 1))


class ModelPageStack(QStackedWidget):
    """Hidden service/model drafts must not enlarge the visible page."""

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current else QSize(0, 0)

    def minimumSizeHint(self):
        current = self.currentWidget()
        return QSize(0, current.minimumSizeHint().height()) if current else QSize(0, 0)

    def heightForWidth(self, width):
        current = self.currentWidget()
        if not current:
            return 0
        height = current.heightForWidth(width)
        return height if height >= 0 else current.sizeHint().height()


class _ModelNameButton(QPushButton):
    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.width() - 4))
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButton, option)


class _ModelDetailsLabel(QLabel):
    def __init__(self, text, parent):
        super().__init__(text, parent)
        self.full_text = text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, self.width()))


class ModelCatalogRow(QWidget):
    """Accessible row actions, with selection reserved for batch operations."""
    selectionChanged = Signal(bool)
    editRequested = Signal()
    testRequested = Signal()

    def __init__(self, title, details, parent=None):
        super().__init__(parent)
        self.setObjectName("ModelCatalogRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 10, 4, 10)
        layout.setSpacing(12)
        self.check = QCheckBox(self)
        self.check.setAccessibleName(f"选择 {title}")
        self.check.toggled.connect(self.selectionChanged)
        layout.addWidget(self.check)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name_button = _ModelNameButton(title, self)
        self.name_button.setToolTip(f"编辑 {title}")
        self.name_button.setCursor(Qt.PointingHandCursor)
        self.name_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.name_button.clicked.connect(self.editRequested)
        self.details = _ModelDetailsLabel(details, self)
        self.details.setToolTip(details)
        self.details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(self.name_button)
        text.addWidget(self.details)
        layout.addLayout(text, 1)
        self.default_badge = QLabel("新对话默认", self)
        self.default_badge.setMaximumHeight(24)
        self.default_badge.hide()
        layout.addWidget(self.default_badge)
        self.edit_button = QPushButton("", self)
        self.edit_button.setFixedWidth(32)
        self.edit_button.setToolTip(f"编辑 {title}")
        self.edit_button.setAccessibleName(f"编辑 {title}")
        self.edit_button.clicked.connect(self.editRequested)
        layout.addWidget(self.edit_button)
        self.test_button = QPushButton("测试", self)
        self.test_button.clicked.connect(self.testRequested)
        layout.addWidget(self.test_button)
        bind_theme(self, self.refresh_theme, surface="settings")

    def set_selected(self, selected):
        self.check.blockSignals(True)
        self.check.setChecked(selected)
        self.check.blockSignals(False)

    def refresh_theme(self, _resolved=None):
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"QWidget#ModelCatalogRow {{ background: {DesignTokens.bg_main}; }}")
        self.name_button.setStyleSheet(product_button_style("ghost") +
            f"QPushButton {{ text-align: left; padding: 0; min-height: 24px; color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_body}px; }}")
        self.details.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px; }}")
        self.details.setText(self.details.fontMetrics().elidedText(self.details.full_text, Qt.ElideRight, self.details.width()))
        self.test_button.setStyleSheet(product_button_style("ghost"))
        self.edit_button.setStyleSheet(product_button_style("ghost"))
        self.edit_button.setIcon(qta.icon("fa5s.chevron-right", color=DesignTokens.text_secondary))
        self.edit_button.setIconSize(QSize(14, 14))
        self.default_badge.setStyleSheet(
            f"QLabel {{ color: {DesignTokens.text_secondary}; background: {DesignTokens.bg_secondary}; border-radius: 4px; padding: 3px 6px; font-size: {DesignTokens.font_size_caption}px; }}")


class ModelEditForm(QWidget):
    changed = Signal()

    def __init__(self, provider_id, model=None, parent=None, base_url=""):
        super().__init__(parent)
        self.setObjectName("ModelEditForm")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.provider_id = provider_id
        self.base_url = str(base_url or "").strip()
        self._source = copy.deepcopy(model or {})
        self._editing_existing = bool(model)
        self._loading = True
        self._protocol_user_selected = False
        self._manual_fields = set(self._source.get("discovery", {}).get("manual_fields", []))
        self._bootstrap_plugin = DeepSeekFlashMinimalBootstrap()
        self._bootstrap_choice = (
            self._source["bootstrap_plugin"] == self._bootstrap_plugin.plugin_id
            if "bootstrap_plugin" in self._source else None
        )
        source = self._source or build_new_model_defaults(provider_id, self.base_url, "")
        self._notes = []
        self._headings = []
        self._separators = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)

        self.info = QWidget(self)
        self.info_grid = QGridLayout(self.info)
        self.info_grid.setContentsMargins(0, 0, 0, 0)
        self.info_grid.setHorizontalSpacing(16)
        self.info_grid.setVerticalSpacing(12)
        self.model_name_input = QLineEdit(self.info)
        self.model_name_input.setPlaceholderText("填写服务提供的模型 ID")
        self.model_name_input.setText(str(source.get("model_name") or ""))
        self.model_field = self._field("模型标识", self.model_name_input, "原样发送给服务。")
        self.display_name_input = QLineEdit(self.info)
        self.display_name_input.setPlaceholderText("留空使用模型标识")
        self.display_name_input.setText(str(source.get("display_name") or ""))
        self.display_field = self._field("显示名称（选填）", self.display_name_input)
        self.api_protocol_combo = ModelFieldCombo(self.info)
        self.api_protocol_combo.addItem("Chat Completions", API_PROTOCOL_CHAT_COMPLETIONS)
        self.api_protocol_combo.addItem("Responses", API_PROTOCOL_RESPONSES)
        self.api_protocol_combo.setCurrentIndex(max(0, self.api_protocol_combo.findData(
            normalize_openai_api_protocol(source.get("api_protocol")))))
        self.protocol_field = self._field("API 协议", self.api_protocol_combo)
        self.anthropic_note = self._note("API 协议：Anthropic Messages", self.info)
        self._layout_info()
        layout.addWidget(self.info)
        self.discovery_warning = self._note("")
        layout.addWidget(self.discovery_warning)
        self.discovery_note = self._note("")
        layout.addWidget(self.discovery_note)
        self.discovery_details_button = QPushButton("▸ 站点声明", self)
        self.discovery_details_button.setCheckable(True)
        layout.insertWidget(layout.indexOf(self.discovery_note), self.discovery_details_button, 0, Qt.AlignLeft)
        self.discovery_details = self._note("")
        self.discovery_details.setTextFormat(Qt.PlainText)
        self.discovery_details.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.discovery_details.hide()
        layout.addWidget(self.discovery_details)
        self.discovery_details_button.toggled.connect(self._toggle_discovery)
        self.update_discovery(source.get("discovery", {}))

        self._heading("模型能力", layout)
        capabilities = QWidget(self)
        capability_layout = ComposerFlowLayout(capabilities)
        capability_layout.setSpacing(16)
        self.vision_check = QCheckBox("图片理解", capabilities)
        self.vision_check.setToolTip("允许向此模型发送图片附件。")
        self.vision_check.setChecked(bool(source.get("supports_vision", False)))
        self.image_generation_check = QCheckBox("图片生成", capabilities)
        self.image_generation_check.setToolTip("仅支持通过 Responses 协议生成图片。")
        self.image_generation_check.setChecked(bool(source.get("supports_image_generation", False)))
        capability_layout.addWidget(self.vision_check)
        capability_layout.addWidget(self.image_generation_check)
        layout.addWidget(capabilities)
        self.image_hint = self._note("图片理解用于图片附件；图片生成需要 Responses 协议。")
        layout.addWidget(self.image_hint)

        configured = normalize_reasoning_efforts(source.get("reasoning_efforts"))
        self.thinking_check = QCheckBox("支持推理强度", self)
        self.thinking_check.setChecked(bool(configured))
        layout.addWidget(self.thinking_check)
        self.reasoning_fields = QWidget(self)
        reasoning_layout = QVBoxLayout(self.reasoning_fields)
        reasoning_layout.setContentsMargins(0, 0, 0, 0)
        reasoning_layout.setSpacing(8)
        reasoning_layout.addWidget(self._note("支持档位 · 选择器只展示勾选的档位", self.reasoning_fields))
        efforts = QWidget(self.reasoning_fields)
        effort_layout = ComposerFlowLayout(efforts)
        effort_layout.setSpacing(12)
        self.reasoning_checks = {}
        for effort in SUPPORTED_REASONING_EFFORTS:
            check = QCheckBox(effort, efforts)
            check.setChecked(effort in configured)
            self.reasoning_checks[effort] = check
            effort_layout.addWidget(check)
        reasoning_layout.addWidget(efforts)
        self.reasoning_combo = ModelFieldCombo(self.reasoning_fields)
        for effort in SUPPORTED_REASONING_EFFORTS:
            self.reasoning_combo.addItem(effort, effort)
        current = normalize_reasoning_effort(
            source.get("reasoning_effort", source.get("deepseek_reasoning_effort")), configured)
        self.reasoning_combo.setCurrentIndex(max(0, self.reasoning_combo.findData(current)))
        reasoning_layout.addWidget(self._field("初始档位", self.reasoning_combo))
        self.effort_preview = self._note("", self.reasoning_fields)
        reasoning_layout.addWidget(self.effort_preview)
        layout.addWidget(self.reasoning_fields)

        self.advanced_button = QPushButton("高级设置", self)
        self._separator(layout)
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button, 0, Qt.AlignLeft)
        self.advanced = QWidget(self)
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(12)
        self.context_window_input = QLineEdit(self.advanced)
        self.max_output_input = QLineEdit(self.advanced)
        for key, widget in (("context_window_tokens", self.context_window_input),
                            ("max_output_tokens", self.max_output_input)):
            widget.setPlaceholderText("留空沿用现有策略")
            widget.setText(str(source[key]) if key in source else "")
        advanced_layout.addWidget(self._field("上下文窗口（tokens）", self.context_window_input))
        advanced_layout.addWidget(self._field("最大输出（tokens）", self.max_output_input))
        advanced_layout.addWidget(self._note(
            "仅在服务给出明确限制时填写；留空使用应用现有策略。", self.advanced))
        self.bootstrap_field = QWidget(self.advanced)
        bootstrap_layout = QVBoxLayout(self.bootstrap_field)
        bootstrap_layout.setContentsMargins(0, 0, 0, 0)
        bootstrap_layout.setSpacing(4)
        self.bootstrap_check = QCheckBox("启用极简启动", self.bootstrap_field)
        self.bootstrap_check.setChecked(source.get("bootstrap_plugin") == self._bootstrap_plugin.plugin_id)
        self.bootstrap_hint = self._note("DeepSeek 官方模型默认开启。新对话首轮使用极简上下文，随后恢复完整能力。", self.bootstrap_field)
        bootstrap_layout.addWidget(self.bootstrap_check)
        bootstrap_layout.addWidget(self.bootstrap_hint)
        advanced_layout.addWidget(self.bootstrap_field)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        self.error_label = QLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.model_name_input.textChanged.connect(self._model_name_changed)
        self.api_protocol_combo.activated.connect(self._protocol_selected)
        self.bootstrap_check.toggled.connect(self._bootstrap_selected)
        for widget in self.findChildren(QLineEdit):
            widget.textChanged.connect(self._edited)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self._edited)
        for widget in self.findChildren(PageScrollSafeComboBox):
            widget.currentIndexChanged.connect(self._edited)
        if not self._editing_existing:
            self._model_name_changed()
        self._sync_controls()
        self._loading = False
        for key, widget, signal in (("supports_vision", self.vision_check, "toggled"),
                                    ("supports_image_generation", self.image_generation_check, "toggled"),
                                    ("api_protocol", self.api_protocol_combo, "activated"),
                                    ("reasoning_efforts", self.thinking_check, "toggled"),
                                    ("context_window_tokens", self.context_window_input, "textEdited"),
                                    ("max_output_tokens", self.max_output_input, "textEdited")):
            getattr(widget, signal).connect(lambda *_args, field=key: self._mark_manual(field))
        for check in self.reasoning_checks.values():
            check.toggled.connect(lambda *_: self._mark_manual("reasoning_efforts"))
        bind_theme(self, self.refresh_theme, surface="settings")

    def _mark_manual(self, field):
        self._manual_fields.add(field)
        if field == "api_protocol":
            self.update_discovery(self._source.get("discovery", {}))
        self.changed.emit()

    def update_discovery(self, evidence):
        if evidence:
            self._source["discovery"] = copy.deepcopy(evidence)
        caps = evidence.get("capabilities", {})
        warnings = []
        if evidence.get("protocol_status") == "unknown" and "api_protocol" not in self._manual_fields:
            warnings.append("调用方式未确认，请按站点说明选择 API 协议。")
        if evidence.get("metadata_warning"):
            warnings.append(evidence["metadata_warning"])
        self.discovery_warning.setText("\n".join(warnings))
        self.discovery_warning.setVisible(bool(warnings))
        details = []
        for key, title in (("supports_vision", "图片理解"), ("supports_image_generation", "Responses 图片生成"),
                           ("supports_tools", "工具调用"), ("reasoning_efforts", "推理档位")):
            value = caps.get(key)
            text = "未提供" if value is None else (" / ".join(value) or "不支持") if isinstance(value, list) else "支持" if value else "不支持"
            details.append(f"{title}：{text}")
        self.discovery_note.setText("未经调用验证 · " + "；".join(details))
        self.discovery_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.discovery_note.setVisible(bool(evidence) and self.discovery_details_button.isChecked())
        self.discovery_details_button.setVisible(bool(evidence))
        self.discovery_details.setText(json.dumps(evidence, ensure_ascii=False, indent=2))
        self.discovery_details.setVisible(bool(evidence) and self.discovery_details_button.isChecked())

    def _toggle_discovery(self, expanded):
        self.discovery_details_button.setText("▾ 站点声明" if expanded else "▸ 站点声明")
        self.discovery_note.setVisible(expanded)
        self.discovery_details.setVisible(expanded)

    def _note(self, text, parent=None):
        label = QLabel(text, parent or self)
        label.setWordWrap(True)
        self._notes.append(label)
        return label

    def _heading(self, text, layout):
        self._separator(layout)
        label = QLabel(text, self)
        self._headings.append(label)
        layout.addWidget(label)

    def _separator(self, layout):
        line = QFrame(self)
        line.setFixedHeight(1)
        self._separators.append(line)
        layout.addWidget(line)

    def _field(self, text, widget, note=""):
        field = QWidget(widget.parentWidget())
        box = QVBoxLayout(field)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.setAlignment(Qt.AlignTop)
        label = QLabel(text, field)
        label.setBuddy(widget)
        box.addWidget(label)
        box.addWidget(widget)
        if note:
            box.addWidget(self._note(note, field))
        return field

    def _layout_info(self):
        narrow = self.width() < 520
        self.info_grid.addWidget(self.model_field, 0, 0, 1, 1 if not narrow else 2)
        self.info_grid.addWidget(self.display_field, 1 if narrow else 0, 0 if narrow else 1, 1, 2 if narrow else 1)
        self.info_grid.addWidget(self.protocol_field, 2 if narrow else 1, 0, 1, 2)
        self.info_grid.addWidget(self.anthropic_note, 3 if narrow else 2, 0, 1, 2)
        self.info_grid.setColumnStretch(0, 1)
        self.info_grid.setColumnStretch(1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_info()

    def _protocol_selected(self, _index):
        self._protocol_user_selected = True

    def _bootstrap_selected(self, checked):
        self._bootstrap_choice = checked

    def _model_name_changed(self, *_args):
        if self._editing_existing:
            return
        if self.provider_id == "openai" and not self._protocol_user_selected:
            protocol = API_PROTOCOL_RESPONSES if (
                is_deepseek_official_base_url(self.base_url) or is_gpt_5_6_model(self.model_name_input.text())
            ) else API_PROTOCOL_CHAT_COMPLETIONS
            self.api_protocol_combo.blockSignals(True)
            self.api_protocol_combo.setCurrentIndex(self.api_protocol_combo.findData(protocol))
            self.api_protocol_combo.blockSignals(False)
        if is_deepseek_official_base_url(self.base_url):
            self.vision_check.blockSignals(True)
            self.vision_check.setChecked(bool(build_new_model_defaults(
                self.provider_id, self.base_url, self.model_name_input.text()).get("supports_vision")))
            self.vision_check.blockSignals(False)

    def set_service(self, provider_id, base_url):
        self.provider_id = provider_id
        self.base_url = str(base_url or "").strip()
        self._sync_controls()

    def _sync_controls(self):
        openai = self.provider_id == "openai"
        self.protocol_field.setVisible(openai)
        self.anthropic_note.setVisible(not openai)
        self.image_generation_check.setVisible(openai)
        self.image_hint.setVisible(openai)
        self.thinking_check.setVisible(openai)
        self.reasoning_fields.setVisible(openai and self.thinking_check.isChecked())
        enabled = openai and self.thinking_check.isChecked()
        allowed = [key for key, check in self.reasoning_checks.items() if check.isChecked()]
        self.reasoning_combo.blockSignals(True)
        for index in range(self.reasoning_combo.count()):
            self.reasoning_combo.model().item(index).setEnabled(self.reasoning_combo.itemData(index) in allowed)
        if allowed and self.reasoning_combo.currentData() not in allowed:
            self.reasoning_combo.setCurrentIndex(self.reasoning_combo.findData(allowed[0]))
        self.reasoning_combo.setEnabled(enabled and bool(allowed))
        self.reasoning_combo.blockSignals(False)
        self.effort_preview.setText("选择器将显示：" + (" · ".join(allowed) if allowed else "请至少勾选一个档位"))
        supported = self._bootstrap_plugin.supports(
            {"provider_type": self.provider_id, "base_url": self.base_url}, self.model_name_input.text().strip())
        self.bootstrap_field.setVisible(supported)
        self.bootstrap_check.setEnabled(supported)
        self.bootstrap_check.blockSignals(True)
        self.bootstrap_check.setChecked(supported and self._bootstrap_choice is not False)
        self.bootstrap_check.blockSignals(False)

    def _edited(self, *_args):
        if self._loading:
            return
        self._sync_controls()
        self.error_label.hide()
        self.changed.emit()

    def get_model(self, existing_id=None):
        name = self.model_name_input.text().strip()
        item = copy.deepcopy(self._source)
        item.update(id=existing_id or item.get("id") or "", model_name=name,
                    display_name=self.display_name_input.text().strip() or name,
                    supports_vision=self.vision_check.isChecked(),
                    supports_image_generation=self.provider_id == "openai" and self.image_generation_check.isChecked(),
                    bootstrap_plugin=self._bootstrap_plugin.plugin_id if (
                        self.bootstrap_check.isEnabled() and self.bootstrap_check.isChecked()) else "")
        for key, widget in (("context_window_tokens", self.context_window_input), ("max_output_tokens", self.max_output_input)):
            value = widget.text().strip()
            item.pop(key, None)
            if value:
                # Invalid text remains a draft. validate() blocks persistence.
                item[key] = int(value) if value.isdecimal() and len(value) <= 9 else value
        if self.provider_id == "openai":
            efforts = [key for key, check in self.reasoning_checks.items()
                       if self.thinking_check.isChecked() and check.isChecked()]
            effort = self.reasoning_combo.currentData() if efforts else ""
            item.update(api_protocol=self.api_protocol_combo.currentData(), reasoning_efforts=efforts,
                        reasoning_effort=effort, deepseek_thinking_enabled=bool(efforts),
                        deepseek_reasoning_effort=effort or DEFAULT_DEEPSEEK_REASONING_EFFORT)
        if "discovery" in item:
            item["discovery"]["manual_fields"] = sorted(self._manual_fields)
        return item

    def validation_error(self):
        if not self.model_name_input.text().strip():
            return "请填写模型标识。", self.model_name_input
        if self.provider_id == "openai":
            if self.thinking_check.isChecked() and not any(check.isChecked() for check in self.reasoning_checks.values()):
                return "请至少选择一个支持的推理档位。", self.thinking_check
            if self.image_generation_check.isChecked() and self.api_protocol_combo.currentData() != API_PROTOCOL_RESPONSES:
                return "图片生成需要 Responses 协议，请切换协议或关闭图片生成。", self.api_protocol_combo
        for title, widget in (("上下文窗口", self.context_window_input), ("最大输出", self.max_output_input)):
            value = widget.text().strip()
            if value and (not value.isdecimal() or len(value) > 9 or not 0 < int(value) <= 100000000):
                return f"{title}请填写 1–100000000 的整数，或留空沿用现有策略。", widget
        context, output = self.context_window_input.text().strip(), self.max_output_input.text().strip()
        if context and output and int(output) >= int(context):
            return "最大输出必须小于上下文窗口，为输入保留空间。", self.max_output_input
        return "", None

    def validate(self):
        message, widget = self.validation_error()
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        if widget:
            if self.advanced.isAncestorOf(widget):
                self.advanced_button.setChecked(True)
            widget.setFocus()
        return not message

    def refresh_theme(self, _resolved=None):
        self.setStyleSheet(f"QWidget#ModelEditForm {{ background: transparent; color: {DesignTokens.text_primary}; }}")
        for widget in self.findChildren(QLineEdit) + self.findChildren(PageScrollSafeComboBox):
            widget.setStyleSheet(model_field_style())
        for label in self._notes:
            label.setStyleSheet(f"color: {DesignTokens.text_secondary}; font-size: {DesignTokens.font_size_meta}px;")
        for label in self._headings:
            label.setStyleSheet(f"color: {DesignTokens.text_primary}; font-size: {DesignTokens.font_size_section}px; font-weight: 600;")
        for line in self._separators:
            line.setStyleSheet(f"QFrame {{ background: {DesignTokens.separator}; border: none; }}")
        self.effort_preview.setStyleSheet(f"QLabel {{ color: {DesignTokens.text_secondary}; background: {DesignTokens.bg_secondary}; border-radius: 6px; padding: 10px 12px; font-size: {DesignTokens.font_size_meta}px; }}")
        self.advanced_button.setStyleSheet(product_button_style("ghost"))
        self.discovery_details_button.setStyleSheet(product_button_style("ghost"))
        self.error_label.setStyleSheet(f"color: {DesignTokens.error_text}; font-size: {DesignTokens.font_size_meta}px;")
