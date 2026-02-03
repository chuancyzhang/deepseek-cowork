---
name: skill-creator
description: MANDATORY tool for creating and updating SKILLs. Supports multi-tool skills and skill evolution.
type: system
created_by: system
allowed-tools: create_new_skill, convert_claude_skill
---

# Skill Creator

This skill helps you create new SKILLs or update existing ones for the workspace.

## When to Use

**CRITICAL: You MUST invoke this skill IMMEDIATELY as your FIRST action when:**
- User wants to create a new skill.
- User wants to add a custom skill to the workspace.
- User wants to update an existing skill (add tools, fix code, or improve documentation).
- User wants to convert an existing Claude Skill folder into a Cowork Skill.
- User mentions creating/adding/making any skill.

## Capabilities

- **Multi-tool Skills**: Can create skills that contain multiple related tools (functions).
- **Skill Updates**: Can update existing skills (overwrites code and updates documentation).
- **Skill Conversion**: Can convert standard Claude Skills (folders with scripts and SKILL.md) into Cowork Skills.
- **Knowledge Injection**: Can include detailed usage guidelines, flows, and examples in the skill documentation.

## SKILL Structure

A valid SKILL requires:

1. **Directory**: `.trae/skills/<skill-name>/` (or `ai_skills/<skill-name>`)
2. **File**: `SKILL.md` inside the directory (Documentation & Metadata)
3. **File**: `impl.py` inside the directory (Python Implementation)

## Tools

### create_new_skill

Creates a new skill or updates an existing one.

**Parameters:**

- `skill_name` (str): Unique identifier for the skill (alphanumeric + hyphens).
- `description` (str): concise description covering what the skill does. Used in the skill's frontmatter.
- `tools_list` (list): A list of dictionaries defining the tools in this skill.
    - Format: `[{"name": "tool_name", "description": "What this tool does"}]`
- `tool_code` (str): The COMPLETE Python code for `impl.py`. Must contain definitions for ALL tools listed in `tools_list`.
- `usage_guidelines` (str): Detailed markdown content for `SKILL.md`. Should include:
    - How to use the tools.
    - The sequence/flow of using multiple tools.
    - Examples and best practices.
- `description_cn` (str, optional): Chinese description of the skill.

**Example Usage:**

To create a "git-helper" skill with two tools:

```python
create_new_skill(
    skill_name="git-helper",
    description="Helper for complex git operations.",
    tools_list=[
        {"name": "git_log_summary", "description": "Get a summary of git logs."},
        {"name": "git_check_status", "description": "Check current git status."}
    ],
    tool_code="def git_log_summary():\n    ...\n\ndef git_check_status():\n    ...",
    usage_guidelines="## Usage Flow\n1. Call `git_check_status` to see if clean.\n2. Call `git_log_summary` to see history."
)
```

### convert_claude_skill

Converts a standard Claude Skill folder (containing `SKILL.md` and `scripts/`) into a Cowork Skill.
It generates an `impl.py` that wraps the original scripts as Python tools.

**Parameters:**

- `source_path` (str): Absolute path to the existing Claude Skill folder.
- `skill_name` (str, optional): Name for the new skill. Defaults to source folder name.

**Example Usage:**

```python
convert_claude_skill(
    source_path="/path/to/claude/skills/pdf-processing"
)
```
