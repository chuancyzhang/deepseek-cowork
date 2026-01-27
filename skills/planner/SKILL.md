---
name: planner
description: Deep Planning Mode tools for proposing and validating execution plans.
description_cn: 深度规划模式工具，用于生成和验证执行计划。
metadata:
  author: DeepSeek
  version: "1.0.0"
  permissions: ["user_interaction"]
allowed-tools: [propose_plan]
---

# Planner Skill

This skill enables the "Deep Planning Mode" where the agent proposes a structured plan to the user for approval before executing complex tasks.

## Features

- **Plan Proposal**: Present a multi-step plan to the user with reasoning.
- **User Validation**: Pause execution until the user approves or rejects the plan.

## Usage Guidelines

- This skill is typically invoked automatically when the agent determines that a request is complex and "Deep Planning Mode" is enabled in settings.
- The `propose_plan` tool halts execution flow (via `ask_user` interaction bridge) until the user responds in the UI.

## Commands

### `propose_plan`

Proposes a detailed execution plan to the user for approval.

**Parameters:**
- `title` (string): A short title for the plan.
- `steps` (array of strings): A list of steps describing what will be done.
- `reasoning` (string): Explanation of why this plan is chosen.

**Example:**
```json
{
  "name": "propose_plan",
  "arguments": {
    "title": "Data Migration Plan",
    "steps": ["Backup database", "Run migration script", "Verify data integrity"],
    "reasoning": "Standard procedure to ensure no data loss."
  }
}
```
