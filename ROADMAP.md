# DeepSeek Cowork Roadmap

Last updated: `2026-06-10`

This file is a shared project-status note for both the team and AI agents.
It records where the project is now, what has already landed, and what we are likely to focus on next.
Treat it as the current working map, not a strict release contract.

## Current Phase

**Phase:** Product polish and platform stabilization

The project has moved past early framework assembly and is now focused on:

- making the desktop experience feel more like a polished product and less like an internal tool
- stabilizing long-running agent, automation, and tool-use workflows
- keeping packaging, runtime bootstrap, and documentation aligned with real behavior

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

- Automation Center supports configured tasks, run history, and task templates
- Session-bound SOP / automation templates are supported
- Scheduled automation supports cron syntax and guided quick setup
- Manual-confirmation and auto-advance step execution flows are implemented

### Integration and operations

- MCP tool integration supports `stdio` and Streamable HTTP servers
- Enterprise IM gateway supports Feishu, DingTalk, and WeCom smart bots
- GitHub Release update flow exists for packaged builds
- Windows runtime bootstrap and packaged runtime resolution have been documented and hardened

### Quality and usability improvements

- Apple-style settings and automation surface polish has already been applied in key dialogs
- Streaming, markdown rendering, and background save behavior have been tuned for better responsiveness
- Long-term memory update and conversation-to-skill workflows are available with manual confirmation

## Current Focus

- Continue reducing "admin panel" visual weight in desktop surfaces
- Keep shared design tokens and reusable style helpers consistent across dialogs and drawers
- Improve doc consistency so user-facing Markdown stays aligned with the shipped UI
- Preserve behavior while making polish changes low-risk

## Candidate Next Steps

These are likely next areas, not hard commitments:

- further unify Apple-style visual language across remaining secondary panels
- continue hardening packaging and bundled runtime behavior on Windows
- deepen observability for multi-agent and long-running automation flows
- expand MCP-related capability beyond the current tools-only first step
- keep trimming friction in settings, automation, and session-control flows

## Working Rules For Team And AI

- Update this file when the project phase changes or a meaningful milestone lands
- Prefer short factual updates over speculative planning
- Reflect what is already true in the codebase before adding future work
- When UI behavior changes, sync this file with `README.md`, `README_CN.md`, and other affected docs

