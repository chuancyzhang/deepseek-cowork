# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [User guide](docs/user-guide.md) | [Technical guide](docs/technical-design.md)

DeepSeek Cowork is a local Windows desktop agent workspace that brings conversations, project files, tool execution, specialized capabilities, and deliverables into one workflow. Describe a goal, provide source material, follow the execution, and edit the resulting files.

This is an independent personal project, unaffiliated with DeepSeek.

Documented version: **5.4.0** · [Version notes](docs/releases/5.4.0.md) · [Release history](docs/releases/index.md)

## What you can do

- **Conversations and projects**: separate chat directories, project sessions, history search, guidance during execution, execution details, and subtasks.
- **Files and deliverables**: file and image attachments, Office previews, text and spreadsheet editing, HTML drafts, Markdown writing pages, and Office/PDF output.
- **Specialized capabilities**: built-in and optional Skills, SkillHub installation, MCP services, conversation experts, and browser automation.
- **Knowledge and collaboration**: local project files, WeKnora, Tencent Docs, Lexiang, enterprise accounts and models, and team spaces.
- **Recurring work**: saved tasks, schedules, messaging channels, and scheduled result delivery.
- **Personalization**: themes, AI theme design, Visualize, long-term memory, and an Experience System.

Version 5.4.0 introduces the Electron workspace, a shared task runtime and durable event pipeline, replaceable UI plugins, and updated library, editing, and management workflows. These notes describe source changes; a new package must still be built and validated before publication.

## Get started

This branch uses Electron + React/TypeScript + Python + SQLite. The default entry launches Electron; the runtime and Windows service package do not require Qt. Conversations, workspace tools, categorized Skills and SkillHub, management pages, and appearance settings use the new host. See the [workspace guide](docs/user-guide.md#新版-windows-工作台) and [development/build instructions](docs/technical-design.md#electron-工作台). Native validation and releases for Apple Silicon macOS and UOS V25 are pending.

1. Extract the complete `cowork-desktop-vVERSION-windows-x64.zip` build and run `deepseek-cowork.exe`. Local builds are not automatically published to [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases); older releases retain their old entry point.
2. Add a service and model under Settings → Models & Services, or sign in to an enterprise account. Production builds use the original configuration, conversation database, attachments, and workspaces in place; no copy or manual import is required.
3. Start a chat, attach source material, and describe the task. Select a project directory when working on an existing project.
4. Follow the answer and execution, then open, edit, and save deliverables in the workspace.

See the [user guide](docs/user-guide.md) for detailed instructions in Chinese.

The [UI SDK](desktop/sdk/README.md) supports complete workbenches, pages, panels, editors, commands, and menus. The stable host preserves drafts and unsaved edits while background tasks continue through plugin changes.

## Run from source

Use Python 3.10+ and Node.js 22.12+ with npm. The bundled `node_env` is an execution runtime and does not imply npm is installed. Run these commands from the repository root:

```powershell
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix desktop ci
npm --prefix web/editors ci
node desktop/node_modules/electron/install.js
.\.venv\Scripts\python.exe main.py
```

## Build a release

Prepare the pinned runtimes in a Windows build environment, then build the executable and release ZIP:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
$env:COWORK_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
npm --prefix desktop run package:win
```

Packaging produces `desktop/out/DeepSeek Cowork-win32-x64`, a distributable ZIP, and `latest-desktop-windows-x64.json`. Updates distinguish Electron packages from legacy layouts. Signing and publication are separate steps. See [build and release instructions](docs/technical-design.md#electron-工作台).

## Design principles

- **Everything is Tool**: executable actions share tool registration, authorization, execution, and results. Skills organize guidance and resources; experts provide roles.
- **AI-designed UI**: declarative themes and isolated previews let users review and apply changes to the workspace.
- **Experience System**: history records the process, memory stores lasting facts, experience captures reusable methods, and Skills organize specialized workflows. This is not model fine-tuning.

## Documentation

| Goal | Entry point |
| --- | --- |
| Complete tasks and use settings and capabilities | [User guide](docs/user-guide.md) |
| Understand the architecture, develop Skills, test, build, and release | [Technical guide](docs/technical-design.md) |
| Deploy and administer the enterprise platform | [Enterprise platform](web/platform/README.md) |
| Review version changes | [Release history](docs/releases/index.md) |

Detailed guides are maintained in Chinese.

## License

Apache-2.0
