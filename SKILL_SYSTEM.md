# Skill System

Current implementation sync: app version **4.9.4**

## 1. Core Idea

Cowork keeps a single execution surface:

- `tool`: callable executor
- `skill`: structured experience package

The model calls tools directly. Skills do not form a second runtime protocol. They supply guidance, boundaries, workflow hints, and lessons learned that improve tool selection.

## 2. What a Skill Contains

A skill can include:

- `SKILL.md`: human-readable guidance
- `skill.json`: metadata for discovery and disclosure
- `impl.py`: optional callable tools
- `experience/entries.jsonl`: optional structured lessons
- `references/`: optional long-form reference material
- `scripts/`: optional assets used through declared script entries

Recommended structure:

```text
<skill>/
  SKILL.md
  skill.json
  impl.py                # optional
  experience/            # optional
  references/            # optional
  scripts/               # optional
  assets/                # optional
```

## 3. Runtime Rules

The runtime flow is:

1. Match the current task to relevant tools and skills.
2. Inject only a minimal skill brief by default.
3. Let the model call tools directly.
4. Expand to full skill guidance only when the task clearly needs it.
5. Reuse disclosed skill context across later turns instead of re-injecting everything.

This keeps prompts smaller and makes the system easier to reason about.

## 4. Progressive Disclosure

Cowork uses four practical disclosure levels:

- **Level 0**: no skill context
- **Level 1**: minimal brief, such as purpose and recommended tools
- **Level 2**: full `SKILL.md`
- **Level 3**: references or structured experience entries

Default behavior:

- do not inject every skill body
- do not inject every experience entry
- do not inject every reference file
- do not expose extra context unless the task needs it

## 5. Discovery and Execution

### `tool_search`

`tool_search` is the discovery entry for:

- deferred tools
- matching skills
- preferred execution hints such as `run_skill_script`

If a matched skill exposes tools, those tools still execute through the normal tool-calling path. If a skill is guidance-only, it appears as experience, not as a callable executor.

### `parallel_tools`

`parallel_tools` exists for one narrow case: several independent read-only calls can run concurrently.

Rules:

- each subcall must target a real tool
- the tool must already be visible or discovered
- the tool must be allowed in the current mode
- the tool must be marked `read_only`
- writes, approvals, package installation, user input, and destructive calls are rejected

## 6. Skill Sources

Cowork loads capabilities from:

- `skills/`: built-in skills
- `ai_skills/`: bundled optional plugins and user-created skills
- MCP servers: exposed as synthetic tool providers

Bundled optional plugins are read-only, ship disabled by default, and can be enabled from the UI. User-created skills remain editable, importable, exportable, and deletable.

## 7. Import, Export, and Editing

The Skill Center supports:

- importing a single skill folder
- importing a folder that contains multiple skills
- importing ZIP packages
- exporting one skill as ZIP
- exporting multiple skills as a collection ZIP
- validating files and debugging tools or scripts

Import safety rules:

- ZIP extraction paths must stay inside a temporary directory
- final skill names come from metadata when available
- existing target names are rejected instead of overwritten

## 8. Experience Lifecycle

Experience belongs to a skill. Cowork does not maintain floating experience objects outside the skill system.

There are two main update paths:

- **Structured capture**: append reusable lessons into `experience/entries.jsonl`
- **Manual consolidation**: sync high-value guidance back into `SKILL.md`

For conversation-derived work, **沉淀为 Skill** is the review gate. It turns a selected conversation segment into a draft, lets the user edit it, and then writes the final skill files.

## 9. Compatibility

Older skill folders that contain only `impl.py` are still supported.

Compatibility behavior:

- public functions can still register as tools
- `SKILL.md` and `skill.json` remain optional for legacy skills
- the system can build a minimal skill record for discovery and display

## 10. Why This Model

This model favors maintainability:

- tools stay simple and directly callable
- skills accumulate operational knowledge
- prompts stay smaller through progressive disclosure
- imported capabilities remain portable
- the user stays in control of what becomes long-term reusable knowledge
