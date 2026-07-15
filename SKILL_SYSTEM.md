# Skill System

Current implementation sync: app version **5.0.0**

This document is aligned with the 5.0.0 release baseline. See [RELEASE_NOTES_5.0.0.md](RELEASE_NOTES_5.0.0.md) for the user-facing scope and compatibility notes.

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
- `config_fields`: optional runtime configuration schema stored in `skill.json`

Cowork can install any standard Agent Skill package that has a root `SKILL.md`
with `name` and `description` frontmatter. Installation preserves that upstream
`SKILL.md` as the authoritative instruction file and generates `skill.json` only
as local Cowork indexing, workbench, and debug metadata.

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

Startup keeps Skill discovery lightweight: sandbox Python DLL/PYD directory
scans are cached across legacy Skill implementations, and heavy specialist
capabilities should be optional or user-installed instead of default built-ins.
Bundled finance support is limited to financial data querying through
`financial-data-akshare`; strategy backtesting is no longer shipped as a
default built-in Skill.

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

### Runtime configuration

`skill.json` may declare `config_fields` with `name`, `label`, `kind`, `required`, `env`, `help`, `placeholder`, `default`, and `options`. `kind: select` renders a fixed-value selector; defaults participate in validation and environment construction. Saved values are stored in local `skill_configs` and injected into script or tool execution through the declared environment variable names. Missing required values fail explicitly before execution. `config_requirements` may express grouped requirements such as token-or-username/password.

`mcp_server_presets` may reference environment names with `{{ENV_NAME}}` placeholders and materialize default-off `stdio` or Streamable HTTP entries. `runtime: skill_python` with exactly one `module` or `entrypoint` starts stdio MCP servers in the owning Skill's isolated Python dependency environment. A preset may also declare managed `auth`; the generated server stores only the Skill configuration reference, while short-lived tokens are resolved at connection time and are not persisted in headers.

### `parallel_tools`

`parallel_tools` exists for one narrow case: several independent read-only calls can run concurrently.

Rules:

- each subcall must target a real tool
- the tool must already be visible or discovered
- the tool must be allowed in the current mode
- the tool must be marked `read_only`
- writes, approvals, package installation, user input, and destructive calls are rejected

## 6. Skill Sources

Bundled optional plugins may own both workflow guidance and narrowly scoped tools. For example, the default-off `ai_skills/visualize` plugin exposes generation and publication tools only while enabled; its published HTML fragments are content-addressed, registered per conversation, sandboxed, and rendered read-only from history when the plugin is later disabled.

Cowork loads capabilities from:

- `skills/`: built-in skills
- `ai_skills/`: bundled optional plugins and user-created skills
- MCP servers: exposed as synthetic tool providers

Bundled optional plugins are read-only, ship disabled by default, and can be enabled from the UI. Tencent Docs, Feishu Docs, DingTalk Docs, WeKnora, ShowDoc MCP, Airflow, and official Superset MCP ship as separate optional skills with independent config fields, script entries, and MCP presets where applicable. User-created skills remain editable, importable, exportable, and deletable.

## 7. Import, Export, and Editing

The Skill Center supports:

- importing a single skill folder
- importing a folder that contains multiple skills
- importing ZIP packages
- installing standard Agent Skill packages through `install_agent_skill`
- exporting one skill as ZIP
- exporting multiple skills as a collection ZIP
- validating files and debugging tools or scripts

Import safety rules:

- ZIP extraction paths must stay inside a temporary directory
- final skill names come from metadata when available
- existing target names are rejected instead of overwritten
- standard Agent Skill installs keep the original root `SKILL.md`; Cowork does not rewrite it into an app-specific template

## 8. Experience Lifecycle

Experience belongs to a skill. Cowork does not maintain floating experience objects outside the skill system.

There are two main update paths:

- **Structured capture**: append reusable lessons into `experience/entries.jsonl`
- **Manual consolidation**: sync high-value guidance back into `SKILL.md`

For conversation-derived work, **沉淀为 Skill** is the review gate. It lets the user choose source messages, turns the selected segment into a draft, lets the user edit the draft, then creates a new user skill or appends structured lessons to an editable existing skill before hot-reloading discovery.

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
