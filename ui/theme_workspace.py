from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any, Callable

import qtawesome as qta
from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMovie, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.theme import DesignTokens
from core.theme_package import CONTENT_DEFAULTS, PROTECTED_COMPONENTS


_COMPOSITION_MODES = {
    "source_over": QPainter.CompositionMode_SourceOver,
    "multiply": QPainter.CompositionMode_Multiply,
    "screen": QPainter.CompositionMode_Screen,
    "overlay": QPainter.CompositionMode_Overlay,
}


def _color(value: str, opacity: float = 1.0) -> QColor:
    result = QColor(str(value or DesignTokens.text_primary))
    result.setAlphaF(max(0.0, min(1.0, result.alphaF() * float(opacity))))
    return result


def apply_theme_component_visibility(widget: QWidget, visible: bool) -> None:
    """Apply declarative visibility without promoting an unattached widget."""
    visible = bool(visible)
    widget._theme_component_desired_visible = visible
    if widget.parentWidget() is None and widget.isWindow():
        if not visible:
            widget.setVisible(False)
        return
    widget.setVisible(visible)


class WorkspaceSceneCanvas(QWidget):
    """The only image and procedural-background owner in the workspace."""

    animationFailed = Signal(str)

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.layers: list[dict[str, Any]] = []
        self.asset_bytes: dict[str, bytes] = {}
        self.asset_records: dict[str, dict[str, Any]] = {}
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._render_cache: dict[tuple, QPixmap] = {}
        self._animation_buffers: dict[str, QBuffer] = {}
        self._animation_movies: dict[str, QMovie] = {}
        self._animation_started: set[str] = set()
        self._animation_finished: set[str] = set()
        self._animation_generation = 0
        self._window_host = host.window()
        self.revision = ""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)
        host.installEventFilter(self)
        if self._window_host is not host:
            self._window_host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.lower()
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.host:
            if event.type() in {QEvent.Resize, QEvent.Show, QEvent.LayoutRequest}:
                self.setGeometry(self.host.rect())
                self.lower()
            if event.type() in {QEvent.Show, QEvent.Hide, QEvent.WindowStateChange}:
                self._sync_animation_state()
        elif watched is self._window_host and event.type() in {
            QEvent.Show,
            QEvent.Hide,
            QEvent.WindowStateChange,
        }:
            self._sync_animation_state()
        return super().eventFilter(watched, event)

    def set_scene(self, scene, asset_records, asset_bytes, *, revision=""):
        self._dispose_animations()
        layers = (scene or {}).get("layers") or []
        self.layers = copy.deepcopy(list(layers or []))
        self.asset_records = copy.deepcopy(dict(asset_records or {}))
        self.asset_bytes = dict(asset_bytes or {})
        self.revision = str(revision or "")
        self._pixmap_cache.clear()
        self._render_cache.clear()
        for layer in self.layers:
            if layer.get("type") == "image":
                asset_id = str(layer.get("asset") or "")
                record = self.asset_records.get(asset_id) or {}
                if record.get("animation"):
                    self._create_animation(asset_id)
                else:
                    self._pixmap(asset_id)
        self.setVisible(bool(self.layers))
        self.lower()
        self._sync_animation_state()
        self.update()

    def _dispose_animations(self):
        self._animation_generation += 1
        for movie in self._animation_movies.values():
            movie.stop()
            try:
                movie.frameChanged.disconnect()
                movie.finished.disconnect()
                movie.error.disconnect()
            except (RuntimeError, TypeError):
                pass
            movie.deleteLater()
        for buffer in self._animation_buffers.values():
            buffer.close()
            buffer.deleteLater()
        self._animation_movies.clear()
        self._animation_buffers.clear()
        self._animation_started.clear()
        self._animation_finished.clear()

    def _create_animation(self, asset_id: str):
        record = self.asset_records.get(asset_id) or {}
        data = self.asset_bytes.get(record.get("path"), b"")
        if not data:
            raise ValueError(f"主题动态背景资产为空：{asset_id}")
        media_type = str(record.get("media_type") or "")
        movie_format = {
            "image/gif": b"gif",
            "image/webp": b"webp",
        }.get(media_type)
        if movie_format is None:
            raise ValueError(f"主题动态背景格式无效：{asset_id}")
        if movie_format not in {bytes(item) for item in QMovie.supportedFormats()}:
            raise ValueError(f"当前运行环境不支持主题动态背景格式：{media_type}")
        buffer = QBuffer(self)
        buffer.setData(QByteArray(data))
        if not buffer.open(QIODevice.ReadOnly):
            buffer.deleteLater()
            raise ValueError(f"主题动态背景资产无法读取：{asset_id}")
        movie = QMovie(buffer, movie_format, self)
        movie.setCacheMode(QMovie.CacheNone)
        if not movie.isValid() or not movie.jumpToFrame(0) or movie.currentPixmap().isNull():
            detail = movie.lastErrorString()
            movie.deleteLater()
            buffer.close()
            buffer.deleteLater()
            raise ValueError(f"主题动态背景资产无法解码：{asset_id}；{detail}")
        generation = self._animation_generation
        movie.frameChanged.connect(
            lambda _frame, current=asset_id, current_generation=generation: self._on_animation_frame(
                current,
                current_generation,
            )
        )
        movie.finished.connect(
            lambda current=asset_id, current_generation=generation: self._on_animation_finished(
                current,
                current_generation,
            )
        )
        movie.error.connect(
            lambda _error, current=asset_id, current_generation=generation: self._on_animation_error(
                current,
                current_generation,
            )
        )
        self._animation_buffers[asset_id] = buffer
        self._animation_movies[asset_id] = movie

    def _on_animation_frame(self, asset_id: str, generation: int):
        if generation != self._animation_generation or asset_id not in self._animation_movies:
            return
        self._render_cache.clear()
        self.update()

    def _on_animation_finished(self, asset_id: str, generation: int):
        if generation == self._animation_generation:
            self._animation_finished.add(asset_id)

    def _on_animation_error(self, asset_id: str, generation: int):
        if generation != self._animation_generation:
            return
        movie = self._animation_movies.get(asset_id)
        if movie is None:
            return
        detail = movie.lastErrorString()
        frame_count = int(
            ((self.asset_records.get(asset_id) or {}).get("animation") or {}).get("frame_count")
            or 0
        )
        # With CacheNone, Qt reports UnknownError while rewinding a fully
        # validated sequential GIF/WebP device. Playback remains Running and
        # immediately advances to frame zero; this is a loop boundary, not a
        # decoder failure.
        if (
            detail == "Unknown error"
            and movie.state() == QMovie.Running
            and frame_count > 1
            and movie.currentFrameNumber() == frame_count - 1
        ):
            return
        movie.stop()
        self._animation_finished.add(asset_id)
        self.animationFailed.emit(f"主题动态背景播放失败：{asset_id}；{detail}")

    def _sync_animation_state(self):
        if not self._animation_movies:
            return
        window = self.window()
        should_play = bool(
            self.layers
            and self.isVisible()
            and self.host.isVisible()
            and (window is None or not window.isMinimized())
        )
        for asset_id, movie in self._animation_movies.items():
            if asset_id in self._animation_finished:
                continue
            if should_play:
                if asset_id not in self._animation_started:
                    self._animation_started.add(asset_id)
                    movie.start()
                elif movie.state() == QMovie.Paused:
                    movie.setPaused(False)
            elif movie.state() == QMovie.Running:
                movie.setPaused(True)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_animation_state()

    def hideEvent(self, event):
        for movie in self._animation_movies.values():
            if movie.state() == QMovie.Running:
                movie.setPaused(True)
        super().hideEvent(event)

    def _pixmap(self, asset_id: str) -> QPixmap:
        record = self.asset_records.get(asset_id) or {}
        digest = str(record.get("sha256") or asset_id)
        cached = self._pixmap_cache.get(digest)
        if cached is not None:
            return cached
        data = self.asset_bytes.get(record.get("path"), b"")
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            raise ValueError(f"主题背景资产无法解码：{asset_id}")
        self._pixmap_cache[digest] = pixmap
        return pixmap

    def _image_pixmap(self, asset_id: str) -> QPixmap:
        movie = self._animation_movies.get(asset_id)
        if movie is None:
            return self._pixmap(asset_id)
        pixmap = movie.currentPixmap()
        if pixmap.isNull():
            raise ValueError(f"主题动态背景当前帧无法解码：{asset_id}")
        return pixmap

    @staticmethod
    def _image_target(source: QSize, target: QRect, fit: str, focal_x: float, focal_y: float):
        source_width = max(1, source.width())
        source_height = max(1, source.height())
        if fit == "stretch":
            return target, QRect(0, 0, source_width, source_height)
        if fit == "center":
            width = min(source_width, target.width())
            height = min(source_height, target.height())
            x = target.x() + (target.width() - width) // 2
            y = target.y() + (target.height() - height) // 2
            source_x = max(0, (source_width - width) // 2)
            source_y = max(0, (source_height - height) // 2)
            return QRect(x, y, width, height), QRect(source_x, source_y, width, height)
        contain = fit == "contain"
        scale = min(target.width() / source_width, target.height() / source_height) if contain else max(
            target.width() / source_width,
            target.height() / source_height,
        )
        scaled_width = max(1, round(source_width * scale))
        scaled_height = max(1, round(source_height * scale))
        if contain:
            destination = QRect(
                target.x() + (target.width() - scaled_width) // 2,
                target.y() + (target.height() - scaled_height) // 2,
                scaled_width,
                scaled_height,
            )
            return destination, QRect(0, 0, source_width, source_height)
        visible_width = min(source_width, round(target.width() / scale))
        visible_height = min(source_height, round(target.height() / scale))
        source_x = round((source_width - visible_width) * focal_x)
        source_y = round((source_height - visible_height) * focal_y)
        return target, QRect(source_x, source_y, visible_width, visible_height)

    def _paint_image(self, painter: QPainter, layer: dict):
        pixmap = self._image_pixmap(layer["asset"])
        target = self.rect()
        fit = layer.get("fit", "cover")
        destination, source = self._image_target(
            pixmap.size(),
            target,
            fit,
            float(layer.get("focal_x", 0.5)),
            float(layer.get("focal_y", 0.5)),
        )
        painter.drawPixmap(destination, pixmap, source)
        if layer.get("tint"):
            painter.fillRect(target, _color(layer["tint"], 0.35))

    def _paint_pattern(self, painter: QPainter, layer: dict):
        pattern_type = layer["type"]
        color = _color(layer.get("color"))
        spacing = max(2, int(layer.get("spacing", 16)))
        line_width = max(1, int(layer.get("line_width", 1)))
        painter.setPen(QPen(color, line_width))
        rect = self.rect()
        if pattern_type == "solid":
            painter.fillRect(rect, color)
        elif pattern_type == "grid":
            major_every = max(0, int(layer.get("major_every", 0)))
            major_color = _color(layer.get("major_color") or layer.get("color"))
            major_width = max(1, int(layer.get("major_line_width", line_width)))
            for index, x in enumerate(range(0, rect.right() + 1, spacing)):
                painter.setPen(
                    QPen(major_color, major_width)
                    if major_every and index % major_every == 0
                    else QPen(color, line_width)
                )
                painter.drawLine(x, rect.top(), x, rect.bottom())
            for index, y in enumerate(range(0, rect.bottom() + 1, spacing)):
                painter.setPen(
                    QPen(major_color, major_width)
                    if major_every and index % major_every == 0
                    else QPen(color, line_width)
                )
                painter.drawLine(rect.left(), y, rect.right(), y)
        elif pattern_type == "dots":
            radius = max(1, line_width)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            for x in range(rect.left(), rect.right() + 1, spacing):
                for y in range(rect.top(), rect.bottom() + 1, spacing):
                    painter.drawEllipse(QPoint(x, y), radius, radius)
        elif pattern_type == "stripes":
            diagonal = int(math.hypot(rect.width(), rect.height())) + spacing * 2
            painter.save()
            painter.translate(rect.center())
            painter.rotate(float(layer.get("angle", 45)))
            for offset in range(-diagonal, diagonal + 1, spacing):
                painter.drawLine(offset, -diagonal, offset, diagonal)
            painter.restore()
        elif pattern_type == "noise":
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            size = max(1, int(layer.get("size", 8)))
            generator = random.Random(
                json.dumps(layer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            count = min(12000, max(100, rect.width() * rect.height() // max(8, size * size)))
            for _ in range(count):
                painter.drawRect(
                    generator.randrange(max(1, rect.width())),
                    generator.randrange(max(1, rect.height())),
                    1,
                    1,
                )

    def _rendered_scene(self) -> QPixmap:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        assets_key = tuple(
            sorted((asset_id, str(record.get("sha256") or "")) for asset_id, record in self.asset_records.items())
        )
        animation_key = tuple(
            sorted(
                (asset_id, movie.currentFrameNumber())
                for asset_id, movie in self._animation_movies.items()
            )
        )
        scene_digest = hashlib.sha256(
            json.dumps(self.layers, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        key = (
            self.revision,
            assets_key,
            animation_key,
            scene_digest,
            self.width(),
            self.height(),
            round(dpr, 3),
        )
        cached = self._render_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            for layer in self.layers:
                painter.save()
                painter.setCompositionMode(_COMPOSITION_MODES.get(layer.get("blend"), QPainter.CompositionMode_SourceOver))
                painter.setOpacity(float(layer.get("opacity", 1)))
                if layer.get("type") == "image":
                    self._paint_image(painter, layer)
                else:
                    self._paint_pattern(painter, layer)
                painter.restore()
        finally:
            painter.end()
        self._render_cache = {key: pixmap}
        return pixmap

    def paintEvent(self, event):
        if not self.layers:
            return
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, self._rendered_scene())
        finally:
            painter.end()


@dataclass
class _SurfaceBinding:
    widget: QWidget
    default_stylesheet: str


@dataclass
class _ComponentBinding:
    widget: QWidget
    layout: QLayout | None
    default_visible: bool
    default_stylesheet: str
    default_icon: QIcon | None
    default_pixmap: QPixmap | None
    default_minimum: QSize
    default_maximum: QSize
    default_index: int
    default_grid: tuple[int, int, int, int] | None


class WorkspaceThemeController:
    """Applies declarative presentation without owning behavior or QObject lifetime."""

    def __init__(self):
        self.scene_canvas: WorkspaceSceneCanvas | None = None
        self.surfaces: dict[str, _SurfaceBinding] = {}
        self.components: dict[str, _ComponentBinding] = {}
        self.content_handlers: dict[str, Callable[[str], None]] = {}
        self._last_resolved: dict[str, Any] | None = None

    def register_scene_host(self, widget: QWidget):
        if self.scene_canvas is not None:
            raise ValueError("工作区场景宿主只能注册一次。")
        self.scene_canvas = WorkspaceSceneCanvas(widget)
        return widget

    def register_surface(self, surface_id: str, widget: QWidget):
        if surface_id in self.surfaces:
            raise ValueError(f"主题区域重复注册：{surface_id}")
        self.surfaces[surface_id] = _SurfaceBinding(widget, widget.styleSheet())
        return widget

    def register_component(self, component_id: str, widget: QWidget, layout: QLayout | None = None):
        if component_id in self.components:
            raise ValueError(f"主题组件重复注册：{component_id}")
        default_icon = QIcon(widget.icon()) if isinstance(widget, (QPushButton, QToolButton)) else None
        default_pixmap = (
            QPixmap(widget.pixmap())
            if isinstance(widget, QLabel) and widget.pixmap() is not None
            else None
        )
        index = layout.indexOf(widget) if layout is not None else -1
        grid = None
        if isinstance(layout, QGridLayout) and index >= 0:
            grid = layout.getItemPosition(index)
        self.components[component_id] = _ComponentBinding(
            widget=widget,
            layout=layout,
            default_visible=not widget.isHidden(),
            default_stylesheet=widget.styleSheet(),
            default_icon=default_icon,
            default_pixmap=default_pixmap,
            default_minimum=widget.minimumSize(),
            default_maximum=widget.maximumSize(),
            default_index=index,
            default_grid=grid,
        )
        return widget

    def register_content(self, key: str, handler: Callable[[str], None]):
        self.content_handlers[key] = handler

    @staticmethod
    def _style_sheet(widget: QWidget, style: dict, base: str) -> str:
        if not style:
            return base
        if not widget.objectName():
            widget.setObjectName("ThemeComponent_" + str(id(widget)))
        selector = f"{widget.metaObject().className()}#{widget.objectName()}"
        declarations = []
        mapping = {
            "foreground": "color",
            "background": "background-color",
            "border_color": "border-color",
            "border_width": "border-width",
            "radius": "border-radius",
            "font_size": "font-size",
            "font_weight": "font-weight",
            "padding": "padding",
        }
        px_fields = {"border_width", "radius", "font_size", "padding"}
        for key, css_name in mapping.items():
            if key in style:
                suffix = "px" if key in px_fields else ""
                declarations.append(f"{css_name}: {style[key]}{suffix}")
        if "border_color" in style and "border_width" not in style:
            declarations.append("border-width: 1px")
        if "border_color" in style:
            declarations.append("border-style: solid")
        if "{" not in base:
            return base.rstrip().rstrip(";") + "; " + "; ".join(declarations) + ";"
        return base + f"\n{selector} {{ {'; '.join(declarations)}; }}"

    @classmethod
    def surface_style_sheet(cls, widget: QWidget, surface: dict, base: str) -> str:
        result = cls._style_sheet(widget, surface.get("style") or {}, base)
        material = surface.get("material") or {}
        if not material:
            return result
        if not widget.objectName():
            widget.setObjectName("ThemeSurface_" + str(id(widget)))
        selector = f"{widget.metaObject().className()}#{widget.objectName()}"
        kind = material.get("kind", "transparent")
        if kind == "transparent":
            color = "transparent"
        else:
            parsed = _color(material.get("color"), float(material.get("opacity", 1)))
            color = f"rgba({parsed.red()}, {parsed.green()}, {parsed.blue()}, {parsed.alpha()})"
        return result + f"\n{selector} {{ background-color: {color}; }}"

    @staticmethod
    def _apply_size(
        widget: QWidget,
        style: dict,
        binding: _ComponentBinding,
        base: dict[str, Any] | None = None,
    ):
        minimum = (base or {}).get("minimum", binding.default_minimum)
        maximum = (base or {}).get("maximum", binding.default_maximum)
        min_width = int(style.get("min_width", minimum.width()))
        min_height = int(style.get("min_height", minimum.height()))
        max_width = int(style.get("max_width", maximum.width()))
        max_height = int(style.get("max_height", maximum.height()))
        widget.setMinimumSize(min_width, min_height)
        widget.setMaximumSize(max_width, max_height)

    @staticmethod
    def _restore_layout(binding: _ComponentBinding):
        layout = binding.layout
        if layout is None or binding.default_index < 0:
            return
        layout.removeWidget(binding.widget)
        if isinstance(layout, QGridLayout) and binding.default_grid:
            row, column, row_span, column_span = binding.default_grid
            layout.addWidget(binding.widget, row, column, row_span, column_span)
        elif isinstance(layout, (QHBoxLayout, QVBoxLayout)):
            layout.insertWidget(min(binding.default_index, layout.count()), binding.widget)

    @staticmethod
    def _apply_layout(binding: _ComponentBinding, spec: dict):
        layout = binding.layout
        if not spec or layout is None:
            return
        alignment = {
            "start": Qt.AlignLeft | Qt.AlignVCenter,
            "center": Qt.AlignCenter,
            "end": Qt.AlignRight | Qt.AlignVCenter,
            "stretch": Qt.Alignment(),
        }.get(str(spec.get("alignment") or "stretch"), Qt.Alignment())
        layout.removeWidget(binding.widget)
        if isinstance(layout, QGridLayout):
            default = binding.default_grid or (0, 0, 1, 1)
            row = int(spec.get("row", default[0]))
            column = int(spec.get("column", default[1]))
            row_span = int(spec.get("row_span", default[2]))
            column_span = int(spec.get("column_span", default[3]))
            layout.addWidget(binding.widget, row, column, row_span, column_span, alignment)
        elif isinstance(layout, (QHBoxLayout, QVBoxLayout)):
            if "order" in spec:
                order = int(spec["order"])
            else:
                slot = str(spec.get("slot") or "")
                order = {
                    "start": 0,
                    "center": layout.count() // 2,
                    "end": layout.count(),
                    "primary": binding.default_index,
                    "secondary": binding.default_index,
                }.get(slot, binding.default_index)
            layout.insertWidget(
                max(0, min(order, layout.count())),
                binding.widget,
                0,
                alignment,
            )

    def _reset(
        self,
        surface_bases: dict[str, str] | None = None,
        component_bases: dict[str, dict[str, Any]] | None = None,
    ):
        if self.scene_canvas is not None:
            self.scene_canvas.set_scene({}, {}, {}, revision="reset")
        for surface_id, binding in self.surfaces.items():
            binding.widget.setStyleSheet(
                (surface_bases or {}).get(surface_id, binding.default_stylesheet)
            )
        for component_id, binding in self.components.items():
            self._restore_layout(binding)
            base = (component_bases or {}).get(component_id) or {}
            binding.widget.setVisible(bool(base.get("visible", binding.default_visible)))
            binding.widget.setStyleSheet(str(base.get("stylesheet", binding.default_stylesheet)))
            binding.widget.setMinimumSize(base.get("minimum", binding.default_minimum))
            binding.widget.setMaximumSize(base.get("maximum", binding.default_maximum))
            icon = base.get("icon", binding.default_icon)
            if icon is not None and isinstance(binding.widget, (QPushButton, QToolButton)):
                binding.widget.setIcon(icon)
            pixmap = base.get("pixmap")
            if isinstance(binding.widget, QLabel):
                pixmap = pixmap if pixmap is not None else binding.default_pixmap
                if pixmap is not None:
                    binding.widget.setPixmap(pixmap)
        for key, handler in self.content_handlers.items():
            handler(CONTENT_DEFAULTS.get(key, ""))

    def _apply(
        self,
        resolved: dict[str, Any],
        surface_bases: dict[str, str] | None = None,
        component_bases: dict[str, dict[str, Any]] | None = None,
    ):
        self._reset(surface_bases, component_bases)
        assets = resolved.get("assets") or {}
        asset_bytes = resolved.get("_asset_bytes") or {}
        scene = resolved.get("workspace_scene") or {}
        scene_active = bool(scene.get("layers"))
        if self.scene_canvas is None and scene_active:
            raise ValueError("工作区场景宿主尚未注册。")
        if self.scene_canvas is not None:
            revision = resolved.get("preview_revision") or resolved.get("id") or "theme"
            self.scene_canvas.set_scene(scene, assets, asset_bytes, revision=revision)
        for surface_id, binding in self.surfaces.items():
            surface = (resolved.get("surfaces") or {}).get(surface_id) or {}
            if scene_active and not surface.get("material"):
                surface = {**surface, "material": {"kind": "transparent"}}
            binding.widget.setStyleSheet(
                self.surface_style_sheet(
                    binding.widget,
                    surface,
                    (surface_bases or {}).get(surface_id, binding.default_stylesheet),
                )
            )
        content = resolved.get("content") or {}
        for key, value in content.items():
            handler = self.content_handlers.get(key)
            if handler is not None:
                handler(str(value))
        for component_id, spec in (resolved.get("components") or {}).items():
            binding = self.components.get(component_id)
            if binding is None:
                continue
            if "visible" in spec:
                if component_id in PROTECTED_COMPONENTS and not spec["visible"]:
                    raise ValueError(f"受保护组件不能隐藏：{component_id}")
                binding.widget.setVisible(bool(spec["visible"]))
            style = spec.get("style") or {}
            component_base = (component_bases or {}).get(component_id) or {}
            binding.widget.setStyleSheet(
                self._style_sheet(
                    binding.widget,
                    style,
                    component_base.get("stylesheet", binding.default_stylesheet),
                )
            )
            self._apply_size(binding.widget, style, binding, component_base)
            icon = spec.get("icon") or {}
            if icon and isinstance(binding.widget, (QPushButton, QToolButton, QLabel)):
                if icon.get("source") == "builtin":
                    themed_icon = qta.icon(icon["name"], color=DesignTokens.icon_primary)
                    if isinstance(binding.widget, QLabel):
                        binding.widget.setPixmap(themed_icon.pixmap(20, 20))
                    else:
                        binding.widget.setIcon(themed_icon)
                elif icon.get("source") == "asset":
                    record = assets.get(icon.get("asset")) or {}
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(asset_bytes.get(record.get("path"), b"")):
                        raise ValueError(f"主题图标资产无法解码：{icon.get('asset')}")
                    if isinstance(binding.widget, QLabel):
                        binding.widget.setPixmap(
                            pixmap.scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                    else:
                        binding.widget.setIcon(QIcon(pixmap))
            self._apply_layout(binding, spec.get("layout") or {})

    def apply(self, resolved: dict[str, Any]):
        previous = copy.deepcopy(self._last_resolved)
        surface_snapshot = {
            surface_id: binding.widget.styleSheet()
            for surface_id, binding in self.surfaces.items()
        }
        component_snapshot = {}
        for component_id, binding in self.components.items():
            item = {
                "visible": not binding.widget.isHidden(),
                "stylesheet": binding.widget.styleSheet(),
                "minimum": binding.widget.minimumSize(),
                "maximum": binding.widget.maximumSize(),
            }
            if isinstance(binding.widget, (QPushButton, QToolButton)):
                item["icon"] = QIcon(binding.widget.icon())
            elif isinstance(binding.widget, QLabel) and binding.widget.pixmap() is not None:
                item["pixmap"] = QPixmap(binding.widget.pixmap())
            component_snapshot[component_id] = item
        try:
            # MainWindow has already rebuilt semantic-token QSS for this revision.
            # Treat that exact state as the base for the declarative surface layer.
            self._apply(resolved or {}, surface_snapshot, component_snapshot)
            self._last_resolved = copy.deepcopy(resolved or {})
        except Exception:
            if previous is not None:
                self._apply(previous, surface_snapshot, component_snapshot)
                self._last_resolved = previous
            else:
                self._reset(surface_snapshot, component_snapshot)
            raise

    def restore_presentation(self):
        """Remove the previous declarative layer before semantic QSS is rebuilt."""
        previous = self._last_resolved or {}
        if self.scene_canvas is not None:
            self.scene_canvas.set_scene({}, {}, {}, revision="restore")
        surface_ids = set((previous.get("surfaces") or {}).keys())
        if ((previous.get("workspace_scene") or {}).get("layers") or []):
            surface_ids.update(self.surfaces)
        for surface_id in surface_ids:
            binding = self.surfaces.get(surface_id)
            if binding is not None:
                binding.widget.setStyleSheet(binding.default_stylesheet)
        for component_id, spec in (previous.get("components") or {}).items():
            binding = self.components.get(component_id)
            if binding is None:
                continue
            if spec.get("layout"):
                self._restore_layout(binding)
            if "visible" in spec:
                binding.widget.setVisible(binding.default_visible)
            if spec.get("style"):
                binding.widget.setStyleSheet(binding.default_stylesheet)
                binding.widget.setMinimumSize(binding.default_minimum)
                binding.widget.setMaximumSize(binding.default_maximum)
            if spec.get("icon"):
                if binding.default_icon is not None and isinstance(binding.widget, (QPushButton, QToolButton)):
                    binding.widget.setIcon(binding.default_icon)
                elif binding.default_pixmap is not None and isinstance(binding.widget, QLabel):
                    binding.widget.setPixmap(binding.default_pixmap)
        for key in (previous.get("content") or {}):
            handler = self.content_handlers.get(key)
            if handler is not None:
                handler(CONTENT_DEFAULTS.get(key, ""))
