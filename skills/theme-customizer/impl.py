from __future__ import annotations

import json

from core.theme import default_design_tokens, resolve_theme, theme_token_schema
from core.theme_package import DEFAULT_WORKSPACE_SCENE
from core.theme_service import (
    DEFAULT_THEME_ID,
    ThemeRepository,
    append_theme_log,
    theme_contrast_warnings,
    theme_manifest_schema,
    theme_token_group,
    validate_theme_manifest,
    validate_theme_overrides,
)


def _repository(_context):
    context = _context if isinstance(_context, dict) else {}
    repository = context.get("theme_repository")
    if isinstance(repository, ThemeRepository):
        return repository
    config_manager = context.get("config_manager")
    data_dir = getattr(config_manager, "data_dir", None)
    return ThemeRepository(data_dir)


def _approval_response(message, *, title, details="", _context=None):
    from skills.interaction.impl import request_user_approval

    return request_user_approval(
        message,
        title=title,
        details=details,
        severity="low",
        _context=_context,
    )


def _is_approved(payload):
    response = payload.get("interaction_response") if isinstance(payload, dict) else None
    return bool((response or {}).get("approved"))


def _summarize_theme(theme, *, active=False):
    return {
        "id": theme.get("id"),
        "name": theme.get("name"),
        "active": bool(active),
        "overrides": json.loads(json.dumps(theme.get("overrides") or {}, ensure_ascii=False)),
        "assets": json.loads(json.dumps(theme.get("assets") or {}, ensure_ascii=False)),
        "workspace_scene": json.loads(
            json.dumps(theme.get("workspace_scene") or DEFAULT_WORKSPACE_SCENE, ensure_ascii=False)
        ),
        "surfaces": json.loads(json.dumps(theme.get("surfaces") or {}, ensure_ascii=False)),
        "components": json.loads(json.dumps(theme.get("components") or {}, ensure_ascii=False)),
        "content_overrides": json.loads(json.dumps(theme.get("content") or {}, ensure_ascii=False)),
    }


def inspect_ui_theme(theme_id="", include_schema=True, _context=None):
    repository = _repository(_context)
    snapshot = repository.load()
    selected_id = str(theme_id or snapshot.active_theme_id or DEFAULT_THEME_ID)
    theme = repository.get_theme(selected_id)
    if not theme:
        return {"status": "error", "error": f"Theme '{selected_id}' not found."}
    resolved = resolve_theme(theme, default_design_tokens())
    items = [
        _summarize_theme(
            {
                "id": DEFAULT_THEME_ID,
                "name": "默认主题",
                "base": DEFAULT_THEME_ID,
                "overrides": {},
            },
            active=snapshot.active_theme_id == DEFAULT_THEME_ID,
        )
    ]
    items.extend(
        _summarize_theme(item, active=item.get("id") == snapshot.active_theme_id)
        for item in snapshot.themes
    )
    for item in items:
        if item.get("id") != DEFAULT_THEME_ID:
            item["path"] = repository.theme_path(item["id"])
    result = {
        "status": "ok",
        "active_theme_id": snapshot.active_theme_id,
        "selected": _summarize_theme(theme, active=selected_id == snapshot.active_theme_id),
        "resolved": resolved,
        "themes": items,
        "contrast_warnings": theme_contrast_warnings(resolved),
        "content": f"Active UI theme is '{repository.get_theme(snapshot.active_theme_id).get('name')}'.",
    }
    if selected_id != DEFAULT_THEME_ID:
        result["selected"]["path"] = repository.theme_path(selected_id)
    if include_schema:
        token_schema = theme_token_schema()
        result["token_schema"] = token_schema
        token_groups = {}
        for token_name, metadata in token_schema.items():
            token_groups.setdefault(metadata["group"], {})[token_name] = metadata
        result["token_groups"] = token_groups
        result["override_schema"] = {
            "font_family": "installed system font family",
            "mono_font_family": "installed system monospace font family",
            "font_scale": {"minimum": 0.8, "maximum": 1.5},
            "density": ["compact", "standard", "comfortable"],
            "radius_scale": {"minimum": 0.5, "maximum": 1.5},
            "tokens": "Use token_schema keys only.",
        }
        result["manifest_schema"] = theme_manifest_schema()
        try:
            from PySide6.QtGui import QFontDatabase

            result["installed_font_families"] = list(QFontDatabase.families())
        except Exception as exc:
            result["font_inventory_error"] = str(exc)
    return result


def _validate_ui_theme(
    name, overrides, workspace_scene=None, surfaces=None, components=None, content=None, assets=None
):
    normalized = validate_theme_overrides(overrides, default_design_tokens())
    requested_fonts = {
        str(normalized.get(field) or "").strip()
        for field in ("font_family", "mono_font_family")
        if normalized.get(field)
    }
    if requested_fonts:
        from PySide6.QtGui import QFontDatabase

        installed = {family.casefold() for family in QFontDatabase.families()}
        missing = sorted(
            family for family in requested_fonts if family.casefold() not in installed
        )
        if missing:
            raise ValueError("系统未安装主题字体：" + ", ".join(missing))
    resolved = resolve_theme(
        {
            "id": "validation",
            "name": str(name or "主题校验"),
            "base": DEFAULT_THEME_ID,
            "overrides": normalized,
        },
        default_design_tokens(),
    )
    normalized_manifest = validate_theme_manifest(
        {
            "format": "cowork-theme",
            "schema_version": 2,
            "id": "validation",
            "name": str(name or "主题校验"),
            "overrides": normalized,
            "assets": assets or {},
            "workspace_scene": workspace_scene or DEFAULT_WORKSPACE_SCENE,
            "surfaces": surfaces or {},
            "components": components or {},
            "content": content or {},
        },
        default_design_tokens(),
    )
    warnings = theme_contrast_warnings(resolved)
    affected_areas = sorted(
        {
            theme_token_group(token_name)
            for token_name in (normalized.get("tokens") or {})
        }
    )
    if any(
        field in normalized
        for field in ("font_family", "mono_font_family", "font_scale", "density", "radius_scale")
    ):
        affected_areas.append("global")
    affected_areas = sorted(set(affected_areas))
    return {
        "status": "ok",
        "name": str(name or "主题校验"),
        "normalized_overrides": normalized,
        "normalized_manifest": normalized_manifest,
        "resolved": resolved,
        "contrast_warnings": warnings,
        "affected_areas": affected_areas,
        "content": "Theme is valid." if not warnings else f"Theme is valid with {len(warnings)} contrast warning(s).",
    }


def validate_ui_theme(
    name, overrides, workspace_scene=None, surfaces=None, components=None, content=None, assets=None, _context=None
):
    repository = _repository(_context)
    append_theme_log(repository.data_dir, "tool_validate_submit", name=name)
    append_theme_log(repository.data_dir, "tool_validate_start", name=name)
    try:
        append_theme_log(repository.data_dir, "tool_validate_run", name=name)
        result = _validate_ui_theme(
            name, overrides, workspace_scene, surfaces, components, content, assets
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_validate_error",
            name=name,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_validate_finish",
        name=name,
        affected_areas=result["affected_areas"],
        contrast_warning_count=len(result["contrast_warnings"]),
    )
    return result


def preview_ui_theme(
    name, overrides, workspace_scene=None, surfaces=None, components=None, content=None, assets=None, _context=None
):
    repository = _repository(_context)
    session_id = str((_context or {}).get("session_id") or "") if isinstance(_context, dict) else ""
    append_theme_log(repository.data_dir, "tool_preview_submit", session_id=session_id, name=name)
    append_theme_log(repository.data_dir, "tool_preview_start", session_id=session_id, name=name)
    try:
        append_theme_log(repository.data_dir, "tool_preview_run", session_id=session_id, name=name)
        preview = repository.write_preview(
            name=name,
            overrides=overrides,
            default_tokens=default_design_tokens(),
            session_id=session_id,
            workspace_scene=workspace_scene or DEFAULT_WORKSPACE_SCENE,
            surfaces=surfaces or {},
            components=components or {},
            content=content or {},
            assets=assets or {},
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_preview_error",
            session_id=session_id,
            name=name,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_preview_finish",
        session_id=session_id,
        preview_id=preview["preview_id"],
        preview_revision=preview["revision"],
    )
    return {
        "status": "ok",
        "preview_id": preview["preview_id"],
        "preview_revision": preview["revision"],
        "preview_path": repository.preview_path,
        "normalized_overrides": preview["overrides"],
        "manifest": {
            key: preview.get(key) or {}
            for key in ("overrides", "assets", "workspace_scene", "surfaces", "components", "content")
        },
        "content": "Theme preview requested. It remains temporary until explicitly saved or restored.",
    }


def patch_ui_theme_preview(
    preview_id,
    preview_revision,
    set_overrides=None,
    unset_tokens=None,
    operations=None,
    _context=None,
):
    repository = _repository(_context)
    append_theme_log(
        repository.data_dir,
        "tool_patch_preview_submit",
        preview_id=preview_id,
        preview_revision=preview_revision,
    )
    append_theme_log(repository.data_dir, "tool_patch_preview_start", preview_id=preview_id)
    try:
        append_theme_log(repository.data_dir, "tool_patch_preview_run", preview_id=preview_id)
        preview = repository.patch_preview(
            preview_id=str(preview_id or ""),
            preview_revision=int(preview_revision),
            set_overrides=set_overrides or {},
            unset_tokens=unset_tokens or [],
            default_tokens=default_design_tokens(),
            operations=operations or [],
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_patch_preview_error",
            preview_id=preview_id,
            preview_revision=preview_revision,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_patch_preview_finish",
        preview_id=preview["preview_id"],
        preview_revision=preview["revision"],
    )
    return {
        "status": "ok",
        "preview_id": preview["preview_id"],
        "preview_revision": preview["revision"],
        "normalized_overrides": preview["overrides"],
        "manifest": {
            key: preview.get(key) or {}
            for key in ("overrides", "assets", "workspace_scene", "surfaces", "components", "content")
        },
        "content": "Theme preview patched. The new revision must be reviewed before saving.",
    }


def import_ui_theme_asset(
    preview_id,
    preview_revision,
    asset_id,
    source_path,
    _context=None,
):
    repository = _repository(_context)
    append_theme_log(
        repository.data_dir,
        "tool_asset_import_submit",
        preview_id=preview_id,
        preview_revision=preview_revision,
        asset_id=asset_id,
    )
    append_theme_log(repository.data_dir, "tool_asset_import_start", preview_id=preview_id, asset_id=asset_id)
    try:
        append_theme_log(repository.data_dir, "tool_asset_import_run", preview_id=preview_id, asset_id=asset_id)
        preview = repository.import_preview_asset(
            preview_id=str(preview_id or ""),
            preview_revision=int(preview_revision),
            asset_id=str(asset_id or ""),
            source_path=str(source_path or ""),
            default_tokens=default_design_tokens(),
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_asset_import_error",
            preview_id=preview_id,
            asset_id=asset_id,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_asset_import_finish",
        preview_id=preview_id,
        preview_revision=preview["revision"],
        asset_id=asset_id,
    )
    return {
        "status": "ok",
        "preview_id": preview["preview_id"],
        "preview_revision": preview["revision"],
        "asset": (preview.get("assets") or {}).get(asset_id),
        "content": "Theme image imported into the preview package. Reference it from workspace_scene or an icon.",
    }


def remove_ui_theme_asset(preview_id, preview_revision, asset_id, _context=None):
    repository = _repository(_context)
    append_theme_log(
        repository.data_dir,
        "tool_asset_remove_submit",
        preview_id=preview_id,
        preview_revision=preview_revision,
        asset_id=asset_id,
    )
    append_theme_log(repository.data_dir, "tool_asset_remove_start", preview_id=preview_id, asset_id=asset_id)
    try:
        append_theme_log(repository.data_dir, "tool_asset_remove_run", preview_id=preview_id, asset_id=asset_id)
        preview = repository.remove_preview_asset(
            preview_id=str(preview_id or ""),
            preview_revision=int(preview_revision),
            asset_id=str(asset_id or ""),
            default_tokens=default_design_tokens(),
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_asset_remove_error",
            preview_id=preview_id,
            asset_id=asset_id,
            error=str(exc),
        )
        return {
            "status": "error",
            "error": str(exc),
            "preview_id": str(preview_id or ""),
            "preview_revision": int(preview_revision),
            "asset_id": str(asset_id or ""),
        }
    append_theme_log(
        repository.data_dir,
        "tool_asset_remove_finish",
        preview_id=preview_id,
        preview_revision=preview["revision"],
        asset_id=asset_id,
    )
    return {
        "status": "ok",
        "preview_id": preview["preview_id"],
        "preview_revision": preview["revision"],
        "content": "Theme image removed from the preview package.",
    }


def clear_ui_theme_preview(preview_id="", _context=None):
    repository = _repository(_context)
    append_theme_log(
        repository.data_dir,
        "tool_restore_submit",
        preview_id=preview_id,
    )
    append_theme_log(
        repository.data_dir,
        "tool_restore_start",
        preview_id=preview_id,
    )
    try:
        append_theme_log(repository.data_dir, "tool_restore_run", preview_id=preview_id)
        cleared = repository.clear_preview(str(preview_id or ""))
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_restore_error",
            preview_id=preview_id,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_restore_finish",
        preview_id=preview_id,
        cleared=bool(cleared),
    )
    return {
        "status": "ok",
        "cleared": bool(cleared),
        "content": "Theme preview cleared." if cleared else "There was no active theme preview.",
    }


def save_ui_theme_preview(
    preview_id,
    preview_revision,
    name="",
    theme_id="",
    activate=True,
    _context=None,
):
    repository = _repository(_context)
    preview = repository.load_preview()
    if not preview or preview.get("preview_id") != preview_id:
        return {"status": "error", "error": "Theme preview does not exist."}
    if int(preview.get("revision") or 0) != int(preview_revision):
        return {
            "status": "error",
            "error": "Theme preview revision changed; inspect the current preview before saving.",
            "current_preview_revision": int(preview.get("revision") or 0),
        }
    display_name = str(name or preview.get("name") or "自定义主题")
    action = "更新" if theme_id else "保存"
    append_theme_log(
        repository.data_dir,
        "tool_save_submit",
        preview_id=preview_id,
        preview_revision=int(preview_revision),
        theme_id=theme_id,
        activate=bool(activate),
    )
    approval = _approval_response(
        f"{action}自定义主题“{display_name}”{'并设为当前主题' if activate else ''}？",
        title="保存主题",
        details="该操作会写入 Cowork 的主题配置；可随时在“设置 → 外观”切回默认主题。",
        _context=_context,
    )
    if not _is_approved(approval):
        append_theme_log(
            repository.data_dir,
            "tool_save_finish",
            preview_id=preview_id,
            status="cancelled",
        )
        return {
            "status": "cancelled",
            "approval": approval,
            "preview_id": preview_id,
            "content": "Theme save cancelled; the temporary preview remains available.",
        }
    append_theme_log(repository.data_dir, "tool_save_start", preview_id=preview_id)
    try:
        append_theme_log(repository.data_dir, "tool_save_run", preview_id=preview_id)
        result = repository.commit_preview(
            preview_id=preview_id,
            preview_revision=int(preview_revision),
            name=display_name,
            theme_id=str(theme_id or ""),
            activate=bool(activate),
            default_tokens=default_design_tokens(),
        )
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_save_error",
            preview_id=preview_id,
            error=str(exc),
        )
        raise
    append_theme_log(
        repository.data_dir,
        "tool_save_finish",
        preview_id=preview_id,
        theme_id=result["theme"]["id"],
        status="ok",
    )
    return {
        "status": "ok",
        "approval": approval,
        "theme": result["theme"],
        "path": repository.theme_path(result["theme"]["id"]),
        "active": bool(activate),
        "restart_recommended": bool(activate),
        "content": f"Theme '{result['theme']['name']}' saved"
        + (
            " and activated. Recommend restarting Cowork so the new theme takes full effect."
            if activate
            else "."
        ),
    }


def activate_ui_theme(theme_id, _context=None):
    repository = _repository(_context)
    theme = repository.get_theme(str(theme_id or ""))
    if not theme:
        return {"status": "error", "error": f"Theme '{theme_id}' not found."}
    append_theme_log(repository.data_dir, "tool_activate_submit", theme_id=theme["id"])
    approval = _approval_response(
        f"切换到主题“{theme.get('name')}”？",
        title="切换主题",
        details="界面会立即应用该主题；不会修改文档或交付物内容。",
        _context=_context,
    )
    if not _is_approved(approval):
        return {"status": "cancelled", "approval": approval, "content": "Theme activation cancelled."}
    append_theme_log(repository.data_dir, "tool_activate_start", theme_id=theme["id"])
    try:
        append_theme_log(repository.data_dir, "tool_activate_run", theme_id=theme["id"])
        repository.clear_preview()
        snapshot = repository.activate_theme(theme["id"])
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_activate_error",
            theme_id=theme["id"],
            error=str(exc),
        )
        raise
    append_theme_log(repository.data_dir, "tool_activate_finish", theme_id=theme["id"])
    return {
        "status": "ok",
        "approval": approval,
        "active_theme_id": snapshot.active_theme_id,
        "restart_recommended": True,
        "content": (
            f"Theme '{theme.get('name')}' activated. "
            "Recommend restarting Cowork so the theme takes full effect."
        ),
    }


def delete_ui_theme(theme_id, _context=None):
    repository = _repository(_context)
    theme = repository.get_theme(str(theme_id or ""))
    if not theme or theme.get("id") == DEFAULT_THEME_ID:
        return {"status": "error", "error": "Only an existing custom theme can be deleted."}
    append_theme_log(repository.data_dir, "tool_delete_submit", theme_id=theme["id"])
    approval = _approval_response(
        f"删除自定义主题“{theme.get('name')}”？",
        title="删除主题",
        details="删除后无法恢复；如果它正在使用，界面将切回默认主题。",
        _context=_context,
    )
    if not _is_approved(approval):
        return {"status": "cancelled", "approval": approval, "content": "Theme deletion cancelled."}
    append_theme_log(repository.data_dir, "tool_delete_start", theme_id=theme["id"])
    try:
        append_theme_log(repository.data_dir, "tool_delete_run", theme_id=theme["id"])
        snapshot = repository.delete_theme(theme["id"])
    except Exception as exc:
        append_theme_log(
            repository.data_dir,
            "tool_delete_error",
            theme_id=theme["id"],
            error=str(exc),
        )
        raise
    append_theme_log(repository.data_dir, "tool_delete_finish", theme_id=theme["id"])
    return {
        "status": "ok",
        "approval": approval,
        "active_theme_id": snapshot.active_theme_id,
        "content": f"Theme '{theme.get('name')}' deleted.",
    }


TOOL_EXPORTS = [
    {
        "name": "inspect_ui_theme",
        "handler": inspect_ui_theme,
        "description": "Inspect the active Cowork UI theme package, declarative workspace manifest, assets, and token schema.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "Optional theme id; defaults to the active theme."},
                "include_schema": {"type": "boolean", "description": "Include configurable token metadata."}
            },
            "required": []
        },
        "read_only": True,
        "allowed_modes": ["execution"],
        "search_hint": "theme appearance skin font color tokens inspect list UI 主题 换肤 字体 配色"
    },
    {
        "name": "validate_ui_theme",
        "handler": validate_ui_theme,
        "description": "Validate and resolve a Cowork UI theme override without changing the application.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Proposed theme name."},
                "overrides": {"type": "object", "description": "Theme overrides using inspect_ui_theme schema."},
                "workspace_scene": {"type": "object", "description": "The single fixed workspace background scene."},
                "surfaces": {"type": "object", "description": "Declarative surface materials and styles."},
                "components": {"type": "object", "description": "Allowed component presentation overrides."},
                "content": {"type": "object", "description": "Whitelisted display copy overrides."},
                "assets": {"type": "object", "description": "Declared package asset metadata."}
            },
            "required": ["name", "overrides"]
        },
        "read_only": True,
        "allowed_modes": ["execution"],
        "search_hint": "theme validate contrast font colors tokens UI"
    },
    {
        "name": "preview_ui_theme",
        "handler": preview_ui_theme,
        "description": "Apply a temporary Cowork UI theme preview without saving it.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Preview name."},
                "overrides": {"type": "object", "description": "Validated theme overrides."},
                "workspace_scene": {"type": "object", "description": "The single fixed workspace background scene."},
                "surfaces": {"type": "object", "description": "Declarative surface materials and styles."},
                "components": {"type": "object", "description": "Allowed component presentation overrides."},
                "content": {"type": "object", "description": "Whitelisted display copy overrides."}
            },
            "required": ["name", "overrides"]
        },
        "read_only": False,
        "destructive": False,
        "allowed_modes": ["execution"],
        "search_hint": "theme preview apply temporary UI skin"
    },
    {
        "name": "patch_ui_theme_preview",
        "handler": patch_ui_theme_preview,
        "description": "Incrementally patch tokens or the validated declarative manifest on the current preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "preview_id": {"type": "string", "description": "Current preview id."},
                "preview_revision": {"type": "integer", "description": "Current preview revision."},
                "set_overrides": {"type": "object", "description": "Top-level overrides and/or token values to merge."},
                "unset_tokens": {"type": "array", "items": {"type": "string"}, "description": "Token names to remove from the preview."},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["set", "remove"]},
                            "path": {"type": "string"},
                            "value": {}
                        },
                        "required": ["op", "path"]
                    },
                    "description": "Restricted JSON Pointer operations under /workspace_scene, /surfaces, /components, or /content."
                }
            },
            "required": ["preview_id", "preview_revision"]
        },
        "read_only": False,
        "destructive": False,
        "allowed_modes": ["execution"],
        "search_hint": "theme preview patch adjust incremental remove token"
    },
    {
        "name": "import_ui_theme_asset",
        "handler": import_ui_theme_asset,
        "description": "Copy and validate an existing local PNG, JPEG, or WebP into the current theme preview package.",
        "parameters": {
            "type": "object",
            "properties": {
                "preview_id": {"type": "string"},
                "preview_revision": {"type": "integer"},
                "asset_id": {"type": "string", "description": "Stable manifest asset id."},
                "source_path": {"type": "string", "description": "Existing local image path."}
            },
            "required": ["preview_id", "preview_revision", "asset_id", "source_path"]
        },
        "read_only": False,
        "destructive": False,
        "allowed_modes": ["execution"],
        "search_hint": "theme background icon image asset import package"
    },
    {
        "name": "remove_ui_theme_asset",
        "handler": remove_ui_theme_asset,
        "description": "Remove an unreferenced image asset from the current theme preview package.",
        "parameters": {
            "type": "object",
            "properties": {
                "preview_id": {"type": "string"},
                "preview_revision": {"type": "integer"},
                "asset_id": {"type": "string"}
            },
            "required": ["preview_id", "preview_revision", "asset_id"]
        },
        "read_only": False,
        "destructive": False,
        "allowed_modes": ["execution"],
        "search_hint": "theme background icon image asset remove package"
    },
    {
        "name": "clear_ui_theme_preview",
        "handler": clear_ui_theme_preview,
        "description": "Clear the current temporary theme preview and restore the saved active theme.",
        "parameters": {
            "type": "object",
            "properties": {
                "preview_id": {"type": "string", "description": "Optional current preview id."}
            },
            "required": []
        },
        "read_only": False,
        "destructive": False,
        "allowed_modes": ["execution"],
        "search_hint": "theme preview cancel restore clear"
    },
    {
        "name": "save_ui_theme_preview",
        "handler": save_ui_theme_preview,
        "description": "Save the current theme preview as a custom theme after explicit user approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "preview_id": {"type": "string", "description": "Preview id returned by preview_ui_theme."},
                "preview_revision": {"type": "integer", "description": "Exact reviewed preview revision."},
                "name": {"type": "string", "description": "Optional final theme name."},
                "theme_id": {"type": "string", "description": "Existing custom theme id to update; empty creates one."},
                "activate": {"type": "boolean", "description": "Activate after saving."}
            },
            "required": ["preview_id", "preview_revision"]
        },
        "allowed_modes": ["execution"],
        "search_hint": "customize UI theme colors font appearance save custom persist activate approval"
    },
    {
        "name": "activate_ui_theme",
        "handler": activate_ui_theme,
        "description": "Activate a saved Cowork UI theme after explicit user approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "Saved theme id or default."}
            },
            "required": ["theme_id"]
        },
        "allowed_modes": ["execution"],
        "search_hint": "theme activate switch mode skin approval"
    },
    {
        "name": "delete_ui_theme",
        "handler": delete_ui_theme,
        "description": "Delete a saved custom Cowork UI theme after explicit user approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "Custom theme id."}
            },
            "required": ["theme_id"]
        },
        "allowed_modes": ["execution"],
        "search_hint": "theme delete remove custom approval"
    }
]
