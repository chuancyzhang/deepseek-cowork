---
name: automation-tools
description: Manage SOP templates, scheduled automation tasks, and run history through AI-callable tools.
description_cn: 让 AI 可以管理 SOP 模板、定时自动化任务和执行历史。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: medium
allowed-tools: [list_automation_templates, upsert_automation_template, list_automation_tasks, upsert_automation_task, set_automation_task_enabled, delete_automation_task, run_automation_task_now, list_automation_run_history]
---

# Automation Tools

This skill lets the AI inspect and manage the app's existing automation system instead of inventing a second workflow layer.

## Capabilities
1. `list_automation_templates`: inspect available SOP templates.
2. `upsert_automation_template`: create or update a reusable SOP template.
3. `list_automation_tasks`: inspect configured scheduled tasks.
4. `upsert_automation_task`: create or update a scheduled automation task.
5. `set_automation_task_enabled`: enable or pause an existing task.
6. `delete_automation_task`: delete a task after explicit user approval.
7. `run_automation_task_now`: trigger a task after explicit user approval when the current runtime can launch it.
8. `list_automation_run_history`: inspect recent automation runs and statuses.

## Usage Guidelines
- Reuse the existing `sop_templates`, `automation_tasks`, and `automation_run_history` config structures.
- Prefer updating an existing template or task by `id`; fall back to exact name matches only when the id is unknown.
- New tasks default to disabled unless the user clearly asked to enable them.
- Deleting or running a task requires explicit user approval.
- If the current runtime cannot launch automation directly, explain that configuration succeeded but direct execution is unavailable here.
- These tools are not read-only except for the list operations, so only the list operations are safe candidates for `parallel_tools`.
