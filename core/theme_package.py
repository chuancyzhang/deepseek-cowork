from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from typing import Any, Callable


THEME_PACKAGE_SUFFIX = ".cowork-theme"
THEME_SCHEMA_VERSION = 2
THEME_MANIFEST_NAME = "manifest.json"
THEME_MAX_ASSETS = 32
THEME_MAX_ASSET_BYTES = 16 * 1024 * 1024
THEME_MAX_TOTAL_BYTES = 64 * 1024 * 1024
THEME_MAX_MANIFEST_BYTES = 512 * 1024
THEME_MAX_IMAGE_EDGE = 8192
THEME_MAX_IMAGE_PIXELS = 20_000_000

ALLOWED_IMAGE_MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

SURFACE_CATALOG = {
    "shell.left_sidebar",
    "shell.app_header",
    "conversation.canvas",
    "conversation.timeline",
    "conversation.composer",
    "shell.right_sidebar",
    "home.hero",
    "home.quick_actions",
    "home.reminder",
}

COMPONENT_CATALOG = {
    "left.new_chat",
    "left.search",
    "left.projects",
    "left.history",
    "left.capabilities",
    "left.automation",
    "left.settings",
    "header.back",
    "header.title",
    "header.subtitle",
    "header.workspace",
    "header.context_actions",
    "conversation.user_message",
    "conversation.assistant_message",
    "conversation.thinking_stage",
    "conversation.tool_stage",
    "conversation.message_actions",
    "composer.input",
    "composer.add_context",
    "composer.skills",
    "composer.agent",
    "composer.model",
    "composer.pause",
    "composer.submit",
    "right.header",
    "right.files",
    "right.observability",
    "right.sub_agents",
    "home.title",
    "home.card.ppt",
    "home.card.files",
    "home.card.images",
    "home.card.office",
    "home.reminder",
}

PROTECTED_COMPONENTS = {
    "left.new_chat",
    "left.settings",
    "composer.input",
    "composer.submit",
}

CONTENT_DEFAULTS = {
    "brand.title": "DeepSeek Cowork",
    "home.title": "从一个任务开始",
    "home.card.ppt.title": "PPT Agent",
    "home.card.ppt.description": "进入 PPT Mode",
    "home.card.files.title": "整理文件",
    "home.card.files.description": "按类型自动分类",
    "home.card.images.title": "处理图片",
    "home.card.images.description": "批量重命名/压缩",
    "home.card.office.title": "办公交付物",
    "home.card.office.description": "预览修改，再生成文件",
    "home.reminder.title": "需要处理文档或数据？",
    "home.reminder.description": "可在设置里安装文档工具包和数据分析工具包，用于 Office/PDF、表格和数据分析。",
    "composer.placeholder": "描述你要完成的任务，例如：整理本周截图并生成周报摘要",
}

RETIRED_SYSTEM_TITLEBAR_SURFACES = {"window.titlebar"}
RETIRED_SYSTEM_TITLEBAR_COMPONENTS = {
    "titlebar.brand",
    "titlebar.logo",
    "titlebar.minimize",
    "titlebar.maximize",
    "titlebar.close",
}
RETIRED_SYSTEM_TITLEBAR_CONTENT = {"brand.tagline"}

_COLOR_RE = re.compile(
    r"^(?:#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0(?:\.\d+)?|1(?:\.0+)?))?\s*\))$"
)
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_ASSET_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数字。")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字。") from None
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} 必须位于 {minimum:g}–{maximum:g} 之间。")
    return result


def _normalize_color(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not _COLOR_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是受支持的颜色。")
    if text.lower().startswith("rgb"):
        channels = [int(item) for item in re.findall(r"\d+", text)[:3]]
        if len(channels) != 3 or any(channel > 255 for channel in channels):
            raise ValueError(f"{name} 的 RGB 通道必须位于 0–255。")
    return text.lower() if text.startswith("#") else text


def _normalize_background_layer(raw: Any, *, surface_id: str, index: int, assets: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{surface_id} 的背景层 {index + 1} 必须是对象。")
    allowed = {
        "type", "color", "asset", "opacity", "blend", "fit", "repeat",
        "focal_x", "focal_y", "tint", "size", "spacing", "angle", "line_width",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{surface_id} 的背景层包含未知字段：{', '.join(unknown)}")
    layer_type = str(raw.get("type") or "").strip().lower()
    if layer_type not in {"solid", "image", "stripes", "grid", "dots", "noise"}:
        raise ValueError(f"{surface_id} 的背景类型无效：{layer_type or '<empty>'}")
    result = {"type": layer_type}
    if layer_type == "image":
        asset_id = str(raw.get("asset") or "").strip()
        if asset_id not in assets:
            raise ValueError(f"{surface_id} 引用了不存在的主题资产：{asset_id}")
        result["asset"] = asset_id
        fit = str(raw.get("fit") or "cover").strip().lower()
        if fit not in {"cover", "contain", "stretch", "center", "tile"}:
            raise ValueError(f"{surface_id} 的图片 fit 无效：{fit}")
        result["fit"] = fit
        result["repeat"] = bool(raw.get("repeat", fit == "tile"))
        result["focal_x"] = _bounded_number(raw.get("focal_x", 0.5), "focal_x", 0, 1)
        result["focal_y"] = _bounded_number(raw.get("focal_y", 0.5), "focal_y", 0, 1)
    else:
        result["color"] = _normalize_color(raw.get("color") or "#000000", "color")
        if layer_type in {"stripes", "grid", "dots"}:
            result["spacing"] = int(_bounded_number(raw.get("spacing", 16), "spacing", 2, 256))
            result["line_width"] = int(_bounded_number(raw.get("line_width", 1), "line_width", 1, 16))
        if layer_type in {"stripes", "noise"}:
            result["size"] = int(_bounded_number(raw.get("size", 8), "size", 1, 256))
        if layer_type == "stripes":
            result["angle"] = _bounded_number(raw.get("angle", 45), "angle", -360, 360)
    result["opacity"] = _bounded_number(raw.get("opacity", 1), "opacity", 0, 1)
    blend = str(raw.get("blend") or "source_over").strip().lower()
    if blend not in {"source_over", "multiply", "screen", "overlay"}:
        raise ValueError(f"{surface_id} 的 blend 无效：{blend}")
    result["blend"] = blend
    if raw.get("tint") not in (None, ""):
        result["tint"] = _normalize_color(raw.get("tint"), "tint")
    return result


def _normalize_style(raw: Any, *, field: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} 必须是对象。")
    allowed = {
        "foreground", "background", "border_color", "border_width", "radius",
        "opacity", "font_size", "font_weight", "padding", "gap",
        "min_width", "preferred_width", "max_width", "min_height",
        "preferred_height", "max_height",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{field} 包含未知样式字段：{', '.join(unknown)}")
    result = {}
    for name, value in raw.items():
        if name in {"foreground", "background", "border_color"}:
            result[name] = _normalize_color(value, f"{field}.{name}")
        elif name == "opacity":
            result[name] = _bounded_number(value, f"{field}.{name}", 0, 1)
        elif name == "font_weight":
            result[name] = int(_bounded_number(value, f"{field}.{name}", 100, 900))
        elif name == "font_size":
            result[name] = int(_bounded_number(value, f"{field}.{name}", 9, 48))
        elif name in {"border_width", "radius", "padding", "gap"}:
            result[name] = int(_bounded_number(value, f"{field}.{name}", 0, 64))
        else:
            result[name] = int(_bounded_number(value, f"{field}.{name}", 0, 1800))
    for prefix in ("width", "height"):
        minimum = result.get(f"min_{prefix}")
        preferred = result.get(f"preferred_{prefix}")
        maximum = result.get(f"max_{prefix}")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{field} 的 min_{prefix} 不能大于 max_{prefix}。")
        if preferred is not None and minimum is not None and preferred < minimum:
            raise ValueError(f"{field} 的 preferred_{prefix} 不能小于 min_{prefix}。")
        if preferred is not None and maximum is not None and preferred > maximum:
            raise ValueError(f"{field} 的 preferred_{prefix} 不能大于 max_{prefix}。")
    return result


def _normalize_layout(raw: Any, *, component_id: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{component_id}.layout 必须是对象。")
    allowed = {
        "slot", "order", "row", "column", "row_span", "column_span", "alignment",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{component_id}.layout 包含未知字段：{', '.join(unknown)}")
    result = {}
    if "slot" in raw:
        slot = str(raw.get("slot") or "").strip().lower()
        if slot not in {"start", "center", "end", "primary", "secondary"}:
            raise ValueError(f"{component_id}.layout.slot 无效：{slot}")
        result["slot"] = slot
    if "alignment" in raw:
        alignment = str(raw.get("alignment") or "").strip().lower()
        if alignment not in {"start", "center", "end", "stretch"}:
            raise ValueError(f"{component_id}.layout.alignment 无效：{alignment}")
        result["alignment"] = alignment
    for name in ("order", "row", "column", "row_span", "column_span"):
        if name in raw:
            minimum = -100 if name == "order" else (1 if name.endswith("_span") else 0)
            maximum = 100 if name == "order" else (4 if name.endswith("_span") else 12)
            result[name] = int(_bounded_number(raw[name], f"{component_id}.layout.{name}", minimum, maximum))
    return result


def normalize_theme_manifest(
    payload: Any,
    *,
    validate_overrides: Callable[[Any], dict],
    default_id: str = "",
    asset_bytes: dict[str, bytes] | None = None,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("主题 manifest 必须是对象。")
    allowed = {
        "format", "schema_version", "id", "name", "overrides", "assets",
        "surfaces", "components", "content",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"主题 manifest 包含未知字段：{', '.join(unknown)}")
    if payload.get("format") != "cowork-theme":
        raise ValueError("不是有效的 cowork-theme manifest。")
    if int(payload.get("schema_version") or 0) != THEME_SCHEMA_VERSION:
        raise ValueError(f"主题 schema_version 必须是 {THEME_SCHEMA_VERSION}。")
    theme_id = str(payload.get("id") or default_id or "").strip()
    if not _ID_RE.fullmatch(theme_id) or theme_id == "default":
        raise ValueError("主题 manifest id 无效。")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ValueError("主题名称必须为 1–40 个字符。")

    raw_assets = payload.get("assets") or {}
    if not isinstance(raw_assets, dict) or len(raw_assets) > THEME_MAX_ASSETS:
        raise ValueError(f"主题 assets 必须是对象且不能超过 {THEME_MAX_ASSETS} 项。")
    assets = {}
    paths = set()
    hashes = set()
    for asset_id, raw in raw_assets.items():
        asset_id = str(asset_id or "").strip()
        if not _ASSET_ID_RE.fullmatch(asset_id) or not isinstance(raw, dict):
            raise ValueError(f"主题资产 ID 或记录无效：{asset_id or '<empty>'}")
        unknown_asset = sorted(set(raw) - {"path", "media_type", "sha256", "width", "height"})
        if unknown_asset:
            raise ValueError(f"主题资产 {asset_id} 包含未知字段：{', '.join(unknown_asset)}")
        path = str(raw.get("path") or "").replace("\\", "/").strip()
        if (
            not path.startswith("assets/")
            or path.startswith("/")
            or ".." in path.split("/")
            or path in paths
        ):
            raise ValueError(f"主题资产路径无效或重复：{path}")
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        expected_media = ALLOWED_IMAGE_MEDIA.get(suffix)
        if not expected_media or str(raw.get("media_type") or "") != expected_media:
            raise ValueError(f"主题资产类型无效：{path}")
        sha256 = str(raw.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError(f"主题资产哈希无效：{asset_id}")
        if sha256 in hashes:
            raise ValueError(f"主题包包含重复图片内容：{asset_id}")
        width = int(_bounded_number(raw.get("width"), f"{asset_id}.width", 1, THEME_MAX_IMAGE_EDGE))
        height = int(_bounded_number(raw.get("height"), f"{asset_id}.height", 1, THEME_MAX_IMAGE_EDGE))
        if width * height > THEME_MAX_IMAGE_PIXELS:
            raise ValueError(f"主题资产像素总量超限：{asset_id}")
        if asset_bytes is not None:
            data = asset_bytes.get(path)
            if data is None:
                raise ValueError(f"主题包缺少资产：{path}")
            if hashlib.sha256(data).hexdigest() != sha256:
                raise ValueError(f"主题资产哈希不匹配：{asset_id}")
            actual = inspect_image_bytes(data, filename=path)
            if actual["media_type"] != expected_media or actual["width"] != width or actual["height"] != height:
                raise ValueError(f"主题资产元数据与文件不一致：{asset_id}")
        assets[asset_id] = {
            "path": path,
            "media_type": expected_media,
            "sha256": sha256,
            "width": width,
            "height": height,
        }
        paths.add(path)
        hashes.add(sha256)

    raw_surfaces = payload.get("surfaces") or {}
    if not isinstance(raw_surfaces, dict):
        raise ValueError("主题 surfaces 必须是对象。")
    surfaces = {}
    for surface_id, raw in raw_surfaces.items():
        if surface_id in RETIRED_SYSTEM_TITLEBAR_SURFACES:
            raise ValueError(f"系统标题栏不支持主题覆盖：{surface_id}")
        if surface_id not in SURFACE_CATALOG or not isinstance(raw, dict):
            raise ValueError(f"未知或无效的主题区域：{surface_id}")
        unknown_surface = sorted(set(raw) - {"background", "style"})
        if unknown_surface:
            raise ValueError(f"{surface_id} 包含未知字段：{', '.join(unknown_surface)}")
        item = {}
        if "background" in raw:
            background = raw.get("background") or {}
            if not isinstance(background, dict) or set(background) - {"layers"}:
                raise ValueError(f"{surface_id}.background 仅支持 layers。")
            layers = background.get("layers") or []
            if not isinstance(layers, list) or len(layers) > 4:
                raise ValueError(f"{surface_id} 的背景层不能超过 4 层。")
            item["background"] = {
                "layers": [
                    _normalize_background_layer(layer, surface_id=surface_id, index=index, assets=assets)
                    for index, layer in enumerate(layers)
                ]
            }
        if "style" in raw:
            item["style"] = _normalize_style(raw.get("style"), field=f"{surface_id}.style")
        surfaces[surface_id] = item

    raw_components = payload.get("components") or {}
    if not isinstance(raw_components, dict):
        raise ValueError("主题 components 必须是对象。")
    components = {}
    for component_id, raw in raw_components.items():
        if component_id in RETIRED_SYSTEM_TITLEBAR_COMPONENTS:
            raise ValueError(f"系统标题栏不支持主题覆盖：{component_id}")
        if component_id not in COMPONENT_CATALOG or not isinstance(raw, dict):
            raise ValueError(f"未知或无效的主题组件：{component_id}")
        unknown_component = sorted(set(raw) - {"visible", "icon", "style", "layout"})
        if unknown_component:
            raise ValueError(f"{component_id} 包含禁止或未知字段：{', '.join(unknown_component)}")
        item = {}
        if "visible" in raw:
            visible = bool(raw.get("visible"))
            if component_id in PROTECTED_COMPONENTS and not visible:
                raise ValueError(f"受保护组件不能隐藏：{component_id}")
            item["visible"] = visible
        if "icon" in raw:
            icon = raw.get("icon")
            if not isinstance(icon, dict) or set(icon) - {"source", "name", "asset"}:
                raise ValueError(f"{component_id}.icon 无效。")
            source = str(icon.get("source") or "").strip().lower()
            if source == "builtin":
                name_value = str(icon.get("name") or "").strip()
                if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name_value):
                    raise ValueError(f"{component_id}.icon.name 无效。")
                item["icon"] = {"source": "builtin", "name": name_value}
            elif source == "asset":
                asset_id = str(icon.get("asset") or "").strip()
                if asset_id not in assets:
                    raise ValueError(f"{component_id}.icon 引用了不存在的资产：{asset_id}")
                item["icon"] = {"source": "asset", "asset": asset_id}
            else:
                raise ValueError(f"{component_id}.icon.source 无效。")
        if "style" in raw:
            item["style"] = _normalize_style(raw.get("style"), field=f"{component_id}.style")
        if "layout" in raw:
            item["layout"] = _normalize_layout(raw.get("layout"), component_id=component_id)
        components[component_id] = item

    occupied_home_cells = {}
    for component_id, item in components.items():
        if not component_id.startswith("home.card."):
            continue
        layout = item.get("layout") or {}
        if "row" not in layout and "column" not in layout:
            continue
        row = int(layout.get("row", 0))
        column = int(layout.get("column", 0))
        row_span = int(layout.get("row_span", 1))
        column_span = int(layout.get("column_span", 1))
        for cell_row in range(row, row + row_span):
            for cell_column in range(column, column + column_span):
                cell = (cell_row, cell_column)
                if cell in occupied_home_cells:
                    raise ValueError(
                        f"首页卡片布局重叠：{occupied_home_cells[cell]} 与 {component_id}"
                    )
                occupied_home_cells[cell] = component_id

    raw_content = payload.get("content") or {}
    if not isinstance(raw_content, dict):
        raise ValueError("主题 content 必须是对象。")
    retired_content = sorted(set(raw_content) & RETIRED_SYSTEM_TITLEBAR_CONTENT)
    if retired_content:
        raise ValueError(f"系统标题栏不支持主题覆盖：{', '.join(retired_content)}")
    unknown_content = sorted(set(raw_content) - set(CONTENT_DEFAULTS))
    if unknown_content:
        raise ValueError(f"主题包含不可替换的文案：{', '.join(unknown_content)}")
    content = {}
    for key, value in raw_content.items():
        text = str(value or "").strip()
        if len(text) > 240:
            raise ValueError(f"主题文案过长：{key}")
        content[key] = text

    return {
        "format": "cowork-theme",
        "schema_version": THEME_SCHEMA_VERSION,
        "id": theme_id,
        "name": name,
        "overrides": validate_overrides(payload.get("overrides") or {}),
        "assets": assets,
        "surfaces": surfaces,
        "components": components,
        "content": content,
    }


def inspect_image_bytes(data: bytes, *, filename: str = "") -> dict:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("主题图片为空。")
    if len(data) > THEME_MAX_ASSET_BYTES:
        raise ValueError(f"主题图片不能超过 {THEME_MAX_ASSET_BYTES // (1024 * 1024)} MiB。")
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader

    payload = QByteArray(bytes(data))
    raw = bytes(data)
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        image_format = "png"
    elif raw.startswith(b"\xff\xd8\xff"):
        image_format = "jpg"
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        image_format = "webp"
    else:
        image_format = ""
    if image_format not in ALLOWED_IMAGE_MEDIA:
        raise ValueError(f"主题图片格式无效：{filename or '<memory>'}")
    buffer = QBuffer()
    buffer.setData(payload)
    if not buffer.open(QIODevice.ReadOnly):
        raise ValueError(f"主题图片无法读取：{filename or '<memory>'}")
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    if reader.supportsAnimation() or reader.imageCount() > 1:
        raise ValueError(f"主题图片不能包含动画：{filename or '<memory>'}")
    image = reader.read()
    if image.isNull():
        raise ValueError(
            f"主题图片无法解码：{filename or '<memory>'}；{reader.errorString()}"
        )
    width, height = image.width(), image.height()
    if width > THEME_MAX_IMAGE_EDGE or height > THEME_MAX_IMAGE_EDGE or width * height > THEME_MAX_IMAGE_PIXELS:
        raise ValueError(f"主题图片尺寸超限：{filename or '<memory>'}")
    return {
        "media_type": ALLOWED_IMAGE_MEDIA[image_format],
        "extension": "jpg" if image_format in {"jpg", "jpeg"} else image_format,
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(bytes(data)).hexdigest(),
    }


def read_theme_package(path: str, *, validate_overrides: Callable[[Any], dict]) -> tuple[dict, dict[str, bytes]]:
    absolute = os.path.abspath(path)
    if not zipfile.is_zipfile(absolute):
        raise ValueError("主题包不是有效的 ZIP 文件。")
    assets = {}
    total = 0
    with zipfile.ZipFile(absolute, "r") as archive:
        names = set()
        manifest_bytes = None
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            if (
                normalized.startswith("/")
                or ".." in normalized.split("/")
                or normalized in names
                or info.is_dir()
                or (info.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError(f"主题包包含不安全或重复路径：{normalized}")
            names.add(normalized)
            total += int(info.file_size)
            if total > THEME_MAX_TOTAL_BYTES:
                raise ValueError("主题包解压体积超过 64 MiB。")
            if normalized == THEME_MANIFEST_NAME:
                if info.file_size > THEME_MAX_MANIFEST_BYTES:
                    raise ValueError("主题 manifest 过大。")
                manifest_bytes = archive.read(info)
            elif normalized.startswith("assets/"):
                if info.file_size > THEME_MAX_ASSET_BYTES:
                    raise ValueError(f"主题资产超过 16 MiB：{normalized}")
                assets[normalized] = archive.read(info)
            else:
                raise ValueError(f"主题包包含未知文件：{normalized}")
        if manifest_bytes is None:
            raise ValueError("主题包缺少 manifest.json。")
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"主题 manifest.json 无效：{exc}") from None
    manifest = normalize_theme_manifest(
        payload,
        validate_overrides=validate_overrides,
        asset_bytes=assets,
    )
    declared_paths = {item["path"] for item in manifest["assets"].values()}
    extras = sorted(set(assets) - declared_paths)
    if extras:
        raise ValueError("主题包包含未声明资产：" + ", ".join(extras))
    return manifest, assets


def write_theme_package(
    path: str,
    manifest: dict,
    assets: dict[str, bytes],
    *,
    validate_overrides: Callable[[Any], dict],
) -> dict:
    normalized = normalize_theme_manifest(
        manifest,
        validate_overrides=validate_overrides,
        asset_bytes=assets,
    )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix=".theme-package-", suffix=".tmp", dir=directory)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(
                THEME_MANIFEST_NAME,
                json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
            )
            for asset_id in sorted(normalized["assets"]):
                asset_path = normalized["assets"][asset_id]["path"]
                archive.writestr(asset_path, assets[asset_path])
        with open(temp_path, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        return _json_copy(normalized)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def build_asset_record(asset_id: str, source_path: str) -> tuple[dict, bytes]:
    asset_id = str(asset_id or "").strip()
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise ValueError("主题资产 ID 必须为 1–80 位字母、数字、点、下划线或短横线。")
    absolute = os.path.abspath(str(source_path or ""))
    if not os.path.isfile(absolute):
        raise ValueError(f"主题图片不存在：{absolute}")
    with open(absolute, "rb") as stream:
        data = stream.read(THEME_MAX_ASSET_BYTES + 1)
    metadata = inspect_image_bytes(data, filename=absolute)
    package_path = f"assets/{metadata['sha256'][:16]}.{metadata['extension']}"
    return {
        "path": package_path,
        "media_type": metadata["media_type"],
        "sha256": metadata["sha256"],
        "width": metadata["width"],
        "height": metadata["height"],
    }, data


def copy_manifest(value: dict) -> dict:
    return copy.deepcopy(value)
