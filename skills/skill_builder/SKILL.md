---
name: skill-builder
description: Build-focused skill for creating and updating knowledge-first skills that reference lightweight tools.
type: system
created_by: system
kind: system
allowed-tools: [create_new_skill, update_skill, convert_claude_skill, convert_openclaw_skill, convert_external_skill]
---

# Skill Builder

This skill is dedicated to skill engineering tasks only.

## When to Use

- Create a new skill from scratch.
- Update an existing skill's code or documentation.
- Convert a Claude or OpenClaw skill folder into a Cowork skill.
- Prepare a skill so it can later be exported or shared as a portable ZIP package from Skill Center.

## Responsibilities

- **Create Skills**: generate `SKILL.md` and `skill.json`, plus `impl.py` only when the user explicitly wants new lightweight tools.
- **Update Skills**: patch existing AI-generated or built-in skills.
- **Convert Skills**: adapt external skills into Cowork's tool-plus-experience model.
- **Keep Packages Portable**: keep reusable references, scripts, and assets inside the skill directory so Skill Center ZIP export can move the complete package.

## Tools

### create_new_skill
Creates or updates an AI-generated skill in `ai_skills`.

### update_skill
Updates an existing skill with optional scope resolution:
- `target_scope="ai_only"`: only modify skill in `ai_skills`
- `target_scope="builtin_only"`: only modify skill in built-in `skills`
- `target_scope="auto"`: prefer AI-generated one, fallback to built-in

### convert_claude_skill
Converts a Claude skill folder into a Cowork skill in `ai_skills`.

### convert_openclaw_skill
Converts an OpenClaw skill folder into a Cowork skill in `ai_skills`.

### convert_external_skill
Auto-detects an external skill format and adapts it into the Cowork skill system.

## Current Runtime Notes

- Skill creation remains knowledge-first: prefer clear experience, boundaries, and tool refs before adding code.
- The UI-level Skill Center handles ZIP export/import. This skill prepares good package contents; it does not itself write ZIP files.
- Avoid writing cache, build output, or environment folders into skill directories because export intentionally skips those directories.
