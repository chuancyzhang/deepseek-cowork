---
name: app-launcher
description: Launches apps or opens files with a chosen app using the local app index. Invoke when user asks to open or launch an application.
description_cn: 启动应用或指定应用打开文件，依赖本地应用索引。用户要打开应用时使用。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
security_level: high
allowed-tools: ["launch_app", "open_with"]
---

# App Launcher

通过本地应用索引或系统 PATH 启动应用，或用指定应用打开文件。

## Tools

### launch_app
根据应用名启动应用，支持简单模糊匹配。

### open_with
用指定应用打开文件。
