# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [User guide](docs/user-guide.md) | [Enterprise overview](docs/enterprise/README.md) | [Technical guide](docs/technical-design.md)

DeepSeek Cowork is a local Windows desktop agent workspace that brings conversations, project files, code and tool execution, specialized skills, and deliverable editing into one window. State your goal, attach source files, follow each step live, and review or edit the resulting files right beside the chat.

This is an independent personal project, unaffiliated with DeepSeek.

Documented version: **5.4.3** · [Version notes](docs/releases/5.4.3.md) · [Release history](docs/releases/index.md)

## Key capabilities

- **Conversations and multitasking**: bind chats to local project folders or run standalone sessions, steer running tasks at any time, inspect live code and command output, and track delegated subtasks in a dedicated tab.
- **File reading, editing, and change tracking**: read Word/PPT/PDF documents continuously with AI layout checks, inspect wide Excel sheets in region-reading mode, edit code, spreadsheets, HTML, and Markdown in place, and review either per-turn file changes or live Git working-tree diffs.
- **Data analysis, charts, and media**: run data analysis and Jupyter notebooks with bundled Python libraries, render Mermaid diagrams, interactive Visualize views, and sandboxed HTML/JavaScript previews, and configure dedicated models for image and video generation or video understanding.
- **Knowledge libraries and extensibility**: organize public and project files locally, connect WeKnora, Tencent Docs, and Lexiang, install Skills from SkillHub or local packages, connect MCP servers, switch conversation experts, and automate either the built-in browser or an external Chrome / Edge browser.
- **Schedules and enterprise collaboration**: save recurring tasks with schedules, connect Feishu, DingTalk, WeCom, QQ, or WeChat for remote triggers and result delivery, and use the enterprise edition for department-based access control, shared Skills/MCP/experts, team spaces, and chat-request compliance auditing.
- **Personalization and experience**: customize themes and reading typography, design and test workspace changes safely in Creation Mode or via the [UI SDK](desktop/sdk/README.md), and accumulate long-term memory and reusable experience across sessions.

## Get started

1. Extract the complete `cowork-desktop-vVERSION-windows-x64.zip` archive and launch `deepseek-cowork.exe` inside the extracted `Cowork` folder.
2. Add a model service under **Settings → Models & Services**, or sign in under **Settings → Enterprise Account**. Upgrades reuse your existing local configuration, chat database, attachments, and workspaces automatically.
3. Start a new chat. Pick a project folder below the composer when working on an existing codebase or folder, attach your reference files, and describe what you want produced.
4. Follow the execution timeline, add follow-up instructions if needed, and click any generated file to inspect, edit, and save it in the right-hand workspace.

See the [user guide](docs/user-guide.md) (in Chinese) for step-by-step instructions and troubleshooting.

## Run from source

The desktop app combines **Electron + React/TypeScript + Python + SQLite**: Electron hosts the native window and webviews, React renders the workbench and plugin surfaces, Python drives model protocols and tools, and SQLite stores local history. See the [workspace overview](docs/user-guide.md#认识工作台) and [build and release guide](docs/technical-design.md#构建与发布).

Install Python 3.12+ (the Windows release bundles Python 3.14) and Node.js 22.12+ with npm, then run from the repository root:

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

Fetch the pinned bundled runtimes in a Windows build environment, then build the desktop executable and release archive:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
$env:COWORK_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
npm --prefix desktop run package:win
```

Packaging outputs the runnable directory, `cowork-desktop-vVERSION-windows-x64.zip`, and `latest-desktop-windows-x64.json` under `desktop/out/`. Local builds are not automatically uploaded to [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases); code signing and publication are separate steps. Apple Silicon macOS and UOS V25 builds require native packaging and verification on target hardware. See [build and release instructions](docs/technical-design.md#构建与发布).

## Design principles

- **Everything is Tool**: executable actions share one tool registration, authorization, execution, and result pipeline; Skills package workflows and references, while experts combine roles with selected capabilities.
- **AI-designed UI**: declarative themes, Creation Mode candidates, and [UI SDK](desktop/sdk/README.md) plugins are tested in isolated previews before you apply them, keeping active runs and unsaved drafts intact.
- **Experience over fine-tuning**: chat history preserves the exact process, long-term memory stores durable facts, experience records proven methods, and Skills turn recurring workflows into reusable assets.

## Documentation

| Goal | Entry point |
| --- | --- |
| Complete daily tasks, configure services, and troubleshoot | [User guide](docs/user-guide.md) |
| Understand enterprise roles, permissions, auditing, and updates | [Enterprise overview](docs/enterprise/README.md) |
| Deploy and administer the enterprise server | [Deployment guide](docs/enterprise/deployment.md) · [Administrator guide](docs/enterprise/admin-guide.md) |
| Understand architecture, build Skills/plugins, test, and package | [Technical guide](docs/technical-design.md) · [UI SDK](desktop/sdk/README.md) |
| Review version-by-version changes | [Release history](docs/releases/index.md) |

Detailed guides are maintained in Chinese.

## License

Apache-2.0
