---
name: skill-builder
description: Build-focused skill for creating, updating, and converting skills. Invoke when changing skill code, structure, or documentation.
type: system
created_by: system
allowed-tools: [create_new_skill, update_skill, convert_claude_skill]
---

# Skill Builder

This skill is dedicated to skill engineering tasks only.

## When to Use

- Create a new skill from scratch.
- Update an existing skill's code or documentation.
- Convert a Claude skill folder into a Cowork skill.

## Responsibilities

- **Create Skills**: generate `SKILL.md` and `impl.py`.
- **Update Skills**: patch existing AI-generated or built-in skills.
- **Convert Skills**: convert script-based Claude skills into callable Cowork tools.

## Tools

### create_new_skill
Creates or updates an AI-generated skill in `ai_skills`.

### update_skill
Updates an existing skill with optional scope resolution:
- `target_scope="ai_only"`: only modify skill in `ai_skills`
- `target_scope="builtin_only"`: only modify skill in built-in `skills`
- `target_scope="auto"`: prefer AI-generated one, fallback to built-in

### convert_claude_skill
Converts a Claude skill folder (with `scripts/` and `SKILL.md`) into a Cowork skill in `ai_skills`.
