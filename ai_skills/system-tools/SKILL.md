---
name: system-tools
description: Provides environment and application automation, including app launch/indexing and unified browser/desktop automation.
description_cn: 提供环境与应用自动化能力，包括应用索引与启动、统一浏览器/桌面自动化。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["system_automate", "build_app_index", "find_app", "launch_app", "open_with"]
---

# System Tools Skill

This skill provides environment automation for app management, browser automation, and desktop UI automation.

## Capabilities
1. **App Index & Launch**:
   - Build local app index from Start Menu/Registry/PATH.
   - Find apps by fuzzy name.
   - Launch apps and open files with selected apps.
2. **Browser & Desktop Automation**:
   - Prefer system default browser and desktop window automation by default.
   - Browser web automation uses native CDP (Chrome DevTools Protocol) with local browser.
   - Use `system_automate` for unified multi-step automation.

## Usage Guidelines
- **App Tools**: Use when user asks to find/open applications or open files in target apps.
- **Browser/Desktop Tools**: Use when user asks to automate browser pages or desktop windows via `system_automate`.
- **Command/Search/Script Tasks**: Use the standalone `command-tools` skill for `bash` / `glob` / `grep` / `run_skill_script`.
- **Parallelism**: Desktop, browser, app launch, and screenshot automation can change external state or rely on UI focus, so do not run it through `parallel_tools`.

## Unified Automation
### system_automate
Run multi-step automation with a single entry point. Steps are routed automatically to browser or desktop actions.

- **steps** (array, required): list of action objects. Supported actions:
  - Web: `goto` / `click` / `fill` / `type` / `scroll` / `wait` / `screenshot`
  - Desktop: `focus_window` / `click_window` / `type` / `scroll_window` / `screenshot_window`
  - Apps: `index` / `find` / `launch` / `open_with`

## Current Runtime Notes
- The desktop app follows a compact right-drawer UI. Use app automation only when the user explicitly asks to interact with external apps or browser windows.
- `build_app_index` and `find_app` are safer discovery steps before `launch_app` or `open_with`.
- God Mode affects how far system automation can reach; without it, keep actions within normal workspace and app boundaries.
