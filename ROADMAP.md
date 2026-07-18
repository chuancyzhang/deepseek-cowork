# DeepSeek Cowork Roadmap

Last updated: `2026-07-18`

This file is a shared project-status note for both the team and AI agents.
It records where the project is now, what has already landed, and what we are likely to focus on next.
Treat it as the current working map, not a strict release contract.

## Current Phase

**Phase:** 5.0.2 release hardening and post-release stabilization

The 5.0.2 release baseline is now documented across the product, user, technical, and Skill docs. The project is focused on:

- making the desktop experience feel more like a polished product and less like an internal tool
- stabilizing long-running agent, automation, and tool-use workflows
- keeping packaging, runtime bootstrap, and documentation aligned with real behavior

For the release scope and acceptance checklist, see [RELEASE_NOTES_5.0.0.md](RELEASE_NOTES_5.0.0.md).

## Completed

### Core agent foundation

- Interleaved reasoning + tool-use loop is in place
- Skills work as structured experience packages instead of a second execution protocol
- Progressive skill disclosure and delayed tool discovery are implemented
- Read-only parallel tool execution is supported through `parallel_tools`

### Desktop product foundation

- PySide6 desktop shell, chat surface, markdown rendering, and tool-call cards are working
- Project-oriented sidebar and workspace-scoped conversations are established
- Right-side context drawer pattern is in place for files, automation, observability, and sub-agent status
- Long-conversation rendering and save paths have been optimized for smoother use

### Automation and workflow support

- Automation Center supports prompt-based configured tasks, referenced skills, optional Agent binding, and run history
- Legacy session-bound SOP and automation template flows have been removed from automation
- Scheduled automation supports cron syntax and guided quick setup
- Automation now executes a task prompt directly through the main assistant or the selected Agent

### Integration and operations

- MCP tool integration supports `stdio` and Streamable HTTP servers
- Enterprise IM gateway supports Feishu, DingTalk, and WeCom smart bots
- GitHub Release update flow exists for packaged builds
- Windows runtime bootstrap and packaged runtime resolution have been documented and hardened

### Quality and usability improvements

- Linear-style settings and automation surface polish has already been applied in key dialogs
- Streaming, markdown rendering, and background save behavior have been tuned for better responsiveness
- Long-term memory update and conversation-to-skill workflows are available with manual confirmation

## Current Focus

- Continue reducing "admin panel" visual weight in desktop surfaces
- Keep shared design tokens and reusable style helpers consistent across dialogs and drawers
- Keep the release notes, user guide, and screenshots aligned with the shipped UI
- Preserve behavior while making polish changes low-risk

## 5.0.2 Release Baseline

- Version source is centralized in `core/app_version.py` and currently reads `5.0.2`
- Responses requests automatically send the conversation-scoped `prompt_cache_key`; Chat Completions retains its existing provider-configured behavior
- README, product documentation, user guide, technical design, Skill system, and this roadmap link to the release notes and current baseline
- Release validation covers packaged startup, model configuration, workspace boundaries, SQLite history recovery, deliverable conversion, PPT Agent, Skill/MCP configuration, automation runs, and diagnostic logs
- Any behavior change that affects a user-facing flow must update the affected Markdown document in the same change

## Candidate Next Steps

These are likely next areas, not hard commitments:

- further unify the Linear-style visual language across remaining secondary panels
- continue hardening packaging and bundled runtime behavior on Windows
- deepen observability for multi-agent and long-running automation flows
- expand MCP-related capability beyond the current tools-only first step
- keep trimming friction in settings, automation, and session-control flows

## Working Rules For Team And AI

- Update this file when the project phase changes or a meaningful milestone lands
- Prefer short factual updates over speculative planning
- Reflect what is already true in the codebase before adding future work
- When UI behavior changes, sync this file with `README.md`, `README_CN.md`, and other affected docs
