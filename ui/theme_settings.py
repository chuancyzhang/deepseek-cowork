from __future__ import annotations

import copy
import json
import os
import re
import uuid

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from core.theme_package import DEFAULT_WORKSPACE_SCENE, THEME_PACKAGE_SUFFIX, build_asset_record

from core.theme import bind_theme, default_design_tokens, DesignTokens
from core.theme_service import (
    DEFAULT_THEME_ID,
    ThemeRepository,
    append_theme_log,
    resolve_theme,
    theme_contrast_warnings,
    validate_theme_document,
    validate_theme_overrides,
)
from ui.primitives import ProductInlineNotice, ProductMessageBox, product_code_style, product_field_style


class _ClickActivatedWheelMixin:
    """Only let a wheel gesture edit a field after an explicit click."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wheel_activated_by_click = False
        line_edit = self.lineEdit() if hasattr(self, "lineEdit") else None
        if line_edit is not None:
            line_edit.installEventFilter(self)

    def eventFilter(self, watched, event):
        line_edit = self.lineEdit() if hasattr(self, "lineEdit") else None
        if watched is line_edit and event.type() == QEvent.MouseButtonPress:
            self._wheel_activated_by_click = True
        return super().eventFilter(watched, event)

    def event(self, event):
        if event.type() == QEvent.MouseButtonPress:
            self._wheel_activated_by_click = True
        return super().event(event)

    def mousePressEvent(self, event):
        self._wheel_activated_by_click = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        popup_visible = (
            hasattr(self, "view")
            and self.view() is not None
            and self.view().isVisible()
        )
        if event.reason() != Qt.PopupFocusReason and not popup_visible:
            self._wheel_activated_by_click = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        if not self._wheel_activated_by_click or not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class _ClickActivatedSpinBox(_ClickActivatedWheelMixin, QSpinBox):
    pass


class _ClickActivatedDoubleSpinBox(_ClickActivatedWheelMixin, QDoubleSpinBox):
    pass


class _ClickActivatedComboBox(_ClickActivatedWheelMixin, QComboBox):
    pass


class _ClickActivatedFontComboBox(_ClickActivatedWheelMixin, QFontComboBox):
    pass


class ThemeSettingsPanel(QWidget):
    changed = Signal()

    BASIC_COLOR_TOKENS = (
        ("primary", "强调色"),
        ("bg_sidebar", "左侧栏背景"),
        ("bg_chat", "聊天区背景"),
        ("composer_bg", "输入区背景"),
        ("right_sidebar_bg", "右侧栏背景"),
    )
    BASIC_GEOMETRY_TOKENS = (
        ("sidebar_width", "左侧栏首选宽度"),
        ("conversation_preferred_width", "聊天内容首选宽度"),
        ("drawer_preferred_min_width", "右侧栏首选宽度"),
    )

    def __init__(self, repository: ThemeRepository, runtime_manager=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.runtime_manager = runtime_manager
        self._loading = False
        self._snapshot = repository.load()
        self._themes = [copy.deepcopy(item) for item in self._snapshot.themes]
        self._active_theme_id = self._snapshot.active_theme_id
        self._current_theme_id = self._active_theme_id
        self._preview_signature = None
        self._preview_active = False
        self._theme_frames = []
        self._theme_headings = []
        self._theme_details = []
        self._loaded_basic_values = {}
        self._loaded_basic_explicit = set()
        self.last_commit_warning = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(self._build_manager_section())
        root.addWidget(self._build_basic_section())
        root.addWidget(self._build_advanced_section())
        root.addWidget(self._build_scene_section())
        root.addWidget(self._build_asset_section())
        root.addWidget(self._build_structure_section())
        root.addWidget(
            ProductInlineNotice(
                "也可以在对话中描述背景、图标、布局、字体和配色。AI 只能修改受验证的呈现层，预览确认后才会保存。",
                "info",
            )
        )
        root.addStretch()
        self._connect_signals()
        bind_theme(self, self._refresh_theme_styles, surface="management")
        self._rebuild_theme_combo(self._active_theme_id)

    def _section(self, title, description):
        frame = QFrame()
        self._theme_frames.append(frame)
        frame.setProperty("uiSurface", True)
        frame.setStyleSheet(
            f'QFrame[uiSurface="true"] {{ background: transparent; border: none; '
            f"border-bottom: 1px solid {DesignTokens.separator}; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 8, 4, 16)
        layout.setSpacing(10)
        heading = QLabel(title)
        self._theme_headings.append(heading)
        heading.setStyleSheet(
            f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; "
            f"color: {DesignTokens.text_primary};"
        )
        layout.addWidget(heading)
        detail = QLabel(description)
        self._theme_details.append(detail)
        detail.setWordWrap(True)
        detail.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
        )
        layout.addWidget(detail)
        return frame, layout

    def _refresh_theme_styles(self, _resolved=None):
        for frame in self._theme_frames:
            frame.setStyleSheet(
                f'QFrame[uiSurface="true"] {{ background: transparent; border: none; '
                f"border-bottom: 1px solid {DesignTokens.separator}; }}"
            )
        for heading in self._theme_headings:
            heading.setStyleSheet(
                f"font-size: {DesignTokens.font_size_section}px; font-weight: 600; "
                f"color: {DesignTokens.text_primary};"
            )
        for detail in self._theme_details:
            detail.setStyleSheet(
                f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
            )
        self.token_editor.setStyleSheet(product_code_style("QPlainTextEdit"))
        self.validation_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
        )

    def refresh_theme(self, _resolved=None):
        self._refresh_theme_styles()

    def _toggle_advanced(self, expanded):
        self.advanced_container.setVisible(bool(expanded))
        self.advanced_toggle.setText("收起高级 JSON" if expanded else "展开高级 JSON")

    def _build_manager_section(self):
        frame, layout = self._section(
            "主题模式",
            "默认主题保持只读；自定义主题由设置页和 AI 共用同一个主题服务。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.theme_combo = _ClickActivatedComboBox()
        self.theme_combo.setMinimumWidth(220)
        self.theme_combo.setStyleSheet(product_field_style())
        row.addWidget(self.theme_combo, 1)
        self.new_btn = QPushButton("新建")
        self.copy_btn = QPushButton("复制")
        self.delete_btn = QPushButton("删除")
        self.preview_btn = QPushButton("预览")
        self.preview_btn.setObjectName("PrimaryBtn")
        for button in (self.new_btn, self.copy_btn, self.delete_btn):
            button.setObjectName("SecondaryBtn")
            row.addWidget(button)
        row.addWidget(self.preview_btn)
        layout.addLayout(row)
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.import_btn = QPushButton("导入主题包")
        self.export_btn = QPushButton("导出主题包")
        self.refresh_btn = QPushButton("刷新列表")
        self.open_folder_btn = QPushButton("打开主题文件夹")
        self.reset_btn = QPushButton("切回默认")
        for button in (
            self.import_btn,
            self.export_btn,
            self.refresh_btn,
            self.open_folder_btn,
            self.reset_btn,
        ):
            button.setObjectName("SecondaryBtn")
            file_row.addWidget(button)
        file_row.addStretch()
        layout.addLayout(file_row)
        return frame

    def _build_basic_section(self):
        frame, layout = self._section(
            "基础外观",
            "修改只更新草稿。点击“预览”后才一次性刷新界面；保存后正式应用。",
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(40)
        form.addRow("主题名称", self.name_edit)
        self.font_combo = _ClickActivatedFontComboBox()
        self.font_combo.setWritingSystem(QFontDatabase.Any)
        form.addRow("界面字体", self.font_combo)
        self.mono_font_combo = _ClickActivatedFontComboBox()
        self.mono_font_combo.setFontFilters(QFontComboBox.MonospacedFonts)
        form.addRow("等宽字体", self.mono_font_combo)
        self.font_scale_spin = _ClickActivatedDoubleSpinBox()
        self.font_scale_spin.setRange(0.80, 1.50)
        self.font_scale_spin.setSingleStep(0.05)
        form.addRow("字体缩放", self.font_scale_spin)
        self.density_combo = _ClickActivatedComboBox()
        self.density_combo.addItem("紧凑", "compact")
        self.density_combo.addItem("标准", "standard")
        self.density_combo.addItem("舒适", "comfortable")
        form.addRow("界面密度", self.density_combo)
        self.radius_scale_spin = _ClickActivatedDoubleSpinBox()
        self.radius_scale_spin.setRange(0.50, 1.50)
        self.radius_scale_spin.setSingleStep(0.05)
        form.addRow("圆角比例", self.radius_scale_spin)
        self.color_edits = {}
        for token_name, label in self.BASIC_COLOR_TOKENS:
            editor = QLineEdit()
            editor.setPlaceholderText("#RRGGBB")
            editor.setMaxLength(32)
            self.color_edits[token_name] = editor
            container = QWidget()
            color_layout = QHBoxLayout(container)
            color_layout.setContentsMargins(0, 0, 0, 0)
            color_layout.setSpacing(8)
            color_layout.addWidget(editor, 1)
            choose_btn = QPushButton("选择")
            choose_btn.setObjectName("SecondaryBtn")
            choose_btn.clicked.connect(
                lambda _checked=False, target=editor: self._choose_color(target)
            )
            color_layout.addWidget(choose_btn)
            form.addRow(label, container)
        self.geometry_spins = {}
        defaults = default_design_tokens()
        for token_name, label in self.BASIC_GEOMETRY_TOKENS:
            editor = _ClickActivatedSpinBox()
            if token_name == "sidebar_width":
                editor.setRange(180, 480)
            elif token_name == "drawer_preferred_min_width":
                editor.setRange(240, 720)
            else:
                editor.setRange(600, 1600)
            editor.setSuffix(" px")
            editor.setValue(int(defaults[token_name]))
            self.geometry_spins[token_name] = editor
            form.addRow(label, editor)
        layout.addLayout(form)
        return frame

    def _build_advanced_section(self):
        frame, layout = self._section(
            "高级语义令牌",
            "按需展开完整覆盖 JSON。未知令牌和非法值会直接报错。",
        )
        self.advanced_toggle = QPushButton("展开高级 JSON")
        self.advanced_toggle.setObjectName("SecondaryBtn")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced_container = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_container)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        self.token_editor = QPlainTextEdit()
        self.token_editor.setMinimumHeight(180)
        self.token_editor.setPlaceholderText('{\n  "primary": "颜色值"\n}')
        self.token_editor.setProperty("codeSurface", True)
        self.token_editor.setStyleSheet(product_code_style("QPlainTextEdit"))
        advanced_layout.addWidget(self.token_editor)
        self.validation_label = QLabel("")
        self.validation_label.setWordWrap(True)
        self.validation_label.setStyleSheet(
            f"font-size: {DesignTokens.font_size_meta}px; color: {DesignTokens.text_secondary};"
        )
        self.advanced_container.setVisible(False)
        layout.addWidget(self.advanced_container)
        layout.addWidget(self.validation_label)
        return frame

    def _build_asset_section(self):
        frame, layout = self._section(
            "主题图片资源",
            "图片会复制进主题包；支持 PNG、JPEG、GIF，以及静态或动态 WebP。GIF 和动态 WebP 仅可作为工作区背景。",
        )
        self.asset_list = QListWidget()
        self.asset_list.setMaximumHeight(132)
        self.asset_list.setIconSize(QSize(48, 36))
        layout.addWidget(self.asset_list)
        row = QHBoxLayout()
        self.asset_add_btn = QPushButton("导入图片")
        self.asset_remove_btn = QPushButton("移除图片")
        for button in (self.asset_add_btn, self.asset_remove_btn):
            button.setObjectName("SecondaryBtn")
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        return frame

    def _build_structure_section(self):
        frame, layout = self._section(
            "工作台结构摘要",
            "显示 AI 可配置的背景、组件呈现与白名单文案；动作和路由不属于主题。",
        )
        self.structure_summary = QPlainTextEdit()
        self.structure_summary.setReadOnly(True)
        self.structure_summary.setProperty("codeSurface", True)
        self.structure_summary.setMaximumHeight(180)
        self.structure_summary.setStyleSheet(product_code_style("QPlainTextEdit"))
        layout.addWidget(self.structure_summary)
        return frame

    def _build_scene_section(self):
        frame, layout = self._section(
            "统一工作区场景",
            "图片和网格只在这里绘制一次；各区域只能使用 transparent、tint 或 opaque 材质。",
        )
        self.scene_editor = QPlainTextEdit()
        self.scene_editor.setProperty("codeSurface", True)
        self.scene_editor.setMaximumHeight(240)
        self.scene_editor.setStyleSheet(product_code_style("QPlainTextEdit"))
        layout.addWidget(self.scene_editor)
        return frame

    def _connect_signals(self):
        self.theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        self.new_btn.clicked.connect(self._new_theme)
        self.copy_btn.clicked.connect(self._copy_theme)
        self.delete_btn.clicked.connect(self._delete_theme)
        self.preview_btn.clicked.connect(self._preview_current)
        self.reset_btn.clicked.connect(self._select_default)
        self.import_btn.clicked.connect(self._import_theme)
        self.export_btn.clicked.connect(self._export_theme)
        self.refresh_btn.clicked.connect(self._reload_from_directory)
        self.open_folder_btn.clicked.connect(self._open_theme_folder)
        self.asset_add_btn.clicked.connect(self._add_asset)
        self.asset_remove_btn.clicked.connect(self._remove_asset)
        self.name_edit.textChanged.connect(self._on_editor_changed)
        self.font_combo.currentFontChanged.connect(self._on_editor_changed)
        self.mono_font_combo.currentFontChanged.connect(self._on_editor_changed)
        self.font_scale_spin.valueChanged.connect(self._on_editor_changed)
        self.density_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.radius_scale_spin.valueChanged.connect(self._on_editor_changed)
        self.token_editor.textChanged.connect(self._on_editor_changed)
        self.scene_editor.textChanged.connect(self._on_editor_changed)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        for editor in self.color_edits.values():
            editor.textChanged.connect(self._on_editor_changed)
        for editor in self.geometry_spins.values():
            editor.valueChanged.connect(self._on_editor_changed)

    def _profile(self, theme_id):
        if theme_id == DEFAULT_THEME_ID:
            return {
                "id": DEFAULT_THEME_ID,
                "name": "默认主题",
                "base": DEFAULT_THEME_ID,
                "schema_version": 2,
                "overrides": {},
                "assets": {},
                "workspace_scene": copy.deepcopy(DEFAULT_WORKSPACE_SCENE),
                "surfaces": {},
                "components": {},
                "content": {},
            }
        return next((item for item in self._themes if item.get("id") == theme_id), None)

    def _rebuild_theme_combo(self, selected_id):
        self._loading = True
        self.theme_combo.clear()
        self.theme_combo.addItem("默认主题", DEFAULT_THEME_ID)
        for item in self._themes:
            self.theme_combo.addItem(item.get("name") or "未命名主题", item.get("id"))
        index = self.theme_combo.findData(selected_id)
        self.theme_combo.setCurrentIndex(max(0, index))
        self._loading = False
        self._load_profile(selected_id if index >= 0 else DEFAULT_THEME_ID)

    def _load_profile(self, theme_id):
        profile = self._profile(theme_id)
        if not profile:
            raise ValueError(f"主题不存在：{theme_id}")
        resolved = resolve_theme(profile, default_design_tokens())
        overrides = copy.deepcopy(profile.get("overrides") or {})
        explicit_tokens = overrides.get("tokens") or {}
        self._loading = True
        self._current_theme_id = profile["id"]
        self.name_edit.setText(profile.get("name") or "")
        self.font_combo.setCurrentFont(QFont(resolved["font_family"]))
        self.mono_font_combo.setCurrentFont(QFont(resolved["mono_font_family"]))
        self.font_scale_spin.setValue(float(overrides.get("font_scale", 1.0)))
        self.density_combo.setCurrentIndex(
            max(0, self.density_combo.findData(overrides.get("density", "standard")))
        )
        self.radius_scale_spin.setValue(float(overrides.get("radius_scale", 1.0)))
        for token_name, editor in self.color_edits.items():
            editor.setText(str(resolved["tokens"].get(token_name) or ""))
        for token_name, editor in self.geometry_spins.items():
            editor.setValue(int(resolved["tokens"].get(token_name) or editor.value()))
        basic_token_names = {
            token_name
            for token_name, _label in (
                *self.BASIC_COLOR_TOKENS,
                *self.BASIC_GEOMETRY_TOKENS,
            )
        }
        self._loaded_basic_values = {
            token_name: resolved["tokens"].get(token_name)
            for token_name in basic_token_names
        }
        self._loaded_basic_explicit = {
            token_name for token_name in basic_token_names if token_name in explicit_tokens
        }
        basic_names = basic_token_names
        advanced_tokens = {
            name: value
            for name, value in (overrides.get("tokens") or {}).items()
            if name not in basic_names
        }
        self.token_editor.setPlainText(
            json.dumps(advanced_tokens, ensure_ascii=False, indent=2)
        )
        self.scene_editor.setPlainText(
            json.dumps(
                {
                    "workspace_scene": profile.get("workspace_scene") or DEFAULT_WORKSPACE_SCENE,
                    "surfaces": profile.get("surfaces") or {},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self.asset_list.clear()
        asset_bytes = self._ensure_profile_asset_bytes(profile) if profile.get("id") != DEFAULT_THEME_ID else {}
        for asset_id, record in sorted((profile.get("assets") or {}).items()):
            animation = record.get("animation") or {}
            animation_text = ""
            if animation:
                animation_text = (
                    f"  ·  动态 {animation.get('frame_count')} 帧"
                    f" / {int(animation.get('duration_ms') or 0) / 1000:g} 秒"
                )
            self.asset_list.addItem(
                f"{asset_id}  ·  {record.get('width')}×{record.get('height')}"
                f"  ·  {record.get('media_type')}{animation_text}"
            )
            item = self.asset_list.item(self.asset_list.count() - 1)
            item.setData(Qt.UserRole, asset_id)
            pixmap = QPixmap()
            if not pixmap.loadFromData(asset_bytes.get(record.get("path"), b"")):
                raise ValueError(f"主题资产缩略图无法解码：{asset_id}")
            item.setIcon(QIcon(pixmap))
        self.structure_summary.setPlainText(
            json.dumps(
                {
                    "background_owner": "workspace_scene",
                    "components": profile.get("components") or {},
                    "content": profile.get("content") or {},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        editable = profile["id"] != DEFAULT_THEME_ID
        for widget in (
            self.name_edit,
            self.font_combo,
            self.mono_font_combo,
            self.font_scale_spin,
            self.density_combo,
            self.radius_scale_spin,
            self.token_editor,
            self.scene_editor,
            *self.color_edits.values(),
            *self.geometry_spins.values(),
        ):
            widget.setEnabled(editable)
        self.delete_btn.setEnabled(editable)
        self.export_btn.setEnabled(editable)
        self.asset_add_btn.setEnabled(editable)
        self.asset_remove_btn.setEnabled(editable)
        self._loading = False
        if not editable:
            self.validation_label.setText("默认主题为只读基线。")
        else:
            warnings = theme_contrast_warnings(resolved)
            if warnings:
                self.validation_label.setText(
                    f"草稿有效；有 {len(warnings)} 项文字对比度提示。点击“预览”后应用。"
                )
            else:
                self.validation_label.setText("草稿有效，尚未预览。")

    def _collect_overrides(self):
        tokens = json.loads(self.token_editor.toPlainText().strip() or "{}")
        if not isinstance(tokens, dict):
            raise ValueError("高级语义令牌必须是 JSON 对象。")
        base_tokens = default_design_tokens()
        for token_name, editor in self.color_edits.items():
            value = editor.text().strip()
            loaded = str(self._loaded_basic_values.get(token_name) or "")
            if (
                token_name not in self._loaded_basic_explicit
                and value.casefold() == loaded.casefold()
            ) or value.casefold() == str(base_tokens.get(token_name) or "").casefold():
                tokens.pop(token_name, None)
            else:
                tokens[token_name] = value
        for token_name, editor in self.geometry_spins.items():
            value = int(editor.value())
            if (
                token_name not in self._loaded_basic_explicit
                and value == int(self._loaded_basic_values.get(token_name))
            ) or value == int(base_tokens.get(token_name)):
                tokens.pop(token_name, None)
            else:
                tokens[token_name] = value
        return validate_theme_overrides(
            {
                "font_family": self.font_combo.currentFont().family(),
                "mono_font_family": self.mono_font_combo.currentFont().family(),
                "font_scale": self.font_scale_spin.value(),
                "density": self.density_combo.currentData(),
                "radius_scale": self.radius_scale_spin.value(),
                "tokens": tokens,
            },
            base_tokens,
        )

    def _collect_current_editor_values(self):
        """Validate and return editor-backed fields without mutating the draft."""
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("主题名称不能为空。")
        overrides = self._collect_overrides()
        scene_document = json.loads(self.scene_editor.toPlainText().strip() or "{}")
        if not isinstance(scene_document, dict) or set(scene_document) - {"workspace_scene", "surfaces"}:
            raise ValueError("统一工作区场景必须是仅含 workspace_scene 和 surfaces 的 JSON 对象。")
        workspace_scene = scene_document.get("workspace_scene")
        surfaces = scene_document.get("surfaces", {})
        if not isinstance(workspace_scene, dict) or not isinstance(surfaces, dict):
            raise ValueError("workspace_scene 和 surfaces 必须是 JSON 对象。")
        return {
            "name": name,
            "overrides": overrides,
            "workspace_scene": workspace_scene,
            "surfaces": surfaces,
        }

    def _store_current_editor(self):
        if self._current_theme_id == DEFAULT_THEME_ID:
            return
        profile = self._profile(self._current_theme_id)
        if profile is None:
            raise ValueError(f"主题不存在：{self._current_theme_id}")
        profile.update(self._collect_current_editor_values())

    def _draft_signature(self, profile):
        return json.dumps(
            {
                "id": profile.get("id"),
                "name": profile.get("name"),
                "overrides": profile.get("overrides") or {},
                "assets": profile.get("assets") or {},
                "workspace_scene": profile.get("workspace_scene") or DEFAULT_WORKSPACE_SCENE,
                "surfaces": profile.get("surfaces") or {},
                "components": profile.get("components") or {},
                "content": profile.get("content") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _preview_current(self):
        try:
            self._store_current_editor()
            profile = self._profile(self._current_theme_id)
            append_theme_log(
                self.repository.data_dir,
                "settings_preview_submit",
                theme_id=profile.get("id"),
            )
            append_theme_log(
                self.repository.data_dir,
                "settings_preview_start",
                theme_id=profile.get("id"),
            )
            resolved = resolve_theme(profile, default_design_tokens())
            warnings = theme_contrast_warnings(resolved)
            preview = self.repository.write_preview(
                name=profile.get("name") or "自定义主题",
                overrides=profile.get("overrides") or {},
                default_tokens=default_design_tokens(),
                session_id="settings",
                replace_existing=True,
                assets=profile.get("assets") or {},
                workspace_scene=profile.get("workspace_scene") or DEFAULT_WORKSPACE_SCENE,
                surfaces=profile.get("surfaces") or {},
                components=profile.get("components") or {},
                content=profile.get("content") or {},
                asset_bytes=self._ensure_profile_asset_bytes(profile),
            )
            if self.runtime_manager is not None and not self.runtime_manager.apply_repository_state(
                reason="settings_preview"
            ):
                raise ValueError("主题无法应用，请检查系统字体和令牌配置。")
            self._preview_signature = self._draft_signature(profile)
            self._preview_active = True
            append_theme_log(
                self.repository.data_dir,
                "settings_preview_finish",
                theme_id=profile.get("id"),
                preview_id=preview["preview_id"],
                preview_revision=preview["revision"],
                affected_areas=sorted(
                    {
                        str(item.get("area") or "")
                        for item in warnings
                        if item.get("area")
                    }
                ),
            )
            if warnings:
                self.validation_label.setText(
                    f"已预览 revision {preview['revision']}；有 {len(warnings)} 项文字对比度提示。"
                )
            else:
                self.validation_label.setText(
                    f"已预览 revision {preview['revision']}，草稿与当前预览一致。"
                )
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_preview_error",
                theme_id=self._current_theme_id,
                error=str(exc),
            )
            self.validation_label.setText(f"预览失败：{exc}")

    def _on_theme_selected(self, _index):
        if self._loading:
            return
        try:
            self._store_current_editor()
            selected_id = str(self.theme_combo.currentData() or DEFAULT_THEME_ID)
            self._active_theme_id = selected_id
            self._load_profile(selected_id)
            if self._preview_active:
                self.validation_label.setText("草稿已变化，当前预览不是最新。")
            self.changed.emit()
        except Exception as exc:
            self._loading = True
            previous_index = self.theme_combo.findData(self._current_theme_id)
            if previous_index >= 0:
                self.theme_combo.setCurrentIndex(previous_index)
            self._loading = False
            self.validation_label.setText(f"无法切换主题：{exc}")

    def _on_editor_changed(self, *_args):
        if self._loading or self._current_theme_id == DEFAULT_THEME_ID:
            return
        try:
            self._store_current_editor()
            profile = self._profile(self._current_theme_id)
            resolved = resolve_theme(profile, default_design_tokens())
            warnings = theme_contrast_warnings(resolved)
            if self._preview_active and self._draft_signature(profile) != self._preview_signature:
                self.validation_label.setText("草稿已变化，当前预览不是最新。")
            elif warnings:
                self.validation_label.setText(
                    f"草稿有效；有 {len(warnings)} 项文字对比度提示。"
                )
            else:
                self.validation_label.setText("草稿有效，尚未应用。")
        except Exception as exc:
            self.validation_label.setText(f"草稿无效：{exc}")
        self.changed.emit()

    def _unique_name(self, base):
        existing = {str(item.get("name") or "").casefold() for item in self._themes}
        if base.casefold() not in existing:
            return base
        index = 2
        while f"{base} {index}".casefold() in existing:
            index += 1
        return f"{base} {index}"

    def _append_theme(self, name, overrides, source=None, asset_bytes=None):
        source = source or {}
        item = {
            "id": uuid.uuid4().hex,
            "name": self._unique_name(name),
            "base": DEFAULT_THEME_ID,
            "schema_version": 2,
            "overrides": copy.deepcopy(overrides),
            "assets": copy.deepcopy(source.get("assets") or {}),
            "workspace_scene": copy.deepcopy(source.get("workspace_scene") or DEFAULT_WORKSPACE_SCENE),
            "surfaces": copy.deepcopy(source.get("surfaces") or {}),
            "components": copy.deepcopy(source.get("components") or {}),
            "content": copy.deepcopy(source.get("content") or {}),
        }
        if asset_bytes:
            item["_asset_bytes"] = dict(asset_bytes)
        self._themes.append(item)
        self._active_theme_id = item["id"]
        self._rebuild_theme_combo(item["id"])
        self.changed.emit()

    def _new_theme(self):
        try:
            self._store_current_editor()
            self._append_theme("自定义主题", {})
        except Exception as exc:
            self.validation_label.setText(f"暂无法新建主题：{exc}")

    def _copy_theme(self):
        try:
            self._store_current_editor()
            source = self._profile(self._current_theme_id)
            asset_bytes = (
                self._ensure_profile_asset_bytes(source)
                if source.get("id") != DEFAULT_THEME_ID
                else {}
            )
            self._append_theme(
                f"{source.get('name') or '主题'} 副本",
                source.get("overrides") or {},
                source=source,
                asset_bytes=asset_bytes,
            )
        except Exception as exc:
            self.validation_label.setText(f"暂无法复制主题：{exc}")

    def _delete_theme(self):
        if self._current_theme_id == DEFAULT_THEME_ID:
            return
        profile = self._profile(self._current_theme_id)
        reply = ProductMessageBox.question(
            self,
            "删除主题",
            f"删除自定义主题“{profile.get('name')}”？此操作将在保存设置后生效。",
            ProductMessageBox.Yes | ProductMessageBox.No,
            ProductMessageBox.No,
        )
        if reply != ProductMessageBox.Yes:
            return
        self._themes = [
            item for item in self._themes if item.get("id") != self._current_theme_id
        ]
        self._active_theme_id = DEFAULT_THEME_ID
        self._rebuild_theme_combo(DEFAULT_THEME_ID)
        self.changed.emit()

    def _select_default(self):
        try:
            self._store_current_editor()
            self._active_theme_id = DEFAULT_THEME_ID
            self._rebuild_theme_combo(DEFAULT_THEME_ID)
            self.changed.emit()
        except Exception as exc:
            self.validation_label.setText(f"暂无法切换主题：{exc}")

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Cowork 主题",
            "",
            "Cowork Theme (*.cowork-theme);;旧版主题 JSON (*.json)",
        )
        if not path:
            return
        append_theme_log(self.repository.data_dir, "settings_package_import_submit", source=path)
        append_theme_log(self.repository.data_dir, "settings_package_import_start", source=path)
        try:
            append_theme_log(self.repository.data_dir, "settings_package_import_run", source=path)
            normalized, asset_bytes = self.repository.read_theme_file(path, default_design_tokens())
            self._store_current_editor()
            self._append_theme(
                normalized["name"],
                normalized["overrides"],
                source=normalized,
                asset_bytes=asset_bytes,
            )
            append_theme_log(
                self.repository.data_dir,
                "settings_package_import_finish",
                source=path,
                theme_id=normalized.get("id"),
                asset_count=len(asset_bytes),
            )
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_package_import_error",
                source=path,
                error=str(exc),
            )
            ProductMessageBox.critical(self, "导入主题失败", str(exc))

    def _open_theme_folder(self):
        try:
            os.makedirs(self.repository.themes_dir, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(self.repository.themes_dir)):
                raise RuntimeError("系统未能打开主题文件夹。")
        except Exception as exc:
            ProductMessageBox.critical(self, "打开主题文件夹失败", str(exc))

    def _ensure_profile_asset_bytes(self, profile):
        if "_asset_bytes" not in profile:
            if profile.get("id") and profile.get("id") != DEFAULT_THEME_ID:
                profile["_asset_bytes"] = self.repository.get_theme_assets(
                    profile["id"], default_design_tokens()
                )
            else:
                profile["_asset_bytes"] = {}
        return profile["_asset_bytes"]

    def _add_asset(self):
        if self._current_theme_id == DEFAULT_THEME_ID:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入主题图片",
            "",
            "主题图片 (*.png *.jpg *.jpeg *.gif *.webp)",
        )
        if not path:
            return
        append_theme_log(self.repository.data_dir, "settings_asset_import_submit", source=path)
        append_theme_log(self.repository.data_dir, "settings_asset_import_start", source=path)
        try:
            profile = self._profile(self._current_theme_id)
            stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", os.path.splitext(os.path.basename(path))[0]).strip("-.") or "image"
            asset_id = stem[:72]
            index = 2
            while asset_id in (profile.get("assets") or {}):
                asset_id = f"{stem[:68]}-{index}"
                index += 1
            record, data = build_asset_record(asset_id, path)
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_import_run",
                theme_id=profile.get("id"),
                asset_id=asset_id,
            )
            profile.setdefault("assets", {})[asset_id] = record
            asset_bytes = self._ensure_profile_asset_bytes(profile)
            asset_bytes[record["path"]] = data
            self._load_profile(self._current_theme_id)
            self.validation_label.setText("图片已加入草稿；预览或保存后才会应用。")
            self.changed.emit()
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_import_finish",
                theme_id=profile.get("id"),
                asset_id=asset_id,
                bytes=len(data),
                media_type=record.get("media_type"),
                animated=bool(record.get("animation")),
                frame_count=int((record.get("animation") or {}).get("frame_count") or 1),
                duration_ms=int((record.get("animation") or {}).get("duration_ms") or 0),
            )
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_import_error",
                source=path,
                error=str(exc),
            )
            ProductMessageBox.critical(self, "导入主题图片失败", str(exc))

    def _remove_asset(self):
        item = self.asset_list.currentItem()
        if item is None or self._current_theme_id == DEFAULT_THEME_ID:
            return
        asset_id = str(item.data(Qt.UserRole) or "")
        append_theme_log(
            self.repository.data_dir,
            "settings_asset_remove_submit",
            theme_id=self._current_theme_id,
            asset_id=asset_id,
        )
        append_theme_log(
            self.repository.data_dir,
            "settings_asset_remove_start",
            theme_id=self._current_theme_id,
            asset_id=asset_id,
        )
        try:
            profile = self._profile(self._current_theme_id)
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_remove_run",
                theme_id=profile.get("id"),
                asset_id=asset_id,
            )
            references = json.dumps(
                {
                    "workspace_scene": profile.get("workspace_scene") or {},
                    "surfaces": profile.get("surfaces") or {},
                    "components": profile.get("components") or {},
                },
                ensure_ascii=False,
            )
            if f'"{asset_id}"' in references:
                raise ValueError("该图片仍被背景或图标引用，请先让 AI 移除对应引用。")
            record = (profile.get("assets") or {}).pop(asset_id, None)
            if record is None:
                raise ValueError("图片资源不存在。")
            self._ensure_profile_asset_bytes(profile).pop(record.get("path"), None)
            self._load_profile(self._current_theme_id)
            self.validation_label.setText("图片已从草稿移除。")
            self.changed.emit()
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_remove_finish",
                theme_id=profile.get("id"),
                asset_id=asset_id,
            )
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_asset_remove_error",
                theme_id=self._current_theme_id,
                asset_id=asset_id,
                error=str(exc),
            )
            ProductMessageBox.critical(self, "移除主题图片失败", str(exc))

    def _choose_color(self, editor):
        initial = QColor(editor.text().strip())
        color = QColorDialog.getColor(
            initial if initial.isValid() else QColor(DesignTokens.primary),
            self,
            "选择主题颜色",
        )
        if color.isValid():
            editor.setText(color.name(QColor.HexRgb))

    def _reload_from_directory(self):
        saved = {
            "active_theme_id": self._snapshot.active_theme_id,
            "themes": [copy.deepcopy(item) for item in self._snapshot.themes],
        }
        current = self.state_signature()
        dirty = (
            current.get("active_theme_id") != saved["active_theme_id"]
            or current.get("themes") != saved["themes"]
        )
        if dirty:
            reply = ProductMessageBox.question(
                self,
                "刷新主题列表",
                "刷新会放弃外观页尚未保存的修改，是否继续？",
                ProductMessageBox.Yes | ProductMessageBox.No,
                ProductMessageBox.No,
            )
            if reply != ProductMessageBox.Yes:
                return
        try:
            self.restore_saved_theme()
            self.validation_label.setText("已重新扫描主题文件夹。")
        except Exception as exc:
            ProductMessageBox.critical(self, "刷新主题列表失败", str(exc))

    def _export_theme(self):
        try:
            self._store_current_editor()
            profile = self._profile(self._current_theme_id)
            if not profile or profile.get("id") == DEFAULT_THEME_ID:
                raise ValueError("默认主题不能导出。")
            filename = re.sub(r'[\\/:*?"<>|]+', "-", profile.get("name") or "cowork-theme")
            path, _ = QFileDialog.getSaveFileName(
                self,
                "导出 Cowork 主题",
                filename + THEME_PACKAGE_SUFFIX,
                "Cowork Theme (*.cowork-theme)",
            )
            if not path:
                return
            if not path.lower().endswith(THEME_PACKAGE_SUFFIX):
                path += THEME_PACKAGE_SUFFIX
            append_theme_log(
                self.repository.data_dir,
                "settings_package_export_submit",
                theme_id=profile.get("id"),
                target=path,
            )
            append_theme_log(
                self.repository.data_dir,
                "settings_package_export_start",
                theme_id=profile.get("id"),
                target=path,
            )
            append_theme_log(
                self.repository.data_dir,
                "settings_package_export_run",
                theme_id=profile.get("id"),
            )
            self.repository.write_theme_record(profile, path, default_design_tokens())
            append_theme_log(
                self.repository.data_dir,
                "settings_package_export_finish",
                theme_id=profile.get("id"),
                target=path,
            )
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_package_export_error",
                theme_id=self._current_theme_id,
                error=str(exc),
            )
            ProductMessageBox.critical(self, "导出主题失败", str(exc))

    def state_signature(self):
        themes = []
        for theme in self._themes:
            persisted_theme = copy.deepcopy(theme)
            persisted_theme.pop("_asset_bytes", None)
            themes.append(persisted_theme)
        error = ""
        if not self._loading and self._current_theme_id != DEFAULT_THEME_ID:
            try:
                editor_values = self._collect_current_editor_values()
                current = next(
                    (
                        theme
                        for theme in themes
                        if theme.get("id") == self._current_theme_id
                    ),
                    None,
                )
                if current is None:
                    raise ValueError(f"主题不存在：{self._current_theme_id}")
                current.update(editor_values)
            except Exception as exc:
                error = str(exc)
        return {
            "active_theme_id": self._active_theme_id,
            "themes": themes,
            "editor_error": error,
        }

    def commit(self):
        self.last_commit_warning = ""
        self._store_current_editor()
        self.repository.clear_preview()
        self._preview_active = False
        self._preview_signature = None
        previous = self._snapshot
        persisted_themes = self.state_signature()["themes"]
        theme_changed = (
            self._active_theme_id != previous.active_theme_id
            or persisted_themes != [copy.deepcopy(item) for item in previous.themes]
        )
        append_theme_log(
            self.repository.data_dir,
            "settings_save_submit",
            active_theme_id=self._active_theme_id,
            theme_count=len(self._themes),
        )
        append_theme_log(
            self.repository.data_dir,
            "settings_save_start",
            active_theme_id=self._active_theme_id,
        )
        snapshot = None
        try:
            snapshot = self.repository.replace_state(
                themes=self._themes,
                active_theme_id=self._active_theme_id,
                default_tokens=default_design_tokens(),
                expected_revision=previous.revision,
                expected_theme_ids={
                    str(item.get("id") or "") for item in previous.themes
                },
            )
            self._snapshot = snapshot
            runtime_applied = True
            if self.runtime_manager is not None:
                self.runtime_manager.acknowledge_repository_state()
                runtime_applied = self.runtime_manager.apply_repository_state(
                    reason="settings_save",
                    persisted_on_failure=True,
                )
            if not runtime_applied:
                self.last_commit_warning = (
                    "设置和主题已保存，但当前界面刷新失败。"
                    "请重启应用以载入新主题。"
                )
            elif theme_changed:
                self.last_commit_warning = (
                    "外观设置已保存并应用。"
                    "建议重启应用，以确保新主题完整生效。"
                )
            append_theme_log(
                self.repository.data_dir,
                "settings_save_finish",
                active_theme_id=snapshot.active_theme_id,
                revision=snapshot.revision,
                runtime_applied=bool(runtime_applied),
                warning=self.last_commit_warning,
            )
            return snapshot
        except Exception as exc:
            append_theme_log(
                self.repository.data_dir,
                "settings_save_error",
                active_theme_id=self._active_theme_id,
                error=str(exc),
            )
            if snapshot is not None:
                self.repository.replace_state(
                    themes=[copy.deepcopy(item) for item in previous.themes],
                    active_theme_id=previous.active_theme_id,
                    default_tokens=default_design_tokens(),
                )
            if self.runtime_manager is not None:
                self.runtime_manager.acknowledge_repository_state()
                self.runtime_manager.apply_repository_state(reason="settings_save_rollback")
            raise

    def has_active_preview(self):
        return bool(self._preview_active)

    def restore_saved_theme(self):
        self._snapshot = self.repository.load()
        self._themes = [copy.deepcopy(item) for item in self._snapshot.themes]
        self._active_theme_id = self._snapshot.active_theme_id
        self.repository.clear_preview()
        self._preview_active = False
        self._preview_signature = None
        self._rebuild_theme_combo(self._active_theme_id)
        if self.runtime_manager is not None:
            self.runtime_manager.apply_repository_state(reason="settings_cancel")
