---
name: gui-automation
description: Automates browser steps and basic desktop UI actions. Invoke when user asks to click/type/scroll in web pages or desktop apps.
description_cn: 自动化浏览器步骤与基础桌面控件操作。用户需要网页/桌面点击、输入、滚动时使用。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["open_url", "screenshot_url", "run_browser_steps", "ui_focus_window", "ui_click", "ui_type", "ui_scroll"]
---

# GUI Automation

提供浏览器自动化与基础桌面控件操作能力。

## Tools

### open_url
用默认浏览器打开指定 URL。

### screenshot_url
使用 Playwright 打开网页并截图。

### run_browser_steps
执行浏览器自动化步骤（点击、输入、滚动、等待、截图）。

### ui_focus_window
聚焦指定窗口。

### ui_click
点击指定窗口内控件或窗口本身。

### ui_type
向指定窗口内控件输入文本。

### ui_scroll
对指定窗口滚动。
