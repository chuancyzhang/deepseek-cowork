---
name: system-tools
description: Provides system-level capabilities like executing shell commands and searching file contents.
description_cn: 提供系统级能力，如执行Shell命令和搜索文件内容。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
security_level: high
allowed-tools: ["bash", "grep"]
---

# System Tools Skill

This skill provides powerful system utilities for the agent to interact with the underlying operating system and file contents.

## Capabilities
1. **Bash**: Execute arbitrary system commands in the shell. 
   - Supports all standard OS commands available in the environment.
   - Captures stdout and stderr.
   - **Warning**: Use with caution.
2. **Grep**: Search for text patterns within files in the workspace.
   - Supports regular expressions.
   - Can search recursively.
   - Returns file paths and matching lines with line numbers.

## Usage Guidelines
- **Bash**: Use when you need to run tools that are not available as built-in skills (e.g., `git`, `npm`, system info).
- **Grep**: Use when you need to find code usage, TODOs, or specific text patterns across the codebase.
