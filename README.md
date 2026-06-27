# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

DeepSeek Cowork is a Windows desktop agent workspace built around DeepSeek-style reasoning plus tool use. It combines chat, project-scoped file work, skill-based capability extension, automation, and optional daemon execution in one PySide6 app.

This project is a personal exploration and is not an official DeepSeek product.

Current app version: **4.9.4**

## What It Does

- Run a local desktop agent that can read files, use tools, and complete multi-step tasks.
- Keep project work inside a selected workspace boundary.
- Extend capabilities through built-in skills, bundled optional plugins, user skills, and MCP tools.
- Manage reusable agents, automation templates, scheduled runs, and long-term memory from the UI.
- Preview common deliverables directly inside the app, including Markdown, HTML, images, PDF, DOCX, PPTX, and XLSX.
- Use Office Mode to create previewable work products for free-form reports, slide-style drafts, design mockups, and document-style outputs.

## Product Surface

- **Conversation + project model**: start a pure chat, or bind a conversation to a project workspace.
- **Office Mode**: keep an office deliverable workflow active from the input bar, with Free, PPT, Design, and DOCX output profiles.
- **Right-side context drawer**: open files, deliverables, observability, and sub-agent status on demand.
- **Settings center**: manage models, agents, workspace defaults, MCP servers, enterprise messaging, and runtime components.
- **Automation center**: manage task templates, schedules, run history, and step confirmation.
- **Skill center**: enable, import, export, debug, and review skills without restarting the app.

## Skill Model

Cowork treats tools as the only direct execution surface.

- `tool`: executable capability the model can call directly
- `skill`: structured experience package that guides when and how to use tools

Skills can provide guidance only, or guidance plus tools. Built-in skills live in `skills/`. Bundled optional plugins and user-created skills live in `ai_skills/`.

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
4. Use the drawer to preview deliverables and inspect tool activity.
5. Save reusable knowledge through **Memory** or **沉淀为 Skill** when a workflow proves useful.

## Architecture

- `main.py`: PySide6 desktop UI
- `core/agent.py`: reasoning loop and tool orchestration
- `core/daemon.py`: optional headless execution path
- `core/skill_manager.py`: skill loading, disclosure, and tool registry
- `core/mcp_client.py`: MCP `stdio` and Streamable HTTP integration
- `core/sop_manager.py`: automation templates and step execution state
- `core/automation_manager.py`: scheduled automation and run history
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
