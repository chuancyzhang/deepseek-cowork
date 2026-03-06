---
name: system-tools
description: Provides system-level capabilities including shell/file search, app launch/indexing, and browser/desktop automation.
description_cn: 提供系统级能力，包括命令执行、文件搜索、应用索引与启动、浏览器/桌面自动化。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["bash", "grep", "search_files", "build_app_index", "find_app", "launch_app", "open_with", "open_url", "screenshot_url", "run_browser_steps", "ui_focus_window", "ui_click", "ui_type", "ui_scroll"]
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
   - Open URL, screenshot webpage, run browser steps with Playwright.
   - Focus window, click/type/scroll desktop controls via pywinauto.

## Usage Guidelines
- **Bash**: Use when you need to run tools that are not available as built-in skills (e.g., `git`, `npm`, system info).
- **Grep**: Use when you need to find code usage, TODOs, or specific text patterns across the codebase.
- **App Tools**: Use when user asks to find/open applications or open files in target apps.
- **Browser/Desktop Tools**: Use when user asks to automate browser pages or desktop windows.
