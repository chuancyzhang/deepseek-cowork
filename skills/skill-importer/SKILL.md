---
name: skill-importer
description: Import a source folder into a knowledge-first skill by analyzing the folder with LLM assistance and generating SKILL.md plus skill.json.
type: system
created_by: system
kind: system
allowed-tools: [analyze_skill_source_folder, generate_skill_from_folder]
---

# Skill Purpose
Use this skill when a user provides a folder and wants it converted into a reusable Cowork skill.

## When to Use
Use it to inspect a source folder, summarize its capabilities, suggest which lightweight tools are relevant, and generate skill metadata after user confirmation.

## When Not to Use
Do not use it for simple one-off file edits or when the user only wants an explanation of the folder contents.

## Interface Details
`analyze_skill_source_folder` returns a JSON preview describing the inferred skill name, kind, candidate tool refs, references, risks, and generated metadata draft.

`generate_skill_from_folder` copies the source folder into `ai_skills/<name>` and writes `SKILL.md` plus `skill.json` centered on explanation, tool refs, and recommended workflow.

## Constraints and Safety Rules
Always preview before writing. Do not overwrite an existing generated skill unless the user has confirmed the update path.

## Common Pitfalls
Folders often contain multiple scripts and partial documentation. Prefer documenting the smallest useful tools instead of turning the entire folder into one big executable workflow.

## Experience / Lessons Learned
Keep the generated skill knowledge-first: document interfaces and caveats before introducing new executable wrappers.
