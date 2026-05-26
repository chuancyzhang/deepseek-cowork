---
name: command-tools
description: 提供独立的命令执行与 skill 脚本执行能力。
description_cn: 提供独立的命令执行与 skill 脚本执行能力。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: high
allowed-tools: ["bash", "glob", "grep", "run_node_code", "run_skill_script"]
---

# Command Tools

这个 skill 负责工作区内的命令执行和已声明 skill 脚本执行。

## 工具分工

1. `bash`
   - 在当前沙盒运行时中执行命令。
   - 用于运行工作区任务、版本检查、构建命令或轻量命令链。
   - 不属于只读并行工具，不能通过 `parallel_tools` 执行。

2. `glob` / `grep`
   - `glob` 用于按路径模式查找文件。
   - `grep` 用于按内容正则查找文件。
   - 两者是只读搜索工具，多个独立搜索可通过 `parallel_tools` 并行执行。

3. `run_skill_script`
   - 执行目标 skill 在 `script_entries` 中声明的脚本入口。
   - 继续复用现有沙盒运行时和 skill 依赖准备流程。
   - 脚本可能产生副作用，因此保持普通单工具调用。

4. `run_node_code`
   - 使用沙盒 Node.js 在当前工作区执行 JavaScript 代码。
   - 适合运行内联 JS、验证 Node.js 可用性、处理 JSON/前端构建相关的小脚本。
   - 不属于只读并行工具，不能通过 `parallel_tools` 执行。

## 使用约定

- 当需要运行 shell 命令时使用 `bash`。
- 当只需要执行 JavaScript/Node.js 代码时优先使用 `run_node_code`，不要为了内联 JS 套一层 shell。
- 当只需要路径或内容搜索时优先使用 `glob` / `grep`，不要用 shell 包一层。
- `glob` 的递归遍历由工具本身完成；如果已知文件名片段，优先写 `*文件名片段*`，例如 `*AI 赋能数据分析*`。
- 不要为了“搜索所有层级”默认写 `**/文件名*`；当前匹配语义下，这种写法可能漏掉工作区根目录文件。
- 如果已知目录范围，用 `path` 限定目录，再让 `pattern` 专注匹配文件名或相对路径。
- 多个独立只读搜索可以合并到 `parallel_tools`，但命令执行和脚本执行必须单独调用。
- 当某个 skill 已声明脚本入口，并且任务需要复用该 skill 自带执行逻辑时，使用 `run_skill_script`。
