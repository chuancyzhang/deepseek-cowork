# Skill System

## Overview

Current implementation sync: app version **4.7.9**.

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
- `沉淀为 Skill` is the manual review path for turning a useful conversation into a skill update
- Skill Center import/export is the portability path for moving skill packages between environments

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

Tools may declare metadata such as `read_only`, `destructive`, `allowed_modes`, aliases, and search hints. This metadata controls lazy discovery, clarifying-mode visibility, selected-skill scope, and whether the tool can be used through `parallel_tools`.

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

### Read-Only Parallelism

`parallel_tools` is an always-visible meta tool for one narrow case: several independent read-only calls should run concurrently.

Rules:

- every subcall must name a real tool
- the tool must already be visible or discovered for the current mode
- the tool must be allowed by the current selected-skill or agent-profile scope
- the tool must be marked `read_only` and must not be destructive
- writes, shell execution, package installation, approvals, user input, experience updates, and sub-agent management stay as normal single calls

Results are returned in the same order as the input calls. If one subcall fails or is denied, the response reports a partial error without executing unsafe writes.

## Manual Feedback Loop

The system now has a human-confirmed loop for promoting completed work into reusable knowledge:

- `更新长期记忆` updates the long-term memory layer by scanning new or changed conversation history, merging it into `memories.md`, and tracking processed history in `memories_update_state.json`.
- `沉淀为 Skill` lets the user choose a current conversation segment and turns it into an editable skill draft before anything is saved.
- New skills are written as `SKILL.md`, `skill.json`, optional `impl.py`, optional Python scripts under `scripts/`, and optional structured entries under `experience/entries.jsonl`.
- Existing skills can be updated by appending structured experience entries or rewriting the visible guidance.
- Skill packages can be exported as ZIP archives and imported back from ZIP files or source folders.

This path is intentionally manual. The model may propose reusable experience, but the user reviews and confirms the draft before it becomes part of the skill system.

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
  assets/              # optional
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

For conversation-derived updates, `沉淀为 Skill` uses this storage model after user confirmation. Appending to an existing skill records reusable lessons as structured entries; rewriting a skill updates the human-readable guidance while preserving prior structured experience where possible. Detected `run_python_code` snippets can be saved as declared `script_entries` so future agents call `run_skill_script` instead of rediscovering the script path.

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

### ZIP Packages

Skill Center single-skill export writes a ZIP archive rooted at the skill directory name.

Skill Center multi-select export writes a collection ZIP whose root contains one folder per selected skill. That collection ZIP can be imported through the same collection import path.

Export excludes cache and build-style directories such as `__pycache__`, `.venv`, `node_modules`, `dist`, and `build`.

Skill Center editing and deletion are intentionally scoped to user-created skills under the writable AppData `ai_skills` root. The UI is split into `Built-in`, `Optional Plugins`, `MCP`, and `Custom` tabs: built-in skills are read-only and stay enabled; optional bundled plugins are read-only, ship under `ai_skills`, default to disabled, and can be toggled; MCP skills stay in their own tab; custom skills remain editable, exportable, and deletable. The workbench can validate skill files, hot-reload after saving, call registered tools, run declared script entries through `run_skill_script`, and debug MCP tools for synthetic MCP skills.

Import accepts:

- a skill source folder
- a skill collection folder that contains multiple child skills
- a ZIP whose root is a single skill folder
- a ZIP whose root is a skill collection folder
- a ZIP whose root directly contains `SKILL.md`, `skill.json`, `impl.py`, `scripts`, `assets`, `references`, or `experience`

Import safety rules:

- ZIP paths are checked so they cannot escape the temporary extraction directory
- the final skill name is read from `skill.json`, then `SKILL.md`, then the folder name
- imported skills land in the writable `ai_skills` root
- existing target names are rejected instead of overwritten
- collection imports scan child directories recursively and import each detected skill independently
- external formats are adapted into Cowork's experience-package model before loading

### Skill Search

`tool_search` still discovers deferred tools, but it now also returns matching skill records in a separate `skills` field.

Rules:

- tool discovery and skill discovery share the same query
- skill matches are case-insensitive
- pure knowledge skills appear only in `skills`; they are not treated as callable tools
- if a matched skill owns real tools, those tools still flow through normal deferred-tool discovery
- imported / agent skills can request `prompt_level = full`, which tells the agent loop to append the full skill prompt on the next model turn
- if a matched skill exposes `script_entries`, `tool_search` should also surface `preferred_tool = run_skill_script` plus the preferred script entry when one is unambiguous
- after a full skill prompt has been disclosed for the current run, later turns reuse that disclosed guidance instead of re-searching the skill directory
- once a script-entry skill is matched, the model should not use `glob`, `grep`, or `bash` just to locate the skill folder or script path

## Experience Ownership

Experience must always belong to a skill.
There are no floating experience objects outside skills.

If a lesson clearly belongs to a specific skill, it should be recorded there.
If a lesson is cross-task or cross-tool and does not naturally belong to a narrower skill, it should go into the dedicated experience package:

- `general-experience`

The conversation-to-skill flow follows the same ownership rule: create a new skill only for a reusable pattern, or update the narrowest existing skill when the lesson clearly belongs there.

This keeps the model simple:

- first-class runtime objects are still only `tool` and `experience`
- every experience record is stored through a skill experience package

## Built-in Experience Package Groups

### Basic Execution

File and information interaction:
- `file-system`

Code and command execution:
- `command-tools`
- `python-runner`

`file-system` owns plain-text file reads/writes and workspace path operations only; it must not parse DOCX, PPTX, XLSX/XLS, or PDF.
`command-tools` owns shell execution, workspace glob/grep search, and declared skill scripts.
`meta-tools` owns `tool_search`, `parallel_tools`, and experience update helpers that help the model find and combine capabilities without widening the execution surface.

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

### Optional Bundled Plugins

Default-off plugins ship under `ai_skills` with `source_type: bundled_plugin` and `default_enabled: false`.

- `document-reader`: unified `document_read` for DOCX, PPTX, XLSX/XLS, and PDF reads. It does not use Pandoc and does not provide write tools.
- `system-tools`: environment, browser, desktop, and app launch automation.
- `web-search`: web search and article reading.
- `financial-data-akshare`: AKShare financial data.
- `browser-automation`, `github-tools`, `yt-dlp-wrapper`: browser, GitHub, and media-download helpers.

Office/PDF writes are intentionally not modeled as fixed tools. The agent should use `run_python_code` with task-appropriate libraries when the user asks to create or modify those formats.

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

A parallel read-only task:

- user asks to compare several files or search several independent patterns
- model discovers the needed read/search tools
- model calls `parallel_tools` with read-only subcalls
- results return in input order and the model synthesizes the answer

A more complex task:

- user asks to integrate with an external service
- the system injects a matching experience package brief
- the package explains auth caveats, retries, output expectations, and known pitfalls
- the model still directly calls tools such as file editing, shell, Python, or HTTP helpers
- if needed, the system expands references or structured experience entries

In both cases, tools do the execution. Skills improve the decision quality by packaging experience.
