# Skill System

## Overview

This project now treats the runtime as having only two first-class objects:

- `tool`: a lightweight atomic executor the AI can call directly
- `experience`: reusable knowledge that guides how tools should be selected and combined

`skill` remains as the public term, but internally it means an **experience package**.
A skill is not a third executable object. It is the packaging format for structured experience.

The default rule is simple:

- AI directly calls `tool`
- AI does not directly call `skill`
- `skill` injects the minimum necessary experience, boundaries, and recommended tools
- `workflow` is experience that has been organized into a recommended sequence of steps

## Core Concepts

### Tool

A tool is the smallest executable unit exposed to the model.

Typical examples:

- execute bash commands
- run Python code
- read or write files
- make HTTP requests
- call legacy public functions from `impl.py`

Tools are operational. They do work and return results.

In the current implementation, public functions from `impl.py` are still supported and are registered as directly callable tools.

### Experience

Experience is the knowledge layer that improves decisions before and during execution.

Experience includes:

- when to use a capability
- when not to use it
- common pitfalls
- lessons learned
- interface details
- safety boundaries
- recommended workflows
- recommended tools

Experience is what makes repeated tasks more reliable over time.

### Skill

A skill is the packaging format for structured experience.

You can think of it as:

- a reusable experience package
- a searchable experience profile
- a gradual-disclosure container for guidance, references, and lessons learned

A skill may reference zero or more tools.
A skill may be purely knowledge-oriented.
A skill may also describe how to use tools without owning execution.

In short:

- `tool` executes
- `skill` packages experience

### Workflow

A workflow is a recommended process described inside a skill.

It is not a standalone runtime protocol for ordinary skills.
It is experience that has been organized into a suggested step order.

A workflow may describe:

- suggested step order
- which tools are usually needed
- common branches
- validation points
- recovery advice

## Runtime Model

The runtime behavior is:

1. The system reads the current conversation and task context.
2. Relevant experience packages are matched by explicit request or semantic relevance.
3. Minimal skill briefs are injected first.
4. The model chooses and calls tools directly.
5. If needed, the system expands the matched skill into fuller guidance, references, or experience entries.
6. Tool results return to the model.
7. The model continues using both tool outputs and the disclosed experience.

This preserves a single execution surface: tools.

## Progressive Disclosure

Progressive disclosure is a hard constraint in this system.

The model should receive only the minimum necessary context at each step.

### Disclosure Levels

Level 0:
- no skill disclosure
- simple tasks use tools directly

Level 1:
- inject a minimal experience brief
- include name, purpose, capability group, recommended tool refs, and a few highlights

Level 2:
- inject the full `SKILL.md` guidance for the matched skill
- used when the skill is explicitly selected or clearly needed

Level 3:
- expand references, structured experience entries, or local directory summaries
- used only when execution genuinely needs that extra context

### Default Restrictions

By default, the system should not:

- inject every skill body
- inject every experience entry
- inject every reference file
- list every skill directory and subdirectory
- dump the entire workspace tree into context

## File Structure

The recommended skill structure is:

```text
<skill>/
  SKILL.md
  skill.json
  experience/
    entries.jsonl      # optional structured experience entries
  references/          # optional
  impl.py              # optional tool source
  scripts/             # optional support files
```

### `SKILL.md`

Human-readable and model-usable experience package text.

Recommended sections:

- Skill purpose
- When to use
- When not to use
- Common pitfalls
- Experience / lessons learned
- Recommended workflow
- Recommended tools
- Interface details
- References

### `skill.json`

Machine-readable metadata for discovery, grouping, and disclosure.

Typical fields:

- `version`
- `name`
- `kind`
- `capability_group`
- `description`
- `tags`
- `triggers`
- `anti_triggers`
- `tool_refs`
- `references`
- `experience_policy`
- `disclosure_level_defaults`
- `workflow`

This file describes the skill as an experience package. It should not be treated as a callable protocol.

### `experience/entries.jsonl`

Optional structured experience entry storage.

Use it for incremental runtime knowledge capture.
Each entry can record fields such as:

- `id`
- `created_at`
- `source`
- `experience_text`
- `tool_name`
- `workspace_hint`
- `task_type`
- `error_pattern`
- `importance`
- `tags`

The default update path is:

1. write a structured entry first
2. sync high-value lessons back into the summary in `SKILL.md`

### `references/`

Optional supporting material such as:

- API docs
- examples
- schemas
- integration notes
- long-form lessons learned

References are not injected by default. They are expanded only when needed.

### `impl.py`

Optional tool source.

Public functions in `impl.py` can still be registered as callable tools.
However, `impl.py` is not the conceptual center of the skill system.
It is just one way to provide tools.

## Experience Ownership

Experience must always belong to a skill.
There are no floating experience objects outside skills.

If a lesson clearly belongs to a specific skill, it should be recorded there.
If a lesson is cross-task or cross-tool and does not naturally belong to a narrower skill, it should go into the dedicated experience package:

- `general-experience`

This keeps the model simple:

- first-class runtime objects are still only `tool` and `experience`
- every experience record is stored through a skill experience package

## Built-in Experience Package Groups

### Basic Execution

File and information interaction:
- `file-system`
- `web-search`

Code and command execution:
- `system-tools`
- `python-runner`

AI and human interaction:
- `interaction`

### Memory / Meta

- `history-query`
- `memory-manager`
- `meta-tools`
- `general-experience`
- `agent-manager`

### System Skills

- `skill_builder`
- `skill-importer`

All of these are still skills in the public sense, but internally they are all experience packages with different roles.

## System Skills

Some capabilities are modeled as system skills, such as:

- `skill_builder`
- `skill-importer`

These still follow the same principle:

- the skill contains structured experience and operational guidance
- the executable surface is exposed through normal tool calls

So even system skills do not introduce a separate skill-call protocol.

## Legacy Compatibility

Older skill directories that only contain `impl.py` are still supported.

Compatibility behavior:

- public functions are registered as callable tools
- the system can create a minimal experience package record for discovery and display
- if `SKILL.md` or `skill.json` exists, those files provide the experience layer

This allows gradual migration without breaking existing behavior.

## Why This Model

This architecture is intentionally biased toward long-term maintainability.

If future LLMs internalize many public skills, raw code wrappers alone become less valuable. What remains valuable is:

- local interface knowledge
- safety boundaries
- operational lessons learned
- task-specific recovery strategies
- guidance for choosing the right tool in the right context

That is why the system treats skills as reusable experience packages rather than executable plugins.

## Practical Example

A simple task:

- user asks to inspect a file
- model directly calls file-related tools
- no extra skill is injected unless needed

A more complex task:

- user asks to integrate with an external service
- the system injects a matching experience package brief
- the package explains auth caveats, retries, output expectations, and known pitfalls
- the model still directly calls tools such as file editing, shell, Python, or HTTP helpers
- if needed, the system expands references or structured experience entries

In both cases, tools do the execution. Skills improve the decision quality by packaging experience.
