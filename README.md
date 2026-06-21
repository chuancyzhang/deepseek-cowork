# DeepSeek Cowork

[中文文档](README_CN.md) | [English](README.md)

**DeepSeek Cowork** is a Windows desktop agent framework built around **DeepSeek V4 thinking and tool-use workflows**. It combines reasoning with tool use to plan, execute, and refine tasks across files, apps, and workflows in a secure desktop environment.(**This project is not an official DeepSeek development. It is a purely personal exploration driven by individual interest.**)

Built by **deepseek-cowork team**.

Current app version: **4.9.2**.

While a foreground task is running, you can add text, images, or files in the main composer and choose **Guide** to steer the active turn. Guidance is applied at the next safe model-request boundary without stopping or starting a new task. Local and daemon-backed desktop runs are supported; automations, standalone sub-agents, and enterprise IM are not steerable yet.

Project status for the team and AI agents lives in [ROADMAP.md](ROADMAP.md).

For installation, model setup, project conversations, and editable PPTX generation, see the illustrated [Chinese user guide](USER_GUIDE.md).

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
*   **Optional Bundled Plugins**: Browser automation, web search, financial data, and Office/PDF reading ship under `ai_skills/`; their dependencies can be prepared from Settings → Components & Dependencies, and the document toolkit includes ReportLab for PDF generation.
*   **Browser Sessions**: `browser-automation` provides serial observe/act/verify workflows with a dedicated persistent profile, temporary isolated profiles, and an explicit Chrome 144+ connection mode that honors Chrome's remote-debugging approval flow.
*   **Portable Skill Packages**: Export a skill as a ZIP package and import it back from either a ZIP file or a source folder.
*   **Standalone Quant Strategy Skill**: `quant-strategy-management` adds controlled Strategy DSL parsing, strategy storage, daily backtests, and report artifacts as a self-contained skill package.
*   **Structured Experience Capture**: Runtime lessons can be stored as structured entries and synced back into `SKILL.md`.
*   **Conversation-to-Skill Loop**: Click `沉淀为 Skill` to choose a current conversation segment, turn it into a reviewed skill draft, and optionally preserve executed Python snippets as registered skill scripts.
*   **Explicit Read-Only Parallelism**: `parallel_tools` runs independent read-only tool calls concurrently while preserving ordered results and refusing writes or destructive calls.

### 🖥️ Desktop Experience
*   **PySide6 UI**: A calmer blue-and-white desktop surface with modern chat bubbles, markdown rendering, tool-call cards, and a dynamically sized conversation column that keeps a Codex-like reading width across wide and narrow windows.
*   **Project Sidebar**: The top `新建对话` action always starts a workspace-free chat, while each project row `+` creates a project-bound task. The plus icon is drawn by Qt for reliable cross-platform and high-DPI rendering, and tooltips use an opaque light surface for reliable Windows rendering. Project headers only expand or collapse their conversations; workspace context follows the visible conversation.
*   **Workspace-free Chat**: Users can ask questions before selecting a project. The UI shows `未连接项目 · 纯对话` and withholds local file, command, and automation tools. `连接项目` preserves the current context and binds the conversation to the chosen workspace.
*   **Workspace Drawer**: A hidden-by-default right context drawer opens from compact icon buttons for files, deliverables, observability, and sub-agent status. Once opened, it stays visible while working in the chat and closes only through its close button or `Esc`. A compact project sidebar, wider central conversation column, and stable deliverables workspace now follow a Codex-like three-column balance without overlapping the chat area. Sub-agent activity lights the panel hint without forcing the drawer open.
*   **HTML Creation Flow**: The deliverables drawer treats HTML as the editable source rather than another file row. Selecting HTML opens a lightweight live preview that suppresses continuous animation work and uses Qt-owned wheel handling plus draggable scrollbars for reliable vertical, horizontal, and nested-area scrolling. Unchanged renders are reused while workspace artifacts are scanned in the background. Users can force-refresh it, enlarge the preview, drag the drawer width or preview split, and return after switching conversations without losing the selected source. PPTX, DOCX, and PDF actions attach the current HTML to the current conversation and continue the existing creative context.
*   **Prompt Cache Observability**: The system-prompt pane separates the stable prefix, per-turn runtime context, and disclosed skill context, while the observability log focuses on cached input tokens and cache hit rate when the provider reports usage.
*   **Vision Attachments**: Prompt-bar image attachments keep structured metadata and are sent as multimodal input only when the selected model enables `支持图片理解`.
*   **Plain-Text Prompt Pasting**: Pasting into the main prompt box strips rich-text styling from sources like browsers, WeChat, and Office while preserving plain text and line breaks.
*   **Staged Background Startup**: The main window shell shows first, then default workspace hydration, sidebar history rendering, tray setup, daemon prewarm, daemon monitoring, and automation scheduling are deferred into short background phases to keep launch interaction smooth even on slower machines.
*   **Singleton Background Services**: Repeated clicks on the exe or start/run no longer fan out extra windows, daemon, or IM gateway processes; the UI process now keeps a runtime lock and retries local activation while the first window is still booting, and child processes still enforce a single live instance with file locks.
*   **Streamed Reply Coalescing**: Token deltas update the UI on short timers instead of forcing a rich-text relayout for every fragment.
*   **Markdown Render Cache**: Repeated Markdown/HTML rendering reuses cached output for stable history bubbles and final responses.
*   **Virtualized Chat Bubbles**: Long conversations collapse far-offscreen bubbles into fixed-height placeholders and restore them near the viewport.
*   **Asynchronous Chat Saves**: High-frequency conversation saves are debounced and written from a background queue, while message rewrites, rename, archive, delete, memory-update, and app-exit paths still flush pending writes before they read or close.
*   **Daemon Context Snapshots**: Desktop-to-daemon requests include the current in-memory conversation snapshot, so continuing an idle chat keeps the visible context even after the daemon has suspended and restored its own session cache.
*   **History Load Guard**: A restored session keeps input and background saves disabled until its SQLite history has been loaded and rebound as the window's canonical message list, preventing an early empty snapshot from overwriting existing context.
*   **Automation Center**: A dedicated sidebar button opens automation management with `Configured`, `Run History`, and `Task Templates` tabs, with both cron-expression scheduling and guided quick configuration.
*   **Session Controls**: Attach files as user-added file chips, mention configured agents, bind a session automation template, restrict the session to selected skills, or switch into clarifying mode from the prompt toolbar; the prompt box auto-resizes with content and re-wraps cleanly in narrower layouts, and active automation and selected-skill chips can be removed with one click.
*   **Inline Message Editing**: Finished user bubbles can switch into a compact inline editor with Cancel and Send controls. Sending replaces the current turn in place, truncates later history, and regenerates the answer; deleting a user message removes only that message and keeps later content.
*   **Pinned and Archived Conversations**: Conversation rows inside projects and the unassigned conversation group expose compact pin and archive actions on hover. Pinned conversations sort first without changing their last-activity timestamp, while archived conversations leave the sidebar immediately.
*   **Complete Prompt Bubble Wrapping**: User chat bubbles wrap full questions, including long Chinese prompts, identifiers, filenames, and hyphenated English text, instead of clipping or hiding content inside the blue bubble.
*   **System Status Toasts**: Chat-stream system hints render as compact Apple-style status pills with restrained color, wrapping text, and auto-dismiss behavior instead of heavy full-width notice boxes. Automation steps that require manual confirmation now show a chat-stream action bar for confirm, rerun, or skip.
*   **Sub-Agent Monitor**: Observe parallel workers in a lightweight timeline summary for task input, tool calls, tool results, streamed output, and final output; opening the panel queues a short, main-thread render pass for UI stability.
*   **Safe Sub-Agent Lifecycle**: Sub-agent completion, status streaming, and worker-thread cleanup are separated so parallel workers can finish or restart without destabilizing the desktop app.
*   **Smoother Settings Saves**: The settings dialog batches model, MCP, agent, workspace, and IM configuration updates into a single disk write, and model-only saves no longer rescan skills unless MCP servers changed.
*   **Smoother Launch & Run Actions**: Daemon connection now bootstraps in the background, so starting a task or running code no longer waits on repeated UI-thread connection retries.
*   **Long Conversation Fast Path**: Opening or searching the sidebar now stays on the SQLite history index by default instead of scanning every legacy `chat_history_*.json` file on each refresh; older JSON sessions can be migrated manually from the sidebar menu.
*   **Lightweight Long-Reply Rendering**: Very long assistant responses stay fully visible; streaming output may use a temporary plain-text path, while final Markdown/HTML replies keep rich rendering unless the text is extremely large and plain.
*   **Hidden Windows Console Launches**: Python, Bash, updater fallback, updater relaunch, app launch, and system-tool subprocesses share a no-window launch path on Windows to avoid flashing CMD windows during normal use.
*   **On-Demand Runtime Diagnostics**: High-frequency sub-agent lifecycle diagnostics are off by default; set `COWORK_RUNTIME_DEBUG_LOG=1` to write `sub_agent_runtime.log` under the app data directory, or `user_data/` in portable mode.
*   **Persistent UI Exception Diagnostics**: The global UI event guard still prevents one event failure from immediately interrupting the app, while always writing the full traceback to `ui_error.log` under the app data directory or portable `user_data/`; the in-app notice points to that log instead of hiding the root cause behind a generic continuation message.
*   **Manual Feedback Controls**: Sidebar actions expose `记忆` and `沉淀为 Skill`; generated long-term summaries are saved only after review.

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
Before running `pyinstaller deepseek-cowork.spec`, fetch the pinned Git Bash runtime with SHA256 verification. Node.js is now an in-app optional component:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch_runtimes.ps1
```

Artifacts are downloaded into `.runtime_downloads/` and extracted to `node_env/` and `git_bash_env/`.
Packaged Windows builds resolve bundled runtimes directly from the current app directory first, including `_internal/node_env/node.exe` and `_internal/git_bash_env/bin/bash.exe`. AppData `runtime_sandbox` remains the temp/cache/dependency root rather than the preferred executable runtime, so automatic upgrades use the newly unpacked `_internal` files. You can override detection with `COWORK_NODE_EXE`, `COWORK_NODE_DIR`, `COWORK_BASH_EXE`, `COWORK_GIT_BASH_DIR`, or `COWORK_BASH_DIR` when needed.
Agents get `run_python_code` in the default execution tool list so Python can be used without a prior `tool_search`; JavaScript execution still uses `run_node_code`, and the `bash` tool prefers Git Bash before falling back to `cmd.exe` on Windows.
Vision turns keep the same `tool_search` discovery path as text turns, so screenshot or image tasks can still discover deferred tools such as document readers or browser helpers when needed.
The packaged Python sandbox should be built from the base interpreter rather than a virtualenv redirector, otherwise `run_python_code` may fail on machines that do not have the builder's Python installation path.
Packaged builds retain the complete sandbox Python base environment. Document, data-analysis, finance, browser-automation, and web-research dependencies can be installed on demand from Settings → Components & Dependencies, using PyPI, Tsinghua, Aliyun, or a custom HTTPS index. Node.js can use the official source or npmmirror. `PySide6-Addons` and QtWebEngine resources remain bundled for HTML preview.
`install_package` for `python-runner` verifies imports inside the same sandbox runtime used by `run_python_code`. Third-party packages are persisted under AppData `runtime_sandbox/.../skills/python-runner/python/site-packages` so they survive restarts, and stale dependency cache entries are reinstalled automatically if the sandbox can no longer import them.
The sandbox now injects a Python bootstrap `sitecustomize.py`, `PATH`, and `COWORK_PYTHON_DLL_DIRS` so Windows packages with native extensions can resolve DLLs from the bundled runtime plus skill-scoped `site-packages`.
When a package still fails to import after installation, `install_package` returns the sandbox traceback instead of only reporting a generic "still cannot import" message.
The bundled Python runtime must also include platform extension modules from locations such as `DLLs/` on Windows, plus the common MSVC runtime DLLs needed by native wheels; otherwise core modules like `_socket` are missing and `pip` or native packages inside `_internal/python_env` may fail to load.

## 📖 Usage Guide

### 1. Configuration
Open **⚙️ Settings**:
The settings window uses a left category list with lightweight borderless preference sections on the right.
*   **Models & Services**: split providers or use-cases into separate model service cards, each with its own access key, base URL, and model list.
*   **Agents**: save reusable working personas with their own prompt and skill scope.
*   **Workspace**: set the default workspace and chat storage location so the app opens into a stable working context.
*   **MCP**: configure `stdio` or Streamable HTTP MCP servers. MCP terms stay in English so they match official examples and JSON snippets.
*   **Extended Permissions**: control whether the assistant may step outside the workspace sandbox for higher-risk actions.

### 2. Select Project
Use the top **新建对话** action for a workspace-free chat. For local file work, add a folder under **项目** and use its `+`, or choose **连接项目** from the current chat. A project remains the workspace boundary for file access.

### 3. Start Automating
Examples:
*   *"Scan this project for unused imports and remove them."*
*   *"Summarize all PDFs in this folder into a single report."*
*   *"Create a new skill to download videos using yt-dlp."*
*   *"Read the text in this screenshot."* OCR-style extraction stays lightweight by using the selected vision model directly instead of adding a separate local OCR dependency.
*   Use **从对话生成 SOP** in the input `+` menu to extract a complete SOP draft from the current conversation in one pass. Confirming saves it as a task template and binds it to the current session; feedback can regenerate the draft first.

### 4. Manage Automation
Open **自动化** from the sidebar:
*   **Automation Center** now opens with a compact overview, softer segmented navigation, and lighter task rows that surface enabled state, template, schedule, next run time, and quick actions without looking like a raw admin table.
*   **Scheduled Task Editor** splits setup into clear sections for basics, schedule, template preview, and execution notes. Schedules still support direct 5-field crontab syntax as well as guided daily/weekly/monthly/interval/once setup.
*   **Run History** presents a calmer detail view for completed, failed, interrupted, missed, or awaiting-confirmation runs, and can still reopen the related task session.
*   **Task Templates** continue to hide manual template IDs by default; templates and steps can be set to manual confirmation or auto-advance, and each step can run through the agent, an uploaded Python file, or a Bash command.
*   **Conversation Automation Entry Points** are more consistent: binding an automation to the current session and previewing a generated SOP draft use the same Apple-style surfaces; manual step confirmation now happens directly in the chat stream.
*   **AI-callable Automation Tools** let the agent inspect templates, create or update templates and scheduled tasks, pause or enable tasks, and review run history through normal `tool_search` discovery. Deleting or immediately running a task still requires explicit user approval, and newly created scheduled tasks default to paused unless the user clearly asks to enable them.

### 5. Close the Feedback Loop
Use the sidebar after meaningful work:
*   **`记忆` / Memory** opens the Memory Center for editing global/workspace summaries and the global soul prompt. Global generation reads all history, while current-workspace generation reads only conversations assigned to that workspace; both remain drafts until confirmation.
*   **`沉淀为 Skill`** lets you choose a current conversation segment and turn it into a skill draft. You can create a new skill or update an existing one by appending experience or rewriting guidance, preview/edit before saving, and optionally store detected `run_python_code` snippets under `scripts/` as `script_entries`.
*   **Skill Center import/export/debug** imports custom abilities from a single skill folder, a skill collection folder, or ZIP packages, and exports existing skills individually or as a selected multi-skill collection ZIP. The Skill Center separates read-only built-ins, default-off bundled plugins, MCP tools, and custom skills while preserving search, status filters, switches, validation, and tool debugging. Switches update the list immediately, while runtime skill registration refreshes on the next use or a manual refresh; MCP switches stay synchronized with the matching server's enabled state in Settings. The full switch track is clickable without opening the capability workbench.

### 6. Enterprise IM
Open **⚙️ Settings → Enterprise Messaging**, fill in credentials for Feishu, DingTalk, or WeCom smart bot, enable the channel, then start the gateway.

### 7. App Updates
Open **⚙️ Settings → Updates** to check GitHub Releases. Packaged builds clean old update packages, staging folders, and scripts before keeping the current target package; then they download, verify, stage, and restart into the new version through the standalone updater. The foreground progress window can be minimized, or installation can run in the background. Source runs only check and link to the release page.

### 8. MCP Servers
Open **⚙️ Settings → MCP** to add MCP servers.
*   Packaged builds bundle the MCP client runtime by default, so end users do not need to install `mcp` separately.
*   `stdio`: configure command, args, cwd, env, and startup timeout.
*   Streamable HTTP: configure URL, headers, and startup timeout.
*   You can also import JSON snippets such as `{"mcpServers":{"showdoc":{"type":"streamable-http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer ..."}}}}`.
*   v1 scope is **tools only**. MCP resources and prompts are not wired into the agent yet.

## 🏗️ Architecture

*   **`main.py`**: PySide6 desktop UI entry.
*   **`core/agent.py`**: Reasoning loop and tool orchestration.
*   **`core/daemon.py`**: Headless inference server.
*   **`core/mcp_client.py`**: MCP transport bridge for `stdio` and Streamable HTTP tool discovery/calls.
*   **`core/im_gateway/`**: Multi-platform enterprise messaging gateway and channel adapters.
*   **`core/skill_manager.py`**: Tool registry, experience package loading, relevance matching, and prompt injection.
*   **`core/sop_manager.py`**: Session-level automation templates, per-step executor metadata, per-step state, advance mode (`manual` / `auto`), confirmation, rerun, and skip flow.
*   **`core/sop_from_conversation.py`**: Conversation-to-SOP draft generation with preview and revision.
*   **`core/automation_manager.py`**: Scheduled automation task normalization, cron / quick-schedule next-run calculation, execution prompt assembly, and run-history helpers.
*   **`core/updater.py`**: GitHub Releases update checks, package validation, staging, and Windows restart installer.
*   **`skills/`**: Core built-in skills.
*   **`ai_skills/`**: Default-off bundled plugins plus AI/user-created skills.
*   **`ai_skills/quant-strategy-management/`**: Standalone quant strategy skill with its own package, CLI entrypoint, storage, and backtest workflow.

## 🧩 Tool + Experience Model
- Tools are the only directly callable execution surface for the LLM.
- Tools are plain Python functions discovered from skill folders (`impl.py`) and converted into JSON-schema function calls.
- Skills are structured experience packages: guidance, boundaries, lessons learned, recommended workflows, and recommended tools.
- Core file tools only handle plain text and workspace paths. DOCX, PPTX, XLSX/XLS, and PDF reading lives in the optional `document-reader` plugin through `document_read`; writing those formats is done with `run_python_code` and task-specific libraries.
- New experience can be recorded into structured entries first, then promoted back into `SKILL.md` summaries.
- `沉淀为 Skill` is the manual confirmation path for promoting a useful conversation segment into `SKILL.md`, `skill.json`, `experience/entries.jsonl`, optional `impl.py`, and optional Python script assets registered in `script_entries`.
- Skill packages can be exported individually or as multi-skill collection ZIP archives. ZIP import validates safe paths, supports flat-root, folder-root, and collection packages, keeps the original skill name from metadata, and rejects name conflicts.
- Imported / agent skills use persistent progressive disclosure: the agent starts with metadata or an explicitly selected skill brief, then records the full skill prompt as hidden conversation context only after explicit selection, a `tool_search` match, or an actual skill-tool call. Free-form user text no longer triggers fuzzy full-prompt injection.
- When a matched imported / agent skill exposes `script_entries`, `tool_search` returns `run_skill_script` as the preferred execution surface, so the agent should use the declared script entry instead of locating the skill directory with `glob` or `bash`.
- `quant-strategy-management` runs as an independently executable Cowork skill and does not rely on `D:\code\测试策略` as a runtime path dependency.
- Skills remain hot-reloadable without restarting.

## 🔄 Agentic Workflow
- Interleaved CoT: think → tool-call → observe → continue thinking → final answer.
- Streaming events: reasoning/content/tool_call/tool_result, plus structured sub-agent state events for input, tool activity, results, and completion.
- Sub-agent lifecycles keep result handling separate from Qt thread cleanup, preventing overlapping restarts while a worker is still winding down.
- Sub-agent monitor events are collected first and rendered as lightweight summary rows after the user opens the drawer, avoiding widget churn during active streaming.
- The context drawer no longer listens for outside clicks; files, deliverables, observability, and sub-agent pages stay open until the user clicks close or presses `Esc`.
- When `COWORK_RUNTIME_DEBUG_LOG=1` is enabled, sub-agent drawer hide/show diagnostics record structured reasons in `sub_agent_runtime.log`, making unexpected collapses easier to trace without paying the logging cost during normal runs.
- Loop guards: detect repeated thoughts or tool signatures to stop runaway loops.
- Pause/Resume/Stop controls manage long operations safely.
- Session automation templates constrain execution to the current step; templates and individual steps can require manual confirmation or auto-advance, and the app dispatches one step at a time instead of sending the whole SOP as a single prompt block. Each step can use the agent, an uploaded Python file, or a Bash command.
- Scheduled automation tasks reuse the same templates, execute them as step-by-step runs, pause in `awaiting_confirmation` when a manual step is reached, and write every run into the local history. Cron syntax is parsed inside the app, so Windows builds do not depend on a system `crontab` service.
- Clarifying mode exposes only read-oriented exploration and routes real questions through the interaction tools before normal execution resumes.

## 🧠 Layered Memory & Context
- System prompt layout: stable policies and tool strategy come first, while volatile status such as run mode, date, runtime paths, selected skills, and SOP state is placed later to improve DeepSeek context-cache stability.
- Memories: the soul prompt and applicable summaries are injected automatically; detailed editable modules are searched only when relevant.
- The Memory Center keeps a soul prompt and compact global/current-workspace summaries in the stable prompt. Generation is review-first and advances an independent global or workspace cursor only after the user saves it.
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
