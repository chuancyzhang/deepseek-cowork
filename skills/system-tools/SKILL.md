---
name: system-tools
description: Provides system-level capabilities including shell/file search, app launch/indexing, and unified browser/desktop automation.
description_cn: 提供系统级能力，包括命令执行、文件搜索、应用索引与启动、统一浏览器/桌面自动化。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["system_automate", "bash", "grep", "search_files", "build_app_index", "find_app", "launch_app", "open_with"]
---

# System Tools Skill

This skill provides system utilities for command execution, search, app management, browser automation, and desktop UI automation.

## Capabilities
1. **Bash**: Execute arbitrary system commands in the shell. 
   - Supports all standard OS commands available in the environment.
   - Captures stdout and stderr.
   - **Warning**: Use with caution.
2. **Grep**: Search for text patterns within files in the workspace.
   - Supports regular expressions.
   - Can search recursively.
   - Returns file paths and matching lines with line numbers.
3. **Search Files**: Find files and folders via Everything CLI on Windows.
   - Searches across the entire system when Everything is available.
   - Falls back to Grep within the workspace if Everything is unavailable.
4. **App Index & Launch**:
   - Build local app index from Start Menu/Registry/PATH.
   - Find apps by fuzzy name.
   - Launch apps and open files with selected apps.
5. **Browser & Desktop Automation**:
   - Prefer system default browser and desktop window automation by default.
   - Playwright is only used when explicitly enabled by system config; no automatic downloads.
   - Use `system_automate` for unified multi-step automation.

## Usage Guidelines
- **Bash**: Use when you need to run tools that are not available as built-in skills (e.g., `git`, `npm`, system info).
- **Grep**: Use when you need to find code usage, TODOs, or specific text patterns across the codebase.
- **App Tools**: Use when user asks to find/open applications or open files in target apps.
- **Browser/Desktop Tools**: Use when user asks to automate browser pages or desktop windows via `system_automate`.

## Unified Automation
### system_automate
Run multi-step automation with a single entry point. Steps are routed automatically to browser or desktop actions.

- **steps** (array, required): list of action objects. Supported actions:
  - Web: `goto` / `click` / `fill` / `type` / `scroll` / `wait` / `screenshot`
  - Desktop: `focus_window` / `click_window` / `type` / `scroll_window` / `screenshot_window`
  - Apps: `index` / `find` / `launch` / `open_with`
  - Search: `search` with `kind=everything|grep`
  - Command: `bash`
