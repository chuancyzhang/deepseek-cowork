---
name: plan-manager
description: Manage a structured execution plan for planning mode and execution tracking.
description_cn: 管理计划模式下的结构化执行计划与执行进度。
license: Apache-2.0
allowed-tools: update_execution_plan
---

# Plan Manager

Use `update_execution_plan` whenever you need to:

- create or revise the structured plan in planning mode
- mark the plan as ready for user confirmation
- mark one step as in progress during execution
- mark execution steps as completed, blocked, or skipped

The plan must stay concise, structured, and user-readable.
