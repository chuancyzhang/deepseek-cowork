---
name: browser-automation
description: Deep Browser Integration: Get active tab info (URL/Title) and automate browser tasks using Playwright.
description_cn: 深度浏览器集成：获取当前标签页信息（URL/标题）并使用 Playwright 自动化浏览器任务。
metadata:
  author: DeepSeek
  version: "1.0.0"
  permissions: ["screen_access", "internet"]
allowed-tools: [get_active_tab_info, visit_and_screenshot]
---

# Browser Automation Skill

This skill allows the agent to interact with the user's web browser, enabling context awareness (reading active tabs) and automation tasks.

## Features

- **Get Active Tab Info**: Retrieve the URL and Title of the currently active browser window (Chrome, Edge, Firefox).
- **Automated Browsing**: Visit URLs and capture screenshots using a headless browser (via Playwright).

## Configuration

This skill uses `uiautomation` for window detection and `playwright` for automation.

### Requirements

- `playwright`
- `uiautomation`

(Dependencies will be installed automatically by the Skill Manager)

## Commands

### `get_active_tab_info`

Gets the current active tab's URL and title from the user's browser.

**Parameters:** None

**Example:**
```json
{
  "name": "get_active_tab_info",
  "arguments": {}
}
```

### `visit_and_screenshot`

Visits a specific URL and takes a screenshot.

**Parameters:**
- `url` (string): The website URL to visit.

**Example:**
```json
{
  "name": "visit_and_screenshot",
  "arguments": {
    "url": "https://www.example.com"
  }
}
```

## Privacy & Security

- This skill requires screen access permission to detect the active window.
- It requires internet access to visit websites.
- Screenshots are saved locally in the workspace.
