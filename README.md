# DeepSeek Cowork

[中文](README_CN.md) | [English](README.md) | [Documentation](docs/index.md)

DeepSeek Cowork is a local Windows desktop workspace for agentic work. It brings
chat, project files, Tool execution, capability extensions, automation, and
deliverables into one observable, steerable, and recoverable workflow.

This is a personal exploration project and is not affiliated with DeepSeek.

Current app version: **5.1.0** · [Read the release notes](docs/releases/5.1.0.md)

## What matters in 5.1.0

- **Revise results in place:** preview common formats and safely edit DOCX, HTML, XLSX, CSV/TSV, Markdown, JSON, XML, YAML, and plain text in Files & Deliverables.
- **Find capabilities by task:** the capability marketplace groups tools into research, documents, data, content, and finance; the new-chat home opens PPT, finance, data, and browser workflows directly.
- **Connect with less setup:** browser automation has one guided preparation and connection flow; enterprise messaging supports Feishu, DingTalk, WeCom, QQ, and WeChat with one active channel at a time.
- **Use a stable Agent tool contract:** core Tools are visible on the first model turn, text work converges on full-file reads plus audited patches, and DeepSeek Responses preserves reasoning, function calls, and server-side search order. This prepares for the post-training calling preferences and Responses protocol compatibility of the DeepSeek V4 Flash release and the later V4 Pro release.

## Core workflow

1. Select a model and start either a standalone conversation or a project-bound workspace.
2. Describe the outcome and attach or paste the files, images, and references the task needs.
3. Follow reasoning, Tools, stage results, and the final answer in one stream; steer or stop the run when needed.
4. Inspect observability, files, and deliverables in the right drawer, then edit or convert the result.
5. Store durable preferences as memory, validated methods as experience or a Skill, and repeatable schedules as automation.

## Capability map

| Layer | Main capabilities |
| --- | --- |
| Workspace | Standalone chat directories, explicit project boundaries, history recovery, background runs, and grouped pagination |
| Files & deliverables | File/image paste, Markdown/HTML/image/PDF/DOCX/PPTX/XLSX preview, safe editing, and Office conversion |
| Agent runtime | Streaming reasoning and Tools, mid-run guidance, structured observability, sub-agents, daemon, and automation |
| Extensions | Built-in, optional, and user Skills; `stdio` and Streamable HTTP MCP; on-demand dependencies and remote Skill installation |
| Connections | Browser automation, web search, finance and data capabilities, plus Feishu/DingTalk/WeCom/QQ/WeChat |
| Personalization | Global and workspace memory, the Experience System, safe `.cowork-theme` themes, and Visualize |

## Three design principles

- **Everything is Tool:** every executable action shares the same Tool schema, permissions, observability, and result protocol. Skills provide guidance, Agents provide roles, and automation provides triggers.
- **AI-designed UI:** AI can configure theme tokens, the workspace scene, and constrained components, but it cannot rewrite the component tree, protected actions, or recovery controls. Themes require validation, isolated preview, and user confirmation.
- **Experience System:** history preserves evidence, memory keeps durable facts, experience improves methods, and Skills package reusable capability. This is not model fine-tuning.

See the [product document](docs/product.md) for the complete product model.

## Install

### Windows release

1. Download the latest ZIP from [GitHub Releases](https://github.com/chuancyzhang/deepseek-cowork/releases).
2. Extract it completely, then run `deepseek-cowork.exe`; do not launch it from inside the archive.
3. Open **Settings → Models & Services**, add a model, and test the connection.

The reference environment is a 4-core CPU, 8 GB RAM, and an SSD. 16 GB RAM is
recommended; a discrete GPU is not required.

### Run from source

Requires Python 3.10+.

```bash
git clone https://github.com/chuancyzhang/deepseek-cowork.git
cd deepseek-cowork
python -m pip install -r requirements.txt
python main.py
```

### Build a release

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
.\.venv\Scripts\python -m PyInstaller deepseek-cowork.spec --noconfirm --clean
.\.venv\Scripts\python.exe .\scripts\package_release.py
```

The release uses pinned runtimes, offline editor assets, and a packaging audit.
Treat the report from the current build as authoritative for size and contents.

## Documentation

- [User guide](docs/user-guide.md) (Chinese): installation, setup, and complete task workflows
- [Product document](docs/product.md) (Chinese): goals, principles, and boundaries
- [Technical design](docs/technical-design.md) (Chinese): Agent Loop, Tools, safety, persistence, and desktop runtime
- [Skill system](docs/skill-system.md) (Chinese): sources, discovery, configuration, MCP, dependencies, and experience
- [AI themes and Visualize](docs/guides/ai-theme-and-visualize.md) (Chinese): user-focused walkthrough
- [Roadmap](docs/roadmap.md) (Chinese): current phase and candidate directions
- [Release history](docs/releases/index.md) (Chinese): version changes and acceptance priorities

## License

MIT License
