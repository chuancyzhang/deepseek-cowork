---
name: theme-customizer
description: Design, validate, preview, save, activate, and remove Cowork UI themes with built-in theme tools. Use when the user asks to customize the app font, colors, sizing, density, corner radii, visual style, skin, appearance, or theme mode.
---

# Theme Customizer

Use the theme domain tools instead of editing QSS, `DesignTokens`, theme packages, or application code.
Each saved user theme is one validated `.cowork-theme` package. Its manifest has one fixed `workspace_scene` background owner plus semantic tokens, surface materials, controlled component layout, icon presentation, visibility, and whitelisted display copy.

## Workflow

1. Call `inspect_ui_theme` before proposing changes. Reuse the returned token names and current values.
2. Translate the user's visual intent into the smallest meaningful token and manifest override set. Put every workspace image, grid, stripe, dot, or noise layer in the single `workspace_scene`; use surface materials only for transparent, tinted, or opaque functional regions.
3. When an existing local PNG, JPEG, GIF, or WebP is needed, create a preview first and call `import_ui_theme_asset`. Static PNG/JPEG/WebP assets may be referenced by `workspace_scene` or an icon; GIF and animated WebP assets may only be referenced by the single `workspace_scene` image layer. Cowork does not generate raster images in this workflow.
4. Call `validate_ui_theme` and fix validation errors before previewing.
5. Call `preview_ui_theme` so the user can inspect the result in the running app.
6. Describe the preview briefly and ask what to adjust. Use `patch_ui_theme_preview` with the current `preview_id` and `preview_revision` for incremental changes. Each patch or asset change creates a new revision. Use `clear_ui_theme_preview` when the user rejects it.
7. Call `save_ui_theme_preview` with the exact accepted `preview_id` and `preview_revision` only after the user explicitly accepts that revision. The tool performs its own approval check.
8. Use `activate_ui_theme` or `delete_ui_theme` only for an explicit request; both tools confirm the action.
9. After a successful save-and-activate or activation, explicitly recommend restarting Cowork so every existing surface loads the theme completely.

## Guardrails

- Never claim a theme was applied when a tool returned an error or cancellation.
- When a successful tool result has `restart_recommended: true`, include the restart recommendation in the final user-facing result.
- Do not invent token names. Unknown tokens fail validation.
- Keep body text readable. Treat contrast warnings as actionable, although the user may choose to save.
- Do not style document, PDF, Office, HTML deliverable, image, or visualization content. You may style their Cowork toolbar, tab, loading/error state, and preview shell.
- Only use surface and component IDs returned by `inspect_ui_theme`. Controlled visibility, icon replacement, and within-region layout are allowed; protected components cannot be hidden.
- The homepage theme-capability reminder is `home.theme_reminder`; set its component `visible` field to `false` when the user asks to hide it. Never change the reminder's action or prefilled prompt.
- Never attach images or procedural layers to `surfaces`. The fixed workspace scene is the only background owner, and nested backgrounds fail validation instead of being silently ignored.
- Never reference an animated asset from a component icon. GIF and animated WebP are restricted to `workspace_scene`; APNG is unsupported.
- The Windows title bar is system-owned. `brand.title` may change only its displayed caption; do not generate `window.titlebar`, `titlebar.*`, or `brand.tagline` overrides.
- Never add action, route, command, signal, prompt, arbitrary QSS, absolute positioning, overlap, or cross-region movement. Display-copy overrides do not change card prompts or dedicated actions.
- Only import an image the user supplied or explicitly identified on the local filesystem. If the requested visual needs a raster source and none exists, ask for one instead of claiming it was generated.
- Do not use file tools or Python to write theme JSON directly; use theme tools so the file is validated and indexed.
- Prefer the installed system font names returned by `inspect_ui_theme`.
