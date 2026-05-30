# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** is a Windows desktop agent framework built around **DeepSeek V4 thinking and tool-use workflows**. It combines reasoning with tool use to plan, execute, and refine tasks across files, apps, and workflows in a secure desktop environment.(**This project is not an official DeepSeek development. It is a purely personal exploration driven by individual interest.**)

Built by **deepseek-cowork team**.

Current app version: **4.8.2**.

![intro](images/english_intro.png)
![App Screenshot 1](images/首页.png)
![App Screenshot 2](images/使用界面.png)

## 🚀 Key Features

### 🧠 Reasoning with Tool Use
*   **Interleaved CoT**: The agent thinks, calls tools, observes results, and continues reasoning in a single flow.
*   **Tool-First Exploration**: Reads real files before acting, reducing hallucinations.

### 🔌 Skill System
*   **Experience-First Skills**: Skills are treated as structured experience packages rather than a second execution protocol.
*   **Hot-Reloadable Skills**: Drop new skills into `skills/` or `ai_skills/` and use them immediately.
*   **Portable Skill Packages**: Export a skill as a ZIP package and import it back from either a ZIP file or a source folder.
*   **Structured Experience Capture**: Runtime lessons can be stored as structured entries and synced back into `SKILL.md`.
*   **Conversation-to-Skill Loop**: Click `沉淀为 Skill` to turn the current conversation into a reviewed skill draft, then create a new skill or update an existing one.
*   **Explicit Read-Only Parallelism**: `parallel_tools` runs independent read-only tool calls concurrently while preserving ordered results and refusing writes or destructive calls.

### 🖥️ Desktop Experience
*   **PySide6 UI**: A calmer blue-and-white desktop surface with modern chat bubbles, markdown rendering, tool-call cards, and a dynamically sized conversation column that keeps a Codex-like reading width across wide and narrow windows.
*   **Project Sidebar**: The left sidebar treats local folders as projects, keeps `新建对话` and search pinned at the top, switches the active workspace when a project is selected, keeps each project collapsed by default with only a short preview of conversations, and presents the list as a softer Apple-inspired panel with quieter inline action buttons.
*   **Workspace Drawer**: A hidden-by-default right context drawer opens from compact icon buttons for files, automation steps, observability, and sub-agent status; sub-agent activity lights the panel hint without forcing the drawer open.
*   **Vision Attachments**: Prompt-bar image attachments keep structured metadata and are sent as multimodal input only when the selected model enables `支持图片理解`.
*   **Automation Center**: A dedicated sidebar button opens automation management with `Configured`, `Run History`, and `Task Templates` tabs.
*   **Session Controls**: Attach files as user-added file chips, mention configured agents, bind a session automation template, restrict the session to selected skills, or switch into clarifying mode from the prompt toolbar; active automation and selected-skill chips can be removed with one click.
*   **Sub-Agent Monitor**: Observe parallel workers in a lightweight timeline summary for task input, tool calls, tool results, streamed output, and final output; opening the panel queues a short, main-thread render pass for UI stability.
*   **Safe Sub-Agent Lifecycle**: Sub-agent completion, status streaming, and worker-thread cleanup are separated so parallel workers can finish or restart without destabilizing the desktop app.
*   **Sub-Agent Runtime Log**: Lifecycle diagnostics are written to `sub_agent_runtime.log` under the `DeepSeekCowork` app data directory, or `user_data/` in portable mode.
*   **Manual Feedback Controls**: Sidebar actions expose `更新长期记忆` and `沉淀为 Skill`, keeping humans in the loop before reusable knowledge is saved.

### 🛰️ Daemon & IM Gateway
*   **Headless Daemon**: Background inference keeps UI responsive.
*   **Enterprise IM (Feishu / DingTalk / WeCom smart bot)**: Send commands via IM with daily session rotation.
*   **Context Budgeting**: IM sessions use model-aware context budgets; DeepSeek V4 keeps long history up to a 1M-token window before compressing, while smaller models still compress conservatively and retry once on overflow.
*   **Workspace Guardrails**: IM requests follow the same workspace limits unless God Mode is enabled.

## 📦 Installation

### Option 1: Run from Executable (Windows)
1.  Download the latest release from [Releases](../../releases).
2.  Unzip and run `deepseek-cowork.exe`.
3.  No Python installation required.

### Option 2: Run from Source (Windows)
**Prerequisites**: Python 3.10+

1.  Clone the repository:
    ```bash
    git clone https://github.com/chuancyzhang/deepseek-cowork.git
    cd deepseek-cowork
    ```

2.  Create and use the virtual environment:
    ```bash
    pip install -r requirements.txt
    ```

3.  Run the application:
    ```bash
    python main.py
    ```

### Build Runtime Bootstrap (Windows Packaging)
Before running `pyinstaller deepseek-cowork.spec`, fetch pinned runtime bundles (Node.js + Git Bash) with SHA256 verification:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

Artifacts are downloaded into `.runtime_downloads/` and extracted to `node_env/` and `git_bash_env/`.
Packaged Windows builds resolve bundled runtimes directly from the current app directory first, including `_internal/node_env/node.exe` and `_internal/git_bash_env/bin/bash.exe`. AppData `runtime_sandbox` remains the temp/cache/dependency root rather than the preferred executable runtime, so automatic upgrades use the newly unpacked `_internal` files. You can override detection with `COWORK_NODE_EXE`, `COWORK_NODE_DIR`, `COWORK_BASH_EXE`, `COWORK_GIT_BASH_DIR`, or `COWORK_BASH_DIR` when needed.
Agents get `run_python_code` in the default execution tool list so Python can be used without a prior `tool_search`; JavaScript execution still uses `run_node_code`, and the `bash` tool prefers Git Bash before falling back to `cmd.exe` on Windows.
The packaged Python sandbox should be built from the base interpreter rather than a virtualenv redirector, otherwise `run_python_code` may fail on machines that do not have the builder's Python installation path.
`install_package` for `python-runner` verifies imports inside the same sandbox runtime used by `run_python_code`. Third-party packages are persisted under AppData `runtime_sandbox/.../skills/python-runner/python/site-packages` so they survive restarts, and stale dependency cache entries are reinstalled automatically if the sandbox can no longer import them.
The sandbox now injects a Python bootstrap `sitecustomize.py`, `PATH`, and `COWORK_PYTHON_DLL_DIRS` so Windows packages with native extensions can resolve DLLs from the bundled runtime plus skill-scoped `site-packages`.
When a package still fails to import after installation, `install_package` returns the sandbox traceback instead of only reporting a generic "still cannot import" message.
The bundled Python runtime must also include platform extension modules from locations such as `DLLs/` on Windows, plus the common MSVC runtime DLLs needed by native wheels; otherwise core modules like `_socket` are missing and `pip` or native packages inside `_internal/python_env` may fail to load.

## 📖 Usage Guide

### 1. Configuration
Open **⚙️ Settings**:
*   **API Key**: DeepSeek or other provider key.
*   **Provider**: `openai` (DeepSeek compatible) or `anthropic`.
*   **MCP**: configure `stdio` or Streamable HTTP MCP servers. Enabled servers are registered as deferred external tools and discovered through `tool_search`.
*   **God Mode**: Toggle safety sandbox restrictions.

### 2. Select Project
Add or select a local folder in the sidebar **项目** section. A project is the workspace boundary for file access, and new conversations are created inside the current project.

### 3. Start Automating
Examples:
*   *"Scan this project for unused imports and remove them."*
*   *"Summarize all PDFs in this folder into a single report."*
*   *"Create a new skill to download videos using yt-dlp."*
*   *"Read the text in this screenshot."* OCR-style extraction stays lightweight by using the selected vision model directly instead of adding a separate local OCR dependency.
*   Use **从对话生成 SOP** in the input `+` menu to extract a complete SOP draft from the current conversation in one pass. Confirming saves it as a task template and binds it to the current session; feedback can regenerate the draft first.

### 4. Manage Automation
Open **自动化** from the sidebar:
*   **Configured**: create scheduled automations, enable or pause them, run them immediately, and review the next run time.
*   **Run History**: inspect completed, failed, interrupted, or missed runs and reopen the related task session.
*   **Task Templates**: edit the former SOP templates without exposing manual template IDs; IDs are generated automatically, and both templates and steps can be set to manual confirmation or auto-advance.

### 5. Close the Feedback Loop
Use the sidebar after meaningful work:
*   **`更新长期记忆`** scans new or changed history, merges it into `memories.md` in batches, shows progress, can run in the background, and lets you review/edit before saving.
*   **`沉淀为 Skill`** turns the current conversation into a skill draft. You can create a new skill or update an existing one by appending experience or rewriting guidance, then preview/edit before saving.
*   **Skill Center import/export** imports custom abilities from folders or ZIP packages and exports existing skills as portable ZIP archives.

### 6. Enterprise IM
Open **⚙️ Settings → Enterprise Messaging**, fill in credentials for Feishu, DingTalk, or WeCom smart bot, enable the channel, then start the gateway.

### 7. App Updates
Open **⚙️ Settings → Updates** to check GitHub Releases. Packaged builds can download, verify, stage, and restart into the new version; source runs only check and link to the release page.

### 8. MCP Servers
Open **⚙️ Settings → MCP** to add MCP servers.
*   `stdio`: configure command, args, cwd, env, and startup timeout.
*   Streamable HTTP: configure URL, headers, and startup timeout.
*   v1 scope is **tools only**. MCP resources and prompts are not wired into the agent yet.

## 🏗️ Architecture

*   **`main.py`**: PySide6 desktop UI entry.
*   **`core/agent.py`**: Reasoning loop and tool orchestration.
*   **`core/daemon.py`**: Headless inference server.
*   **`core/mcp_client.py`**: MCP transport bridge for `stdio` and Streamable HTTP tool discovery/calls.
*   **`core/im_gateway/`**: Multi-platform enterprise messaging gateway and channel adapters.
*   **`core/skill_manager.py`**: Tool registry, experience package loading, relevance matching, and prompt injection.
*   **`core/sop_manager.py`**: Session-level automation templates, per-step state, advance mode (`manual` / `auto`), confirmation, rerun, and skip flow.
*   **`core/sop_from_conversation.py`**: Conversation-to-SOP draft generation with preview and revision.
*   **`core/automation_manager.py`**: Scheduled automation task normalization, next-run calculation, execution prompt assembly, and run-history helpers.
*   **`core/updater.py`**: GitHub Releases update checks, package validation, staging, and Windows restart installer.
*   **`skills/`**: Built-in system skills.
*   **`ai_skills/`**: AI or user-created skills.

## 🧩 Tool + Experience Model
- Tools are the only directly callable execution surface for the LLM.
- Tools are plain Python functions discovered from skill folders (`impl.py`) and converted into JSON-schema function calls.
- Skills are structured experience packages: guidance, boundaries, lessons learned, recommended workflows, and recommended tools.
- New experience can be recorded into structured entries first, then promoted back into `SKILL.md` summaries.
- `沉淀为 Skill` is the manual confirmation path for promoting a useful conversation into `SKILL.md`, `skill.json`, `experience/entries.jsonl`, and optional `impl.py` assets.
- Skill packages can be exported as ZIP archives. ZIP import validates safe paths, supports both flat-root and folder-root packages, keeps the original skill name from metadata, and rejects name conflicts.
- Skills remain hot-reloadable without restarting.

## 🔄 Agentic Workflow
- Interleaved CoT: think → tool-call → observe → continue thinking → final answer.
- Streaming events: reasoning/content/tool_call/tool_result, plus structured sub-agent state events for input, tool activity, results, and completion.
- Sub-agent lifecycles keep result handling separate from Qt thread cleanup, preventing overlapping restarts while a worker is still winding down.
- Sub-agent monitor events are collected first and rendered as lightweight summary rows after the user opens the drawer, avoiding widget churn during active streaming.
- Context-drawer outside-click handling now uses drawer and rail hit-testing in global coordinates, so sub-agent panel children, scroll viewports, and transient popups do not immediately collapse the drawer.
- Sub-agent drawer hide/show diagnostics now record structured reasons in `sub_agent_runtime.log`, making unexpected collapses much easier to trace.
- Loop guards: detect repeated thoughts or tool signatures to stop runaway loops.
- Pause/Resume/Stop controls manage long operations safely.
- Session automation templates constrain execution to the current step; templates and individual steps can require manual confirmation or auto-advance, and the app dispatches one step at a time instead of sending the whole SOP as a single prompt block.
- Scheduled automation tasks reuse the same templates, execute them as step-by-step runs, pause in `awaiting_confirmation` when a manual step is reached, and write every run into the local history.
- Clarifying mode exposes only read-oriented exploration and routes real questions through the interaction tools before normal execution resumes.

## 🧠 Layered Memory & Context
- System prompt layout: stable policies and tool strategy come first, while volatile status such as run mode, date, runtime paths, selected skills, and SOP state is placed later to improve DeepSeek context-cache stability.
- Memories: optional `memories.md` auto-injected when present.
- Long-term memory updates are manually triggered with `更新长期记忆`; processed history is tracked in `memories_update_state.json` so later runs focus on new or changed conversations.
- Skill prompts: minimal experience briefs first, then fuller guidance only when needed.
- Session-level selected skills and agent profiles narrow the allowed capability scope for the current task.
- Context budgeting: DeepSeek V4 Pro/Flash default to a 1,000,000-token window and only proactively compress near the configured budget threshold; non-V4 models use a smaller conservative window.
- History hygiene: reasoning content is deduplicated per turn and DeepSeek thinking tool-call rounds keep the required `reasoning_content` replay fields.

## 🛠️ Extending
Create a folder in `skills/` with:
1.  `SKILL.md`: the experience package body.
2.  `skill.json`: metadata such as capability group, tool refs, workflow, and disclosure defaults.
3.  Optional `impl.py`: tool source if the skill needs new callable tools.
4.  Optional `experience/entries.jsonl`: structured runtime lessons.

Mental model:
- `tool` = executor
- `skill` = experience package

See [SKILL_SYSTEM.md](SKILL_SYSTEM.md) for the full architecture.

## 📄 License

MIT License
