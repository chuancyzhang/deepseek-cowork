---
name: skill_creator
description: Allows creating new skills dynamically.
description_cn: 允许动态创建新的功能模块。
license: Apache-2.0
allowed-tools: create_new_skill
---

# Skill Creator Skill

This skill allows creating new skills dynamically.

## Tools

### create_new_skill
Create a new local skill with a specified tool.

- **skill_name** (string, required): The name of the new skill (folder name).
- **description** (string, required): Description of the skill.
- **tool_name** (string, required): The name of the tool function.
- **tool_description** (string, required): Description of the tool.
- **tool_code** (string, required): The python code for the tool function.
