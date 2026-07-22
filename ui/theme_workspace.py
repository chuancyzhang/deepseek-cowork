from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Any, Callable

import qtawesome as qta
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
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


class ThemeSurfaceBackdrop(QWidget):
    """A non-interactive declarative background layer attached below host children."""

    def __init__(self, host: QWidget, surface_id: str):
        super().__init__(host)
        self.host = host
        self.surface_id = surface_id
        self.layers: list[dict[str, Any]] = []
        self.asset_bytes: dict[str, bytes] = {}
        self.asset_records: dict[str, dict[str, Any]] = {}
        self._pixmap_cache: dict[str, QPixmap] = {}
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.lower()
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in {QEvent.Resize, QEvent.Show, QEvent.LayoutRequest}:
            self.setGeometry(self.host.rect())
            self.lower()
        return super().eventFilter(watched, event)

    def set_theme(self, layers, asset_records, asset_bytes):
        self.layers = copy.deepcopy(list(layers or []))
        self.asset_records = copy.deepcopy(dict(asset_records or {}))
        self.asset_bytes = dict(asset_bytes or {})
        self._pixmap_cache.clear()
        self.setVisible(bool(self.layers))
        self.lower()
        self.update()

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
        pixmap.setDevicePixelRatio(max(1.0, self.devicePixelRatioF()))
        self._pixmap_cache[digest] = pixmap
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
        pixmap = self._pixmap(layer["asset"])
        target = self.rect()
        fit = layer.get("fit", "cover")
        if fit == "tile" or layer.get("repeat"):
            painter.drawTiledPixmap(target, pixmap)
        else:
            destination, source = self._image_target(
                pixmap.size() / pixmap.devicePixelRatio(),
                target,
                fit,
                float(layer.get("focal_x", 0.5)),
                float(layer.get("focal_y", 0.5)),
            )
            painter.drawPixmap(destination, pixmap, source)
        if layer.get("tint"):
            painter.fillRect(target, _color(layer["tint"], float(layer.get("opacity", 1)) * 0.35))

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
            for x in range(rect.left(), rect.right() + 1, spacing):
                painter.drawLine(x, rect.top(), x, rect.bottom())
            for y in range(rect.top(), rect.bottom() + 1, spacing):
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
            generator = random.Random(f"{self.surface_id}:{rect.width()}:{rect.height()}:{size}")
            count = min(12000, max(100, rect.width() * rect.height() // max(8, size * size)))
            for _ in range(count):
                painter.drawRect(
                    generator.randrange(max(1, rect.width())),
                    generator.randrange(max(1, rect.height())),
                    1,
                    1,
                )

    def paintEvent(self, event):
        if not self.layers:
            return
        painter = QPainter(self)
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
        self.surfaces: dict[str, ThemeSurfaceBackdrop] = {}
        self.surface_styles: dict[str, str] = {}
        self.components: dict[str, _ComponentBinding] = {}
        self.content_handlers: dict[str, Callable[[str], None]] = {}
        self._last_resolved: dict[str, Any] | None = None

    def register_surface(self, surface_id: str, widget: QWidget):
        if surface_id in self.surfaces:
            raise ValueError(f"主题区域重复注册：{surface_id}")
        self.surfaces[surface_id] = ThemeSurfaceBackdrop(widget, surface_id)
        self.surface_styles[surface_id] = widget.styleSheet()
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
        for surface_id, backdrop in self.surfaces.items():
            backdrop.host.setStyleSheet(
                (surface_bases or {}).get(surface_id, self.surface_styles.get(surface_id, ""))
            )
            backdrop.set_theme([], {}, {})
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
        for surface_id, backdrop in self.surfaces.items():
            surface = (resolved.get("surfaces") or {}).get(surface_id) or {}
            layers = ((surface.get("background") or {}).get("layers") or [])
            backdrop.set_theme(layers, assets, asset_bytes)
            backdrop.host.setStyleSheet(
                self._style_sheet(
                    backdrop.host,
                    surface.get("style") or {},
                    (surface_bases or {}).get(
                        surface_id,
                        self.surface_styles.get(surface_id, ""),
                    ),
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
            surface_id: backdrop.host.styleSheet()
            for surface_id, backdrop in self.surfaces.items()
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
        for surface_id in (previous.get("surfaces") or {}):
            backdrop = self.surfaces.get(surface_id)
            if backdrop is not None:
                backdrop.host.setStyleSheet(self.surface_styles.get(surface_id, ""))
                backdrop.set_theme([], {}, {})
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
