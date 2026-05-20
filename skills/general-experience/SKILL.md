---
name: general-experience
description: Cross-task runtime lessons, execution patterns, and reusable operational experience.
license: Apache-2.0
type: system
created_by: system
kind: knowledge
capability_group: memory-meta
experience: []
allowed-tools: [record_general_experience]
---

# Skill Purpose
Use this experience package to capture runtime lessons that do not naturally belong to a narrower skill.

## When to Use
Use it for cross-task execution patterns, error recovery rules, environment caveats, and general collaboration lessons.

## When Not to Use
Do not store narrow tool guidance here when it clearly belongs to a more specific skill.

## Common Pitfalls
Avoid promoting one-off debugging notes into durable lessons unless they are likely to recur.

## Experience / Lessons Learned
Add reusable cross-task lessons here as the system evolves.

## Recommended Workflow
1. Record the lesson as a structured experience entry.
2. Promote repeated or high-value lessons into the summary.
3. Keep narrow lessons in the most specific skill possible.

## Recommended Tools
- `record_general_experience`
- `update_experience`
- `parallel_tools` for independent read-only evidence gathering before deciding whether a lesson is general enough to record

## Interface Details
Use `record_general_experience` when a lesson clearly belongs to cross-task runtime knowledge instead of a narrower skill.
Use `update_experience` when you also want a shared entry point that can route to a specific skill or to general experience.

## Constraints and Safety Rules
Do not put sensitive secrets or ephemeral one-off debugging dumps into this experience package.

## Current Runtime Notes
- Prefer the narrowest owning skill. Use this package only for patterns that apply across multiple skills or task types.
- Experience updates are write operations and must not be batched through `parallel_tools`.
- User-reviewed `沉淀为 Skill` remains the preferred path for substantial reusable workflow knowledge.
