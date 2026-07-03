# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

DeepSeek Cowork is a Windows desktop agent workspace built around DeepSeek-style reasoning plus tool use. It combines chat, project-scoped file work, skill-based capability extension, automation, and optional daemon execution in one PySide6 app.

This project is a personal exploration and is not an official DeepSeek product.

Current app version: **4.9.6**

## What It Does

- Run a local desktop agent that can read files, use tools, and complete multi-step tasks.
- Keep project work inside a selected workspace boundary.
- Extend capabilities through built-in skills, bundled optional plugins, user skills, and MCP tools.
- Manage reusable agents, prompt-based automation tasks, scheduled runs, and long-term memory from the UI.
- Preview common deliverables directly inside the app, including Markdown, HTML, images, PDF, DOCX, PPTX, and XLSX.
- Turn any assistant reply into a previewable office draft, with Free, PPT, Design, and DOCX output profiles; office-draft generation turns are collapsed into a task card by default while result files stay visible below the card.
- Generate PPTX, DOCX, or PDF from an HTML draft in a folded task card with local action feedback; the drawer stays usable or closable while completion returns through toast and refreshed deliverable shortcuts.

## Product Surface

- **Conversation + project model**: start a pure chat, or bind a conversation to a project workspace; newly submitted conversations appear in the sidebar immediately while the background save queue catches up, and the project controls use quiet Apple-style icon buttons.
- **Prompt attachments**: attached text files are inlined into the model request when small enough, images are sent as vision parts on vision-capable models, and large or non-text files are surfaced with explicit path/size guidance for tool-based reading.
- **Office draft actions**: generate Free, PPT, Design, or DOCX-style HTML drafts from an assistant reply, with the generation process folded into a compact task card and deliverable shortcuts kept outside the folded process.
- **Right-side context drawer**: browse files and deliverables in a list view, then drill into a focused preview/action view; chat deliverable cards open directly in preview.
- **Token usage chip**: view accumulated conversation tokens and cached input usage from a light click-to-open detail popover.
- **Settings center**: manage models, agents, workspace defaults, archived projects and conversations, MCP servers, enterprise messaging, and runtime components.
- **Home toolkit prompt**: new conversations point users to Settings for the document and data-analysis toolkits before Office/PDF, spreadsheet, analysis, and visualization work.
- **Automation center**: manage prompt-based tasks, schedules, run history, referenced skills, and optional Agent bindings.
- **Skill center**: enable, import, export, debug, and review skills without restarting the app.

## Skill Model

Cowork treats tools as the only direct execution surface.

- `tool`: executable capability the model can call directly
- `skill`: structured experience package that guides when and how to use tools

Skills can provide guidance only, or guidance plus tools. Built-in skills live in `skills/`. Bundled optional plugins and user-created skills live in `ai_skills/`. Cowork can install standard Agent Skill packages as user AI skills, preserving the original root `SKILL.md` and generating `skill.json` only for local discovery, workbench, and debug metadata. Skills may declare `config_fields`; the workbench renders those fields in a configuration tab and injects saved values into script/tool execution through explicit environment variables.

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
2. Start a pure chat or attach the conversation to a project.
3. Ask the agent to inspect files, edit code, generate reports, or run automation.
4. For document, spreadsheet, or data-analysis tasks, use the home prompt to open **Settings → Components & dependencies** and install the document and data-analysis toolkits.
5. Enable optional skills from **Skill Center** or install a standard Agent Skill with `install_agent_skill`; Tencent Docs, Feishu Docs, and DingTalk Docs are bundled as separate document skills, and their workbench pages expose configuration fields, read-only files, Tool debugging, and script entries.
6. Use the drawer to browse files, preview deliverables, convert HTML drafts, and inspect tool activity.
7. Save reusable knowledge through **Memory** or **沉淀为 Skill** when a workflow proves useful.

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
