---
name: command-tools
description: 提供独立的命令执行、路径搜索、内容搜索与 skill 脚本执行能力。
description_cn: 提供独立的命令执行、路径搜索、内容搜索与 skill 脚本执行能力。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["bash", "glob", "grep", "run_skill_script"]
---

# Command Tools

这个 skill 负责工作区内的命令执行、路径搜索、内容搜索和已声明 skill 脚本执行。

## 工具分工

1. `bash`
   - 在当前沙盒运行时中执行命令。
   - 用于运行工作区任务、版本检查、构建命令或轻量命令链。

2. `glob`
   - 只做工作区内路径/文件名模式搜索。
   - 不搜索文件内容。
   - 用于查找匹配模式的文件，例如 `*.py`、`src/*.ts`、`*test*`。

3. `grep`
   - 只做工作区内文件内容搜索。
   - 支持正则表达式，返回相对路径、行号和匹配行。
   - 适合查找代码用法、TODO、配置项和文本片段。

4. `run_skill_script`
   - 执行目标 skill 在 `script_entries` 中声明的脚本入口。
   - 继续复用现有沙盒运行时和 skill 依赖准备流程。

## 使用约定

- 当需要“找文件路径”时优先使用 `glob`，不要把它当作内容搜索工具。
- 当需要“搜文件内容”时使用 `grep`。
- 当需要运行 shell 命令时使用 `bash`。
- 当某个 skill 已声明脚本入口，并且任务需要复用该 skill 自带执行逻辑时，使用 `run_skill_script`。
