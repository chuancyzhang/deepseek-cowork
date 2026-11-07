# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [User guide](docs/user-guide.md) | [Technical guide](docs/technical-design.md)

DeepSeek Cowork is a local Windows desktop agent workspace that brings conversations, project files, tool execution, specialized capabilities, and deliverables into one workflow. Describe a goal, provide source material, follow the execution, and edit the resulting files.

This is an independent personal project, unaffiliated with DeepSeek.

Current app version: **5.3.4** · [Version notes](docs/releases/5.3.4.md) · [Release history](docs/releases/index.md)

Documented version: **5.3.3**; see the version notes for changes in 5.3.4.

## What you can do

- **Conversations and projects**: separate chat directories, project sessions, history search, guidance during execution, execution details, and subtasks.
- **Files and deliverables**: file and image attachments, Office previews, text and spreadsheet editing, HTML drafts, Markdown writing pages, and Office/PDF output.
- **Specialized capabilities**: built-in and optional Skills, SkillHub installation, MCP services, conversation experts, browser automation, and speech transcription.
- **Knowledge and collaboration**: WeKnora, Tencent Docs, Lexiang, enterprise accounts and models, and team spaces.
- **Recurring work**: saved tasks, schedules, messaging channels, and scheduled result delivery.
- **Personalization**: themes, AI theme design, Visualize, long-term memory, and an Experience System.

Version 5.3.4 adds attachment editing for past messages, visible knowledge references, a dismissible discussion-mode chip, and a unified task workspace. It also improves browser task recovery and fixes history loading, response completion, and update installation.

## Get started

1. Download the Windows ZIP from [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases), extract it fully, and run `deepseek-cowork.exe`.
2. Enter a DeepSeek API key in onboarding, or add a service and model under Settings → Models & Services. Enterprise clients can also sign in to their organization.
3. Start a chat, attach source material, and describe the task. Select a project directory when working on an existing project.
4. Follow the answer and execution, then open, edit, and save deliverables in the workspace.

See the [user guide](docs/user-guide.md) for detailed instructions in Chinese.

## Run from source

Use Python 3.10+ and run these commands from the repository root:

```powershell
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## Build a release

Prepare the pinned runtimes in a Windows build environment, then build the executable and release ZIP:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
.\.venv\Scripts\python.exe -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe scripts/package_release.py
```

Packaging produces the versioned ZIP, audit report, and `latest.json`. Update notes come from `docs/releases/VERSION.md`. See [build and release instructions](docs/technical-design.md#构建与发布).

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
