---
name: meta-tools
description: Meta skill for recording verified, non-sensitive, cross-task lessons and running independent read-only tools in parallel.
description_cn: 用于沉淀已验证、非敏感、可跨任务复用的高价值经验，并并行执行独立只读工具。
type: system
created_by: system
allowed-tools: [parallel_tools, update_experience]
---

# Meta Tools

Tools for controlled read-only parallelism and high-value experience maintenance.

## Tools

### parallel_tools
Executes multiple independent read-only tool calls concurrently and returns ordered results.

**When to use:**
- When several file reads, grep/glob searches, or data lookups are independent and can run in parallel.
- When you want one explicit tool call to fan out across multiple read-only tools.
- Do not use it for writes, shell commands, approvals, user input, experience updates, or agent management.

**Runtime rules:**
- Each subcall must already be visible or discovered in the current run context.
- Each subcall must be allowed by the current mode, selected-skill scope, and agent profile.
- Each subcall must be marked read-only and non-destructive.
- Results preserve the order of the input call list, even if faster calls finish earlier.
- A denied or failed subcall returns a structured partial error; unsafe write tools are not executed.

### update_experience
Autonomously record a verified, reusable, non-sensitive lesson in project or global memory during execution. No per-lesson user approval is needed. This tool never changes a Skill.

- Record confirmed fixes, reproducible limitations with clear conditions, explicit lasting user corrections, or a validated method that reduces future trial and error.
- A failure alone is not a lesson. Separate verified observations from an unknown root cause. Never store routine success, temporary outages, project progress, guesses, secrets, logs or whole conversations.
- Reuse the exact text already read in this scope when merging a related lesson. Call `read_memories` when the relevant entry is missing or a conflict is reported. Current instructions and evidence take priority.
- `scope`: required `project` or `global`. Project-specific rules belong to the current registered project; cross-project methods and stable environment constraints belong to global memory. The host chooses the actual project. No project is not a reason to write global memory.
- `experience`: required concise single paragraph without line breaks; state applicability, action and necessary verification evidence.
- `previous_experience`: optional exact old lesson text without its Markdown bullet, read from the same scope, to replace. Omit to add. A stale or ambiguous match fails; reread before merging.
- Grilling remains read-only. Reconsider recording after entering execution; do not queue automatic writes.
- Creation sessions are independent chats, not projects, and do not inherit their source project's write target. Only verified cross-project lessons may go to global memory.
- Writes affect the next run's prompt. Unchanged lessons are not written again. On failure keep the original memory and report the reason; never claim success or bypass through file or shell tools.
- User-requested reusable workflows still use the reviewed “沉淀为 Skill” flow. Explicit Skill edits belong to `skill_builder`; explicit general memory changes use `write_memories`.
