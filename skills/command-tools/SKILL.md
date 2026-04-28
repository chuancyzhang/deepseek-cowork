---
name: command-tools
description: 提供独立的命令执行与 skill 脚本执行能力。
description_cn: 提供独立的命令执行与 skill 脚本执行能力。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["bash", "glob", "grep", "run_skill_script"]
---

# Command Tools

这个 skill 负责工作区内的命令执行和已声明 skill 脚本执行。

## 工具分工

1. `bash`
   - 在当前沙盒运行时中执行命令。
   - 用于运行工作区任务、版本检查、构建命令或轻量命令链。

2. `run_skill_script`
   - 执行目标 skill 在 `script_entries` 中声明的脚本入口。
   - 继续复用现有沙盒运行时和 skill 依赖准备流程。

## 使用约定

- 当需要运行 shell 命令时使用 `bash`。
- 当某个 skill 已声明脚本入口，并且任务需要复用该 skill 自带执行逻辑时，使用 `run_skill_script`。
