# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

DeepSeek Cowork is a Windows desktop agent workspace built around DeepSeek-style reasoning plus tool use. It combines chat, project-scoped file work, skill-based capability extension, automation, and optional daemon execution in one PySide6 app.

This project is a personal exploration and is not an official DeepSeek product.

Current app version: **5.0.0**

## What It Does

- Run a local desktop agent that can read files, use tools, and complete multi-step tasks.
- Keep project work inside a selected workspace boundary.
- Extend capabilities through built-in skills, bundled optional plugins, user skills, and MCP tools.
- Manage reusable agents, prompt-based automation tasks, scheduled runs, and long-term memory from the UI.
- Work in a light, compact Linear-inspired desktop surface with flat navigation, clear focus states, and an on-demand context drawer.
- Preview common deliverables directly inside the app, including Markdown, HTML, images, PDF, DOCX, PPTX, and XLSX.
- Use **PPT Agent** from PPT Mode to turn topics, source files, templates, existing conversations, or visual-slide requests into presentation-shaped HTML drafts before exporting.
- Turn any assistant reply into a previewable office draft, with Free, PPT, Design, and DOCX output profiles; office-draft generation turns are collapsed into a task card by default while result files stay visible below the card.
- Generate PPTX, DOCX, or PDF from an HTML draft in a folded task card with local action feedback; the drawer stays usable or closable while completion returns through toast and refreshed deliverable shortcuts.
- Filter deliverables by registered file category and sort them by modified time, name, or size from two explicit controls. In-app notices and confirmations use Linear-style surfaces, while closable notifications stack at the bottom right and pause on hover.

## Product Surface

- **Conversation + project model**: direct chats get their own workspace under `conversation_workspaces/<session_id>/` next to the executable, or you can explicitly bind an empty conversation to a project workspace. Opening, adding, dragging, or browsing a project updates the file view without silently moving an existing conversation into that project; newly submitted conversations appear in the sidebar immediately while the background save queue catches up.
- **Conversation-scoped model choice**: the model selector is saved per conversation and applies to the next turn. All model channels may be removed and saved; an unconfigured composer routes the user to Model services.
- **Transparent task process**: Thinking folds into a compact process row by default. Expanding it restores the original reasoning and tool calls in execution order, while parameters, code, stdout, stderr, and tracebacks remain in the observability drawer. Historical conversations restore the same sequence without replaying animations.
- **Compact composer tools**: the anchored `+` popover is a single in-window overlay that adds files, prioritizes skills, or captures experience as a Skill without spawning native helper windows.
- **Restorable conversation state**: switching conversations preserves drafts, scroll position, drawer state, and expanded process nodes. Editing an earlier user message happens in place and regenerates from that point without deleting workspace files.
- **Prompt attachments**: paste clipboard images directly into the composer, review or remove their thumbnails, and send them as vision parts on vision-capable models. Text-only models block submission and guide model switching instead of silently dropping images. Small text files are inlined; large or non-text files retain explicit path/size guidance.
- **Office draft actions**: generate Free, PPT, Design, or DOCX-style HTML drafts from an assistant reply, with the generation process folded into a compact task card and deliverable shortcuts kept outside the folded process.
- **PPT Mode / PPT Agent**: a home card and the composer Agent picker provide the presentation workflow. PPT Agent selects between the default PPT HTML draft flow and built-in html-ppt strategies for Guizang PPT Skill, Frontend Slides, and Huashu Design, then registers the result as an HTML deliverable for the existing PPTX/DOCX/PDF conversion path.
- **Right-side context drawer**: when explicit outputs exist, the drawer opens on a compact Deliverables view with an on-demand **Browse workspace** route; otherwise it opens directly on the real folder tree. Deliverables come from generated, converted, published, or user-marked results rather than extension-wide scanning. Search/filter state and navigation position are restored, while detail mode keeps Preview/Source and **Generate file…** actions.
- **Linear project sidebar**: projects preview the five most recent conversations; actions appear only on hover or keyboard focus, with the same menu available by right-click. Browsing a project does not change conversation activity time.
- **Structured observability**: runtime context, tool calls, and technical details remain first-level views, with readable Python/Shell/JSON arguments and separated output, error, and traceback rendering.
- **Token usage chip**: view accumulated conversation tokens and cached input usage from a light click-to-open detail popover.
- **Stable prompt cache prefix**: automatic skill-context matches are used only during the active turn and are not persisted into conversation history, keeping later prompt prefixes easier for providers to cache.
- **Lightweight startup**: the main workspace becomes interactive before skill indexing, MCP tool probing, deliverable WebEngine preview creation, and deeper sidebar history pages finish in the background or on first use.
- **Main-content management pages**: the sidebar keeps only Capabilities, Automation, and Settings as stable destinations. They switch inside the main content area and restore the prior conversation instead of opening large blocking dialogs.
- **Settings center**: manage models, agents, personality and memory, workspace defaults, archived projects and conversations, MCP servers, enterprise messaging, and runtime components through consistent list/detail pages; save actions respond only to semantic configuration changes, not background logs or connection tests.
- **Home toolkit prompt**: new conversations point users to Settings for the document and data-analysis toolkits before Office/PDF, spreadsheet, analysis, and visualization work.
- **Automation center**: manage prompt-based tasks, schedules, run history, referenced skills, and optional Agent bindings with a compact empty state and an embedded task editor; each task saves independently.
- **Skill center**: search, filter, enable, configure, and debug skills in a master/detail layout without restarting the app.

## Skill Model

Cowork treats tools as the only direct execution surface.

- `tool`: executable capability the model can call directly
- `skill`: structured experience package that guides when and how to use tools

Skills can provide guidance only, or guidance plus tools. Built-in skills live in `skills/`. Bundled optional plugins and user-created skills live in `ai_skills/`. Cowork can install standard Agent Skill packages as user AI skills, preserving the original root `SKILL.md` and generating `skill.json` only for local discovery, workbench, and debug metadata. PPT Agent's Guizang PPT Skill, Frontend Slides, and Huashu Design strategies are bundled as real `ai_skills` packages, so their upstream `SKILL.md`, resources, and source metadata can enter the runtime context and observability view when selected. Skills may declare `config_fields`; the workbench renders those fields in a configuration tab and injects saved values into script/tool execution through explicit environment variables. Skills may also declare `mcp_server_presets`, letting saved skill credentials generate or update local MCP server entries without hardcoding secrets into bundled files.

See [SKILL_SYSTEM.md](SKILL_SYSTEM.md) for the detailed model.

## Installation

### Windows executable

1. Download the latest package from [Releases](../../releases).
2. Unzip it.
3. Run `deepseek-cowork.exe`.

### Run from source

Prerequisite: Python 3.10+

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### Packaging runtime bootstrap

Before running `pyinstaller deepseek-cowork.spec`, fetch the pinned runtime bundle:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

This prepares the packaged Git Bash runtime. Node.js is installed as an optional runtime component from Settings.

## Basic Usage

1. Open **Settings** and configure a model service.
2. Start a direct chat with its auto-created conversation workspace, or explicitly attach an empty conversation to a project.
3. Ask the agent to inspect files, edit code, generate reports, or run automation.
4. For document, spreadsheet, or data-analysis tasks, use the home prompt to open **Settings → Components & dependencies** and install the document and data-analysis toolkits.
5. Enable optional skills from **Skill Center** or install a standard Agent Skill with `install_agent_skill`; Tencent Docs, Feishu Docs, DingTalk Docs, WeKnora, ShowDoc MCP, Airflow, and official Superset MCP are bundled as separate optional skills. Their workbench pages expose configuration fields, read-only files, Tool debugging, script entries, and MCP preset generation where applicable.
6. Use the home **PPT Agent** card or the built-in **PPT Agent** inside the Agent module when you want a focused presentation workflow; choose automatic strategy selection, a web/technical/business/template preference, optional source files, and an optional PPTX template.
7. Use the drawer to browse files, preview deliverables, convert HTML drafts, and inspect tool activity.
8. Maintain long-term context under **Settings → Personality & Memory**, or choose **沉淀为 Skill** from the `+` popover or an assistant response. The two-step flow selects the save mode and source messages, then generates a draft in the background. A persistent task row asks the user to review and save it instead of opening an interrupting preview automatically.

## Architecture

- `main.py`: PySide6 desktop UI
- `core/agent.py`: reasoning loop and tool orchestration
- `core/daemon.py`: optional headless execution path
- `core/skill_manager.py`: skill loading, disclosure, and tool registry
- `core/mcp_client.py`: MCP `stdio` and Streamable HTTP integration
- `core/automation_manager.py`: prompt-based scheduled automation, referenced skills, optional Agent binding, and run history
- `core/chat_storage.py`: local conversation persistence

See [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) for the technical design and [PRODUCT_DOC.md](PRODUCT_DOC.md) for the product-facing summary.

## Related Docs

- [README_CN.md](README_CN.md): Chinese overview
- [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md): architecture and runtime design
- [SKILL_SYSTEM.md](SKILL_SYSTEM.md): skill and tool model
- [PRODUCT_DOC.md](PRODUCT_DOC.md): product positioning and user flow
- [USER_GUIDE.md](USER_GUIDE.md): illustrated user guide
- [ROADMAP.md](ROADMAP.md): current project status

## License

MIT License
