---
name: app-indexer
description: Builds and queries a local index of installed apps and executables. Invoke when user asks to find or locate an application.
description_cn: 构建并查询本地应用索引，用于定位应用或可执行文件。用户要找应用时使用。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
security_level: medium
allowed-tools: ["build_app_index", "find_app"]
---

# App Indexer

用于扫描开始菜单、注册表卸载列表和 PATH，构建应用名到可执行文件路径的索引，并提供检索能力。

## Tools

### build_app_index
构建或刷新应用索引并返回 JSON 结果。

### find_app
根据关键词检索应用，返回候选列表。
