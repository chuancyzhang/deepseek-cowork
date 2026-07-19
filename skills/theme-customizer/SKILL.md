---
name: theme-customizer
description: Design, validate, preview, save, activate, and remove Cowork UI themes with built-in theme tools. Use when the user asks to customize the app font, colors, sizing, density, corner radii, visual style, skin, appearance, or theme mode.
---

# Theme Customizer

Use the theme domain tools instead of editing QSS, `DesignTokens`, theme files, or application code.
Each saved user theme is one validated JSON file in Cowork's user theme directory; the settings page scans the same directory.

## Workflow

1. Call `inspect_ui_theme` before proposing changes. Reuse the returned token names and current values.
2. Translate the user's visual intent into the smallest meaningful override set. Prefer semantic tokens over changing every derived state.
3. Call `validate_ui_theme` and fix validation errors before previewing.
4. Call `preview_ui_theme` so the user can inspect the result in the running app.
5. Describe the preview briefly and ask what to adjust. Use `patch_ui_theme_preview` with the current `preview_id` and `preview_revision` for incremental changes. Each patch creates a new revision. Use `clear_ui_theme_preview` when the user rejects it.
6. Call `save_ui_theme_preview` with the exact accepted `preview_id` and `preview_revision` only after the user explicitly accepts that revision. The tool performs its own approval check.
7. Use `activate_ui_theme` or `delete_ui_theme` only for an explicit request; both tools confirm the action.
8. After a successful save-and-activate or activation, explicitly recommend restarting Cowork so every existing surface loads the theme completely.

## Guardrails

- Never claim a theme was applied when a tool returned an error or cancellation.
- When a successful tool result has `restart_recommended: true`, include the restart recommendation in the final user-facing result.
- Do not invent token names. Unknown tokens fail validation.
- Keep body text readable. Treat contrast warnings as actionable, although the user may choose to save.
- Do not style document, PDF, Office, HTML deliverable, image, or visualization content. You may style their Cowork toolbar, tab, loading/error state, and preview shell.
- Do not hide features, reorder navigation, move components, replace icon assets, change interaction behavior, or provide arbitrary QSS.
- Do not use file tools or Python to write theme JSON directly; use theme tools so the file is validated and indexed.
- Prefer the installed system font names returned by `inspect_ui_theme`.
