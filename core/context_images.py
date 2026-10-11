"""Image input budgets, without changing image detail or provider payloads.

These are conservative estimates for context capacity, not billing. Resizing
uses patch-budget upper bounds; actual usage still calibrates the full request.
Rules checked 2026-10-11:
https://developers.openai.com/api/docs/guides/images-vision
https://platform.claude.com/docs/en/build-with-claude/vision
"""

import base64
import math
import re
from io import BytesIO

from PIL import Image


class ImageBudgetUnavailable(ValueError):
    """The image remains sendable, but local capacity cannot be reported."""


def image_dimensions(part):
    """Inspect local headers only. Budgeting never fetches a remote image."""
    path = part.get("path")
    source = part.get("source") or {}
    value = part.get("image_url") or source.get("url") or ""
    url = value.get("url", "") if isinstance(value, dict) else value
    try:
        if path:
            with Image.open(path) as image:
                return image.size
        if isinstance(url, str) and url.startswith("data:"):
            header, encoded = url.split(",", 1)
            if not header.endswith(";base64"):
                raise ImageBudgetUnavailable("图片不是受支持的 base64 数据格式。")
        elif source.get("type") == "base64":
            encoded = source.get("data") or ""
        else:
            raise ImageBudgetUnavailable("远程图片或文件引用的尺寸尚不可用。")
        with Image.open(BytesIO(base64.b64decode(encoded, validate=True))) as image:
            return image.size
    except ImageBudgetUnavailable:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # Only local estimation is unavailable. The existing provider boundary
        # remains responsible for image validation and explicit delivery errors.
        raise ImageBudgetUnavailable("无法读取图片尺寸以估算上下文。") from exc


def _fit(width, height, edge):
    scale = min(1.0, edge / max(width, height))
    return max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))


def image_token_budget(part, model_name, *, detail=None):
    # Only exact family names and dated snapshots inherit a documented rule.
    model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", str(model_name or "").lower())
    image_url = part.get("image_url")
    detail = detail or (image_url.get("detail") if isinstance(image_url, dict) else None) or part.get("detail") or "auto"
    if detail not in {"auto", "low", "high", "original"}:
        raise ImageBudgetUnavailable("当前图片精度尚无预算规则。")

    tiles = {"gpt-5.1": (70, 140), "gpt-5": (70, 140),
             "gpt-4o": (85, 170), "gpt-4.1": (85, 170), "gpt-4o-mini": (2833, 5667)}
    patch_models = {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
               "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-4.1-mini"}
    claude = re.fullmatch(r"claude-(?:opus|sonnet|haiku)-(\d+)(?:[.-](\d+))?(?:-\d{8})?", model)
    if model not in tiles and model not in patch_models and not claude:
        raise ImageBudgetUnavailable("当前模型尚无图片预算规则。")
    width, height = image_dimensions(part)
    if model in tiles:
        base, tile = tiles[model]
        if detail == "low":
            return base
        if detail == "original":
            raise ImageBudgetUnavailable("当前模型的 original 图片精度尚无预算规则。")
        width, height = _fit(width, height, 2048)
        scale = min(1.0, 768 / min(width, height))
        return base + tile * math.ceil(width * scale / 512) * math.ceil(height * scale / 512)

    edge, cap, multiplier = None, None, 1.2
    if model in {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        edge = 512 if detail == "low" else 65535 if detail in {"auto", "original"} or model == "gpt-6-astra" else 2048
        cap = 2500 if detail == "high" else None
    elif model in {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"}:
        if detail == "auto":
            detail = "original" if model == "gpt-5.5" else "high"
        if detail == "original":
            edge, cap = 6000, 10000
        elif detail == "low":
            edge, cap = (512, None) if model == "gpt-5.5" else (2048, 6144)
        else:
            edge, cap = 2048, 2500
    elif model in {"gpt-5.2", "gpt-4.1-mini"} and detail != "original":
        edge, cap = 2048, 6144
        multiplier = 1.62 if model == "gpt-4.1-mini" else 1.2
    if edge is not None:
        width, height = _fit(width, height, edge)
        patches = math.ceil(width / 32) * math.ceil(height / 32)
        # Never assume that an original-detail image is silently resized to a
        # patch cap: the service rejects images over its input limit instead.
        return math.ceil(min(patches, cap) * multiplier if cap else patches * multiplier)

    if claude:
        version = (int(claude[1]), int(claude[2] or 0))
        edge, cap = (2576, 4784) if version >= (4, 7) else (1568, 1568)
        width, height = _fit(width, height, edge)
        return min(math.ceil(width / 28) * math.ceil(height / 28), cap)
    raise ImageBudgetUnavailable("当前模型尚无图片预算规则。")
