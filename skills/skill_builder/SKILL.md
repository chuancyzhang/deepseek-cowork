---
name: skill-builder
description: Build-focused skill for creating, updating, and installing standard Agent Skills.
type: system
created_by: system
kind: system
allowed-tools: [create_new_skill, update_skill, install_agent_skill]
---

# Skill Builder

This skill is dedicated to skill engineering tasks only.

## When to Use

- Create a new skill from scratch.
- Update an existing skill's code or documentation.
- Install a standard Agent Skill package into the user AI skill directory.
- Prepare a skill so it can later be exported or shared as a portable ZIP package from Skill Center.

## Responsibilities

- **Create Skills**: generate `SKILL.md` and `skill.json`, plus `impl.py` only when the user explicitly wants new lightweight tools.
- **Update Skills**: patch existing AI-generated or built-in skills.
- **Install Agent Skills**: copy a standard Agent Skill package as-is, preserve its original `SKILL.md`, and add Cowork indexing metadata.
- **Keep Packages Portable**: keep reusable references, scripts, and assets inside the skill directory so Skill Center ZIP export can move the complete package.

## Tools

### create_new_skill
Creates or updates an AI-generated skill in `ai_skills`.

### update_skill
Updates an existing skill with optional scope resolution:
- `target_scope="ai_only"`: only modify skill in `ai_skills`
- `target_scope="builtin_only"`: only modify skill in built-in `skills`
- `target_scope="auto"`: prefer AI-generated one, fallback to built-in

### install_agent_skill
Installs a standard Agent Skill package into user `ai_skills`. The source can be a skill directory, ZIP package, or single Markdown `SKILL.md` file.

## Current Runtime Notes

- Skill creation remains knowledge-first: prefer clear experience, boundaries, and tool refs before adding code.
- Agent Skill installation preserves the upstream `SKILL.md`; `skill.json` is generated only as Cowork indexing and workbench metadata.
- The UI-level Skill Center handles ZIP export/import. This skill prepares good package contents; it does not itself write ZIP files.
- Avoid writing cache, build output, or environment folders into skill directories because export intentionally skips those directories.
