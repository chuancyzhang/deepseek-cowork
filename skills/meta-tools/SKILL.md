---
name: meta-tools
description: Meta skill for AI self-evolution. Invoke when recording lessons learned and improving skill guidance from execution feedback.
description_cn: 面向AI进化的元技能。用于沉淀经验教训并持续优化技能说明。
type: system
created_by: system
allowed-tools: [update_experience]
---

# Meta Tools

Tools for the Agent to self-evolve through execution feedback.

## Tools

### update_experience
Records a successful "experience" or "lesson learned", or updates the description/instructions for a specific skill.
This allows the skill to evolve by refining its capabilities and usage guide.

**When to use:**
- When you encounter an error with a tool and find a workaround (use `experience`).
- When you discover a specific configuration that works best (use `experience`).
- When you realize the skill's description is inaccurate or incomplete (use `description`).
- When the usage instructions (body) need clarification or expansion (use `instructions`).

### update_experience Parameters:
- `skill_name`: The name of the skill to update.
- `experience`: (Optional) A concise, actionable sentence describing the lesson learned (appended to existing).
- `description`: (Optional) A new summary of what the skill does (replaces existing).
- `instructions`: (Optional) The full markdown body explaining how to use the skill (replaces existing).
