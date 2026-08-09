import copy
import os
import time
import weakref

import qdarktheme

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from shiboken6 import isValid as is_qt_object_valid

from .theme_service import (
    DEFAULT_THEME_ID,
    ThemeRepository,
    append_theme_log,
    configurable_theme_tokens,
    resolve_theme,
    theme_token_bounds,
    theme_token_group,
    validate_theme_manifest,
)


class DesignTokens:
    primary = "#5e6ad2"
    primary_hover = "#4f5bc4"
    primary_pressed = "#4651b7"
    primary_soft = "#eef0ff"
    primary_focus = "#c9cdf7"
    selection_bg = "#b9c0f4"
    selection_text = "#17181c"
    primary_gradient_start = "#6975dc"
    primary_gradient_end = "#5e6ad2"

    accent_ai = "#5e6ad2"
    accent_user = "#4f566b"
    accent_success = "#2f9e64"
    accent_tool = "#b7791f"

    text_primary = "#202124"
    text_secondary = "#5f6269"
    text_tertiary = "#8b8e96"
    text_inverse = "#ffffff"
    text_disabled = "#a7a9af"

    bg_app = "#f7f7f8"
    bg_main = "#ffffff"
    bg_secondary = "#f4f4f5"
    bg_tertiary = "#ececee"
    bg_sidebar = "#f2f2f3"
    bg_card = "#ffffff"
    bg_card_subtle = "#fafafa"
    bg_glass = "#ffffff"
    bg_hover = "#e9e9eb"
    bg_pressed = "#e2e2e5"
    bg_disabled = "#f0f0f1"
    bg_code = "#f6f6f7"
    bg_panel = "#ffffff"
    bg_panel_strong = "#ffffff"
    bg_sidebar_selected = "#e7e8f8"
    bg_sidebar_hover = "#e8e8ea"
    bg_chat = "#ffffff"
    bg_user_bubble = "#eef0ff"
    bg_assistant_stream = "#ffffff"
    bg_settings_nav = "#f2f2f3"
    bg_settings_nav_selected = "#e5e6f5"
    bg_settings_summary = "#f4f4fb"

    sidebar_text = "#5f6269"
    sidebar_text_muted = "#8b8e96"
    sidebar_border = "#e6e6e9"
    chat_text = "#202124"
    chat_text_muted = "#5f6269"
    chat_border = "#e8e8eb"
    composer_bg = "#ffffff"
    composer_text = "#202124"
    composer_text_muted = "#8b8e96"
    composer_border = "#e8e8eb"
    right_sidebar_bg = "#ffffff"
    right_sidebar_header_bg = "#ffffff"
    right_sidebar_text = "#202124"
    right_sidebar_text_muted = "#5f6269"
    right_sidebar_border = "#e6e6e9"
    management_bg = "#f7f7f8"
    management_panel_bg = "#ffffff"
    management_border = "#e6e6e9"
    overlay_bg = "#ffffff"
    overlay_text = "#202124"
    overlay_border = "#d8d8dc"
    preview_shell_bg = "#ffffff"
    preview_shell_toolbar_bg = "#ffffff"
    preview_shell_text = "#202124"
    preview_shell_text_muted = "#5f6269"
    preview_shell_border = "#e6e6e9"
    scrollbar_track = "#f1f1f3"
    scrollbar_thumb = "#b9bbc2"
    scrollbar_thumb_hover = "#9699a2"
    icon_primary = "#202124"
    icon_secondary = "#5f6269"
    icon_disabled = "#a7a9af"

    border = "#d8d8dc"
    border_strong = "#c5c5cb"
    separator = "#e6e6e9"
    border_subtle = "#e8e8eb"
    border_panel = "#dedee2"
    border_settings_nav = "#dfdfe3"
    border_settings_summary = "#d9dcf6"

    radius_sm = 6
    radius_md = 8
    radius_lg = 10
    radius_xl = 14
    composer_radius = 10
    preview_shell_radius = 8
    overlay_radius = 10

    # Product geometry. Keep these values on the 4 px grid so dialogs and the
    # main workspace use the same density instead of accumulating local sizes.
    control_height_sm = 28
    control_height = 32
    control_height_lg = 36
    row_height = 36
    row_height_comfortable = 44
    icon_size_sm = 14
    icon_size = 16
    icon_size_lg = 20
    scrollbar_width = 10
    chat_scrollbar_visual_width = 8
    chat_scrollbar_hit_width = 14
    chat_scrollbar_grab_padding = 6

    font_size_caption = 11
    font_size_meta = 12
    font_size_body = 14
    font_size_section = 16
    font_size_page = 20
    font_size_hero = 24
    font_weight_medium = 500
    font_weight_semibold = 600
    font_weight_bold = 700

    focus_ring_width = 2
    content_max_width = 1080
    settings_content_max_width = 1120
    management_split_threshold = 860
    settings_compact_threshold = 760

    spacing_xs = 4
    spacing_sm = 8
    spacing_md = 16
    spacing_lg = 24
    spacing_xl = 32
    spacing_2xl = 40

    shadow_sidebar = "0 18px 38px rgba(15, 23, 42, 0.07)"
    shadow_card = "0 12px 28px rgba(15, 23, 42, 0.06)"
    shadow_soft = "0 6px 16px rgba(15, 23, 42, 0.06)"

    sidebar_min_width = 204
    sidebar_width = 240
    sidebar_max_width = 320
    drawer_min_width = 272
    drawer_preferred_min_width = 360
    drawer_max_width = 500
    drawer_width_ratio = 0.28

    conversation_min_width = 840
    conversation_compact_min_width = 560
    conversation_preferred_width = 1040
    conversation_max_width = 1040
    conversation_closed_min_width = 900
    conversation_closed_max_width = 1040
    conversation_closed_target_ratio = 0.76
    conversation_open_min_width = 840
    conversation_open_compact_min_width = 560
    conversation_open_max_width = 1040
    conversation_open_target_ratio = 0.96
    conversation_open_left_spacer_ratio = 0.40

    message_min_width = 720
    message_compact_min_width = 420
    message_max_width = 880
    message_width_ratio = 0.86
    user_bubble_min_width = 620
    user_bubble_compact_min_width = 360
    user_bubble_max_width = 640
    user_bubble_ratio = 0.88

    # Conversation timeline density. Assistant stages remain individually
    # visible, but share one compact rhythm inside the same turn group.
    chat_message_vertical_margin = 2
    assistant_stage_header_height = 28
    assistant_stage_content_gap = 4
    assistant_stage_separator_vertical_margin = 8
    assistant_stage_separator_indent = 32
    assistant_stage_separator_right_margin = 8
    assistant_thinking_timeline_indent = 8
    assistant_thinking_content_indent = 14
    user_message_padding_horizontal = 12
    user_message_padding_vertical = 8
    message_action_gap = 4
    toast_min_width = 260
    toast_max_width = 720
    toast_top_margin = 16
    toast_slide_distance = 8
    toast_enter_duration_ms = 180
    toast_exit_duration_ms = 140
    toast_default_duration_ms = 6000
    toast_warning_duration_ms = 8000
    toast_error_duration_ms = 10000
    toast_edge_margin = 16
    toast_stack_gap = 8
    toast_max_visible = 3

    success_bg = "#edf8f2"
    success_text = "#247a4d"
    success_border = "#c9ead8"
    success_icon = "#247a4d"
    success_accent = "#2f9e64"

    error_bg = "#fef2f2"
    error_hover_bg = "#fde8e8"
    error_pressed_bg = "#fbd5d5"
    error_text = "#991b1b"
    error_border = "#fecaca"
    error_icon = "#991b1b"

    warning_bg = "#fffbeb"
    warning_text = "#92400e"
    warning_border = "#fde68a"
    warning_icon = "#92400e"
    warning_panel_bg = "#fff7ed"
    warning_panel_border = "#fdba74"
    warning_panel_text = "#9a3412"

    info_bg = "#f1f2fb"
    info_text = "#4f5bc4"
    info_border = "#d9dcf6"
    info_icon = "#5e6ad2"

    toast_bg = "rgba(255, 255, 255, 0.94)"
    toast_border = "#dde3ec"
    toast_shadow_alpha = 10
    toast_tint_success = "#eefaf3"
    toast_tint_error = "#fdf0f0"
    toast_tint_warning = "#fff7ea"
    toast_tint_info = "#eef5ff"

    muted_chip_bg = "#f0f0f3"
    muted_chip_text = "#636366"

    status_running = "#5e6ad2"
    status_thinking = "#7957c8"
    status_tool = "#a56a16"
    status_success = "#2f9e64"
    status_error = "#c83f49"
    status_idle = "#7d8088"

    activity_indicator_size = 14
    activity_indicator_stroke = 1.6
    activity_indicator_interval_ms = 70


_DEFAULT_DESIGN_TOKEN_VALUES = {
    name: value
    for name, value in vars(DesignTokens).items()
    if not name.startswith("_") and isinstance(value, (str, int, float))
}


def default_design_tokens():
    """Return an immutable-source copy of the built-in appearance tokens."""
    return dict(_DEFAULT_DESIGN_TOKEN_VALUES)


def theme_token_schema():
    defaults = default_design_tokens()
    schema = {}
    for name, token_type in configurable_theme_tokens(defaults).items():
        item = {
            "type": token_type,
            "group": theme_token_group(name),
            "default": defaults[name],
            "description": name.replace("_", " "),
        }
        bounds = theme_token_bounds(name, token_type)
        if bounds is not None:
            item["minimum"], item["maximum"] = bounds
        schema[name] = item
    return schema


def _runtime_theme_stylesheet(resolved):
    tokens = resolved["tokens"]
    font_family = str(resolved.get("font_family") or "Microsoft YaHei UI").replace("'", "")
    mono_family = str(resolved.get("mono_font_family") or "Consolas").replace("'", "")
    return f"""
    QMainWindow, QDialog, QWidget#MainContainer {{
        background: {tokens['management_bg']};
        color: {tokens['text_primary']};
        font-family: '{font_family}';
    }}
    QWidget#Sidebar {{
        background: {tokens['bg_sidebar']};
        color: {tokens['sidebar_text']};
        border-right: 1px solid {tokens['sidebar_border']};
    }}
    QWidget#ConversationPage, QWidget#ConversationColumn,
    QTabWidget#SessionTabs::pane {{
        background: {tokens['bg_chat']};
        color: {tokens['chat_text']};
        border: none;
    }}
    QFrame#ContentCard {{
        background: {tokens['composer_bg']};
        color: {tokens['composer_text']};
        border: 1px solid {tokens['composer_border']};
        border-radius: {tokens['composer_radius']}px;
    }}
    QFrame#RightSidebar, QWidget[themeSurface="right-sidebar"] {{
        background: {tokens['right_sidebar_bg']};
        color: {tokens['right_sidebar_text']};
        border-left: 1px solid {tokens['right_sidebar_border']};
    }}
    QWidget[themeSurface="preview-shell"] {{
        background: {tokens['preview_shell_bg']};
        color: {tokens['preview_shell_text']};
        border-color: {tokens['preview_shell_border']};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        selection-background-color: {tokens['selection_bg']};
        selection-color: {tokens['selection_text']};
    }}
    QMenu {{
        background: {tokens['overlay_bg']};
        color: {tokens['overlay_text']};
        border: 1px solid {tokens['overlay_border']};
        selection-background-color: {tokens['primary_soft']};
        selection-color: {tokens['text_primary']};
    }}
    QToolTip {{
        background-color: {tokens['bg_main']};
        color: {tokens['text_primary']};
        border: 1px solid {tokens['border']};
    }}
    QTextEdit[codeSurface="true"], QPlainTextEdit[codeSurface="true"] {{
        font-family: '{mono_family}';
    }}
    QScrollBar:vertical {{
        width: {tokens['scrollbar_width']}px;
        background: {tokens['scrollbar_track']};
        border: none;
    }}
    QScrollBar::handle:vertical {{
        min-height: 28px;
        background: {tokens['scrollbar_thumb']};
        border-radius: {max(1, int(tokens['scrollbar_width']) // 2)}px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {tokens['scrollbar_thumb_hover']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: transparent;
    }}
    """


def _theme_animation_log_fields(profile):
    animated = [
        record
        for record in ((profile or {}).get("assets") or {}).values()
        if isinstance(record, dict) and isinstance(record.get("animation"), dict)
    ]
    return {
        "animation_asset_count": len(animated),
        "animation_formats": sorted(
            {str(record.get("media_type") or "") for record in animated}
        ),
        "animation_frame_count": sum(
            int((record.get("animation") or {}).get("frame_count") or 0)
            for record in animated
        ),
        "animation_duration_ms": sum(
            int((record.get("animation") or {}).get("duration_ms") or 0)
            for record in animated
        ),
    }


class ThemeRuntimeManager(QObject):
    """UI-only adapter that applies repository state to Qt and runtime tokens."""

    themeChanged = Signal(dict)
    themeApplyFailed = Signal(str)
    previewStateChanged = Signal(object)

    def __init__(self, app, repository=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.repository = repository or ThemeRepository()
        self.current = None
        self.last_error = ""
        self.last_failure = {}
        self.binding_registry = ThemeBindingRegistry()
        self.app.theme_binding_registry = self.binding_registry
        self._last_store_stamp = None
        self._last_preview_stamp = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(700)
        self._poll_timer.timeout.connect(self.poll_external_changes)

    def start(self):
        if self.repository.clear_preview():
            append_theme_log(self.repository.data_dir, "startup_preview_discarded")
        self.apply_repository_state(reason="startup")
        self._capture_stamps()
        self._poll_timer.start()

    def stop(self):
        self._poll_timer.stop()

    def report_runtime_error(self, error, *, reason="runtime_theme"):
        """Surface a late theme rendering failure through the normal diagnostics path."""
        message = str(error or "主题运行时发生未知错误。")
        self.last_error = message
        self.last_failure = {
            "error": message,
            "reason": str(reason or "runtime_theme"),
            "theme_id": (self.current or {}).get("id", DEFAULT_THEME_ID),
            "preview": bool((self.current or {}).get("preview")),
            "saved_requires_restart": False,
        }
        append_theme_log(
            self.repository.data_dir,
            "animation_error",
            reason=self.last_failure["reason"],
            theme_id=self.last_failure["theme_id"],
            preview=self.last_failure["preview"],
            error=message,
            **_theme_animation_log_fields(self.current),
        )
        self.themeApplyFailed.emit(message)

    @staticmethod
    def _path_stamp(path):
        try:
            stat = os.stat(path)
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def _directory_stamp(self):
        try:
            items = []
            for filename in sorted(os.listdir(self.repository.themes_dir)):
                if filename == os.path.basename(self.repository.preview_path):
                    continue
                if not filename.lower().endswith((".json", ".cowork-theme")):
                    continue
                path = os.path.join(self.repository.themes_dir, filename)
                stat = os.stat(path)
                items.append((filename, stat.st_mtime_ns, stat.st_size))
            return tuple(items)
        except FileNotFoundError:
            return tuple()

    def _capture_stamps(self):
        self._last_store_stamp = self._directory_stamp()
        self._last_preview_stamp = self._path_stamp(self.repository.preview_path)

    def acknowledge_repository_state(self):
        """Mark the current repository files as an app-local, already observed change."""
        self._capture_stamps()

    def poll_external_changes(self):
        store_stamp = self._directory_stamp()
        preview_stamp = self._path_stamp(self.repository.preview_path)
        if store_stamp == self._last_store_stamp and preview_stamp == self._last_preview_stamp:
            return
        self._last_store_stamp = store_stamp
        self._last_preview_stamp = preview_stamp
        self.apply_repository_state(
            reason="external_change",
            persisted_on_failure=True,
        )

    def apply_repository_state(self, *, reason="apply", persisted_on_failure=False):
        try:
            preview = self.repository.load_preview()
            if preview:
                profile = {
                    "id": f"preview:{preview.get('preview_id')}",
                    "name": preview.get("name") or "主题预览",
                    "base": DEFAULT_THEME_ID,
                    "overrides": preview.get("overrides") or {},
                    "schema_version": int(preview.get("schema_version") or 2),
                    "assets": preview.get("assets") or {},
                    "workspace_scene": preview.get("workspace_scene") or {},
                    "surfaces": preview.get("surfaces") or {},
                    "components": preview.get("components") or {},
                    "content": preview.get("content") or {},
                    "preview_id": preview.get("preview_id"),
                    "preview_revision": int(preview.get("revision") or 1),
                }
                preview_mode = True
            else:
                snapshot = self.repository.load()
                profile = self.repository.get_theme(snapshot.active_theme_id)
                preview_mode = False
            applied = self.apply_profile(
                profile,
                preview=preview_mode,
                reason=reason,
                persisted_on_failure=bool(persisted_on_failure and not preview_mode),
            )
            if applied:
                self.previewStateChanged.emit(_json_theme_copy(preview) if preview else None)
            return applied
        except Exception as exc:
            self.last_error = str(exc)
            self.last_failure = {
                "error": str(exc),
                "reason": reason,
                "theme_id": "",
                "preview": False,
                "saved_requires_restart": False,
            }
            append_theme_log(
                self.repository.data_dir,
                "apply_error",
                reason=reason,
                error=str(exc),
            )
            self.themeApplyFailed.emit(str(exc))
            return False

    def apply_profile(
        self,
        profile,
        *,
        preview=False,
        reason="apply",
        persisted_on_failure=False,
    ):
        animation_log = _theme_animation_log_fields(profile)
        append_theme_log(
            self.repository.data_dir,
            "apply_submit",
            reason=reason,
            theme_id=(profile or {}).get("id", DEFAULT_THEME_ID),
            preview=bool(preview),
            **animation_log,
        )
        append_theme_log(
            self.repository.data_dir,
            "apply_start",
            reason=reason,
            theme_id=(profile or {}).get("id", DEFAULT_THEME_ID),
            preview=bool(preview),
            **animation_log,
        )
        previous_tokens = {
            name: getattr(DesignTokens, name)
            for name in default_design_tokens()
            if hasattr(DesignTokens, name)
        }
        previous_font = QFont(self.app.font())
        previous_stylesheet = self.app.styleSheet()
        previous_mono_family = self.app.property("themeMonoFontFamily")
        previous_palette = self.app.palette()
        previous_current = _json_theme_copy(self.current)
        try:
            if int((profile or {}).get("schema_version") or 1) == 2 and (profile or {}).get("id") != DEFAULT_THEME_ID:
                runtime_profile_id = str((profile or {}).get("id") or "")
                manifest = {
                    key: _json_theme_copy((profile or {}).get(key))
                    for key in (
                        "format", "schema_version", "id", "name", "overrides", "assets",
                        "workspace_scene", "surfaces", "components", "content",
                    )
                }
                manifest["format"] = "cowork-theme"
                if runtime_profile_id.startswith("preview:"):
                    manifest["id"] = "preview_" + runtime_profile_id.split(":", 1)[1]
                validated_manifest = validate_theme_manifest(manifest, default_design_tokens())
                validated_manifest["id"] = runtime_profile_id
                profile = {
                    **(profile or {}),
                    **validated_manifest,
                }
            resolved = resolve_theme(profile, default_design_tokens())
            append_theme_log(
                self.repository.data_dir,
                "apply_run",
                reason=reason,
                theme_id=resolved["id"],
                preview=bool(preview),
                surface_count=len((profile or {}).get("surfaces") or {}),
                scene_layer_count=len(
                    (((profile or {}).get("workspace_scene") or {}).get("layers") or [])
                ),
                preview_revision=int((profile or {}).get("preview_revision") or 0),
                component_count=len((profile or {}).get("components") or {}),
                **animation_log,
            )
            resolved["schema_version"] = int((profile or {}).get("schema_version") or 1)
            resolved["workspace_scene"] = _json_theme_copy(
                (profile or {}).get("workspace_scene") or {}
            )
            resolved["surfaces"] = _json_theme_copy((profile or {}).get("surfaces") or {})
            resolved["components"] = _json_theme_copy((profile or {}).get("components") or {})
            resolved["content"] = _json_theme_copy((profile or {}).get("content") or {})
            resolved["assets"] = _json_theme_copy((profile or {}).get("assets") or {})
            if preview:
                resolved["_asset_bytes"] = self.repository.get_preview_assets(
                    default_design_tokens()
                )
            elif resolved["id"] != DEFAULT_THEME_ID:
                resolved["_asset_bytes"] = self.repository.get_theme_assets(
                    resolved["id"],
                    default_design_tokens(),
                )
            else:
                resolved["_asset_bytes"] = {}
            installed = {name.casefold(): name for name in QFontDatabase.families()}
            for field in ("font_family", "mono_font_family"):
                family = str(resolved.get(field) or "").strip()
                if family.casefold() not in installed:
                    raise ValueError(f"系统未安装主题字体：{family}")
            for name, value in resolved["tokens"].items():
                if hasattr(DesignTokens, name):
                    setattr(DesignTokens, name, value)
            app_font = QFont(resolved["font_family"])
            app_font.setPixelSize(int(resolved["tokens"]["font_size_body"]))
            self.app.setFont(app_font)
            self.app.setProperty("themeMonoFontFamily", resolved["mono_font_family"])
            self.app.setStyleSheet(_runtime_theme_stylesheet(resolved))
            apply_tooltip_palette(self.app)
            self.current = dict(resolved)
            self.current["preview"] = bool(preview)
            if preview:
                self.current["preview_id"] = (profile or {}).get("preview_id")
                self.current["preview_revision"] = int(
                    (profile or {}).get("preview_revision") or 1
                )
            self.last_error = ""
            self.last_failure = {}
            self._refresh_existing_widgets()
            self.themeChanged.emit(dict(self.current))
            append_theme_log(
                self.repository.data_dir,
                "apply_finish",
                reason=reason,
                theme_id=resolved["id"],
                preview=bool(preview),
                refreshed_controls=int(self.current.get("_binding_count") or 0),
                surface_elapsed_ms=self.current.get("_binding_surface_elapsed_ms") or {},
                **animation_log,
            )
            return True
        except Exception as exc:
            self.last_error = str(exc)
            for name, value in previous_tokens.items():
                setattr(DesignTokens, name, value)
            self.app.setFont(previous_font)
            self.app.setProperty("themeMonoFontFamily", previous_mono_family)
            self.app.setStyleSheet(previous_stylesheet)
            self.app.setPalette(previous_palette)
            self.current = previous_current
            self.last_failure = {
                "error": str(exc),
                "reason": reason,
                "theme_id": (profile or {}).get("id", DEFAULT_THEME_ID),
                "preview": bool(preview),
                "saved_requires_restart": bool(persisted_on_failure and not preview),
            }
            recovery_error = ""
            try:
                self._refresh_existing_widgets()
            except Exception as recovery_exc:
                recovery_error = str(recovery_exc)
            append_theme_log(
                self.repository.data_dir,
                "apply_error",
                reason=reason,
                theme_id=(profile or {}).get("id", DEFAULT_THEME_ID),
                preview=bool(preview),
                error=str(exc),
                recovery_error=recovery_error,
                **animation_log,
            )
            self.themeApplyFailed.emit(str(exc))
            return False

    def _refresh_existing_widgets(self):
        revision = int(time.time() * 1000)
        bound_ids = self.binding_registry.apply(self.current or {})
        for top_level in self.app.topLevelWidgets():
            candidates = [top_level, *top_level.findChildren(QObject)]
            for candidate in candidates:
                if id(candidate) in bound_ids:
                    continue
                refresh = getattr(candidate, "refresh_theme", None)
                if callable(refresh):
                    refresh()
            top_level.setProperty("themeRevision", revision)
            style = top_level.style()
            style.unpolish(top_level)
            style.polish(top_level)
            top_level.update()

    def restore_saved_theme(self, *, reason="preview_restore"):
        self.repository.clear_preview()
        applied = self.apply_repository_state(reason=reason)
        if applied:
            self.previewStateChanged.emit(None)
        return applied

    def commit_current_preview(self, *, activate=True, reason="preview_commit"):
        preview = self.repository.load_preview()
        if not preview:
            raise ValueError("当前没有主题预览。")
        result = self.repository.commit_preview(
            preview_id=preview["preview_id"],
            preview_revision=int(preview.get("revision") or 1),
            activate=bool(activate),
            default_tokens=default_design_tokens(),
        )
        if not self.apply_repository_state(
            reason=reason,
            persisted_on_failure=True,
        ):
            raise ValueError("主题已保存，但当前界面刷新失败；请重启应用以载入新主题。")
        self.previewStateChanged.emit(None)
        return result


def _json_theme_copy(value):
    return copy.deepcopy(value) if value is not None else None


class ThemeBindingRegistry:
    """Weak runtime bindings for styles, icons, geometry, and custom painters."""

    def __init__(self):
        self._bindings = {}

    def bind(self, widget, callback, *, surface="global"):
        if widget is None or not callable(callback):
            raise ValueError("主题绑定需要控件和可调用回调。")
        key = id(widget)
        widget_ref = weakref.ref(
            widget,
            lambda _ref, binding_key=key: self._bindings.pop(binding_key, None),
        )
        callback_ref = (
            weakref.WeakMethod(callback)
            if getattr(callback, "__self__", None) is not None
            else callback
        )
        descriptor = (
            f"{widget.metaObject().className()}#{widget.objectName() or '-'}"
        )
        self._bindings[key] = (
            widget_ref,
            callback_ref,
            str(surface or "global"),
            descriptor,
        )
        widget.destroyed.connect(
            lambda _object=None, binding_key=key: self._bindings.pop(
                binding_key,
                None,
            )
        )
        return widget

    def apply(self, resolved):
        applied_ids = set()
        failures = []
        surface_elapsed = {}
        surface_counts = {}
        started = time.perf_counter()
        for key, (
            widget_ref,
            callback_ref,
            surface,
            descriptor,
        ) in list(self._bindings.items()):
            widget = widget_ref()
            callback = callback_ref() if isinstance(callback_ref, weakref.WeakMethod) else callback_ref
            if (
                widget is None
                or callback is None
                or not is_qt_object_valid(widget)
            ):
                self._bindings.pop(key, None)
                continue
            try:
                callback_started = time.perf_counter()
                callback(resolved)
                applied_ids.add(key)
                surface_elapsed[surface] = surface_elapsed.get(surface, 0.0) + (
                    time.perf_counter() - callback_started
                ) * 1000
                surface_counts[surface] = surface_counts.get(surface, 0) + 1
            except Exception as exc:
                failures.append(f"{surface}:{descriptor}: {exc}")
        if failures:
            raise RuntimeError("主题绑定刷新失败：" + " | ".join(failures[:8]))
        if resolved is not None:
            resolved["_binding_count"] = len(applied_ids)
            resolved["_binding_elapsed_ms"] = round(
                (time.perf_counter() - started) * 1000,
                2,
            )
            resolved["_binding_surface_elapsed_ms"] = {
                name: round(value, 2) for name, value in surface_elapsed.items()
            }
            resolved["_binding_surface_counts"] = surface_counts
        return applied_ids


def bind_theme(widget, callback=None, *, surface="global"):
    """Register a live theme callback when the application manager is available."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    registry = getattr(app, "theme_binding_registry", None) if app is not None else None
    target = callback or getattr(widget, "refresh_theme", None)
    if registry is not None and callable(target):
        registry.bind(widget, target, surface=surface)
        manager = getattr(app, "theme_manager", None)
        current = getattr(manager, "current", None)
        if current is not None:
            target(current)
    return widget


def get_tech_stylesheet(theme="light"):
    is_dark = theme == "dark"

    if is_dark:
        c_bg_main = "#0d1117"
        c_bg_sidebar = "#010409"
        c_bg_card = "#161b22"
        c_bg_input = "#0d1117"
        c_text_primary = "#e6edf3"
        c_text_secondary = "#8b949e"
        c_text_tertiary = "#484f58"
        c_accent = "#0a84ff"
        c_accent_hover = "#409cff"
        c_border = "#30363d"
        c_selection = "#1f6feb"
    else:
        c_bg_main = DesignTokens.bg_main
        c_bg_sidebar = DesignTokens.bg_sidebar
        c_bg_card = DesignTokens.bg_card
        c_bg_input = DesignTokens.bg_secondary
        c_text_primary = DesignTokens.text_primary
        c_text_secondary = DesignTokens.text_secondary
        c_text_tertiary = DesignTokens.text_tertiary
        c_accent = DesignTokens.primary
        c_accent_hover = DesignTokens.primary_hover
        c_border = DesignTokens.border
        c_selection = DesignTokens.selection_bg

    css = f"""
    QWidget {{
        font-family: 'Microsoft YaHei UI', 'Segoe UI Variable', 'Segoe UI', sans-serif;
        font-size: 14px;
        color: {c_text_primary};
        selection-background-color: {c_selection};
        selection-color: {DesignTokens.selection_text};
    }}

    QMainWindow, QWidget#MainContainer {{
        background-color: {c_bg_main};
    }}

    QWidget#Sidebar, QWidget#RightSidebar {{
        background-color: {c_bg_sidebar};
    }}

    QFrame#ContentCard, QFrame#SkillCard, QFrame#PanelCard {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_lg}px;
    }}

    QPushButton {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_md}px;
        padding: 7px 12px;
        color: {c_text_primary};
        text-align: center;
    }}
    QPushButton:hover {{
        border-color: {DesignTokens.border_strong};
        background-color: {DesignTokens.bg_secondary};
    }}

    QPushButton#PrimaryBtn {{
        background-color: {c_accent};
        color: #ffffff;
        border: 1px solid {c_accent};
        font-weight: 600;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {c_accent_hover};
        border-color: {c_accent_hover};
    }}
    QPushButton#PrimaryBtn:pressed {{
        background-color: {DesignTokens.primary_pressed};
        border-color: {DesignTokens.primary_pressed};
    }}
    QPushButton:disabled {{
        background-color: {DesignTokens.bg_disabled};
        color: {DesignTokens.text_disabled};
        border-color: {DesignTokens.border_subtle};
    }}

    QPushButton#SecondaryBtn {{
        background-color: {DesignTokens.bg_main};
        color: {c_text_primary};
        border: 1px solid {c_border};
    }}

    QPushButton#GhostBtn {{
        background-color: transparent;
        border: none;
        color: {c_text_secondary};
    }}
    QPushButton#GhostBtn:hover {{
        color: {c_accent};
        background-color: {DesignTokens.bg_secondary};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
        background-color: {c_bg_input};
        border: 1px solid {c_border};
        border-radius: {DesignTokens.radius_md}px;
        padding: 7px 9px;
        color: {c_text_primary};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {c_accent};
        background-color: {c_bg_card};
    }}

    QTextEdit#MainInput {{
        font-size: 15px;
        border-radius: {DesignTokens.radius_lg}px;
        padding: 12px 16px;
        background-color: {c_bg_card};
    }}

    QTreeView, QListView, QListWidget {{
        background-color: {DesignTokens.bg_main};
        border: none;
        outline: none;
    }}
    QTreeView::item, QListWidget::item {{
        padding: 5px;
        border-radius: {DesignTokens.radius_sm}px;
        margin: 2px 4px;
    }}
    QTreeView::item:selected, QListWidget::item:selected {{
        background-color: {DesignTokens.primary_soft};
        color: {c_text_primary};
        border: 1px solid {c_accent};
    }}

    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background: transparent;
        padding: 8px 14px;
        margin-right: 4px;
        color: {c_text_secondary};
        font-weight: 500;
        border-radius: {DesignTokens.radius_sm}px;
    }}
    QTabBar::tab:hover {{
        color: {c_text_primary};
    }}
    QTabBar::tab:selected {{
        color: {c_text_primary};
        background: {DesignTokens.primary_soft};
    }}

    QScrollBar:vertical {{
        border: none;
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c_text_tertiary}55;
        min-height: 28px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c_text_tertiary}88;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        border: none;
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c_text_tertiary}55;
        min-width: 28px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    QLabel[roleTitle="true"] {{
        font-size: 18px;
        font-weight: 700;
        color: {c_text_primary};
    }}
    QLabel[roleSubtitle="true"] {{
        font-size: 13px;
        color: {c_text_secondary};
    }}

    QMenu {{
        background-color: {c_bg_card};
        border: 1px solid {c_border};
        padding: 8px;
        border-radius: {DesignTokens.radius_lg}px;
    }}
    QMenu::item {{
        padding: 7px 24px 7px 12px;
        border-radius: {DesignTokens.radius_sm}px;
    }}
    QMenu::item:selected {{
        background-color: {c_accent};
        color: #ffffff;
    }}

    QToolTip {{
        background-color: {c_bg_main};
        color: {c_text_primary};
        border: 1px solid {c_border};
    }}
    """
    return css


def apply_theme(app, theme="auto"):
    mode = "light"
    base_sheet = qdarktheme.load_stylesheet(mode)
    tech_sheet = get_tech_stylesheet(mode)
    app.setStyleSheet(base_sheet + "\n" + tech_sheet)
    # Native tooltip windows still read palette roles on Windows. Keep these
    # colors identical to the deliberately minimal QToolTip stylesheet above;
    # translucent or rounded tooltip rules can render as black native windows.
    apply_tooltip_palette(app)


def apply_tooltip_palette(app):
    from PySide6.QtGui import QColor, QPalette
    palette = app.palette()
    palette.setColor(QPalette.ToolTipBase, QColor(DesignTokens.bg_main))
    palette.setColor(QPalette.ToolTipText, QColor(DesignTokens.text_primary))
    app.setPalette(palette)


def apply_tooltip_theme(app):
    """Apply only the Windows-safe tooltip surface without restyling the UI."""
    app.setStyleSheet(
        f"QToolTip {{ background-color: {DesignTokens.bg_main}; "
        f"color: {DesignTokens.text_primary}; border: 1px solid {DesignTokens.border}; }}"
    )
    apply_tooltip_palette(app)
