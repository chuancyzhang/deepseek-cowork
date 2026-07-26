# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [Documentation](docs/index.md)

DeepSeek Cowork is a Windows desktop agent workspace that brings chat, project
files, tool execution, skills, automation, long-term context, and deliverable
preview into one observable and recoverable workflow.

This is a personal exploration project, not an official DeepSeek product.

Current app version: **5.0.8**

## Three Product Ideas

### Everything is Tool

Every model-executable action enters the Agent Loop through one Tool interface.
File operations, commands, MCP, user interaction, theme configuration, and
external services share the same schema, permission, observability, and result
protocol.

Skills provide guidance and experience, Agents provide roles and run context,
and automation provides triggers. None of them creates a second execution
protocol.

### AI-Designed UI

Cowork lets AI design the interface without letting AI take over application
code. AI can configure theme tokens, the workspace scene, surface materials,
component styles, constrained layout, icons, and allowlisted display copy.

Every theme passes schema validation, revision-bound isolated preview, and user
confirmation. Critical controls, region ownership, actions, and recovery paths
remain protected by code.

### Experience System

After a task, the Agent can record tool techniques, failure patterns, and
recovery methods as structured experience. Experience belongs to a specific
Skill or `general-experience` and is disclosed only for relevant work.

This is not model fine-tuning. History provides evidence, memory keeps durable
facts and preferences, experience improves operating methods, and Skills package
reusable capability.

See the [product document](docs/product.md) for the full model.

## Capabilities

- Run a local Agent inside a conversation workspace or explicit project boundary.
- Interleave reasoning, Tool calls, stage replies, and the final answer.
- Steer or stop running work, answer approval requests, and inspect observability.
- Preview Markdown, HTML, images, PDF, DOCX, PPTX, and XLSX deliverables.
- Generate office drafts and convert HTML work products to PPTX, DOCX, or PDF.
- Extend the runtime with built-in, bundled, user, and standard Agent Skills.
- Connect MCP Tools over `stdio` or Streamable HTTP.
- Manage models, Agents, automation, memory, themes, enterprise messaging, and optional components.

## Install

### Windows release

1. Download the latest package from [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases).
2. Extract the ZIP.
3. Run `deepseek-cowork.exe`.

### Run from source

Requires Python 3.10+.

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### Build a release

Prepare the pinned runtime first:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

Then run the clean build and package audit:

```powershell
.\.venv\Scripts\python -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe .\scripts\package_release.py
```

## Quick Start

1. Configure and test a model under **Settings → Models & Services**.
2. Start a standalone conversation or create one from a project.
3. Describe the goal and attach files or select capabilities when needed.
4. Follow Tool activity and observability; review any approval before submitting.
5. Preview results in **Files & Deliverables** and continue converting if needed.
6. Store durable preferences as memory and reusable methods as experience or a Skill.

## Documentation

- [User guide](docs/user-guide.md) (Chinese)
- [Product document](docs/product.md) (Chinese)
- [Technical design: from Agent Loop to desktop runtime](docs/technical-design.md) (Chinese)
- [Skill system](docs/skill-system.md) (Chinese)
- [AI themes and Visualize](docs/guides/ai-theme-and-visualize.md) (Chinese)
- [Roadmap](docs/roadmap.md)
- [5.0.8 release notes](docs/releases/5.0.8.md)

## License

MIT License
