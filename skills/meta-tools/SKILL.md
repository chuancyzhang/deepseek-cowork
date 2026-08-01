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
Records a verified, reusable lesson or updates a Skill description/instructions when the user explicitly requests that maintenance.

**When to use:**
- A failure was reproduced, its root cause was confirmed, the fix was verified, and the lesson applies across tasks.
- A stable, non-sensitive configuration rule was validated and will materially prevent future errors.
- The user explicitly asks to correct a Skill description or usage instructions.

**Do not use:**
- Routine task success, unverified guesses, temporary project state, full conversation content, secrets, or personal data.
- To modify Skill guidance without an explicit user request.

### update_experience Parameters:
- `skill_name`: The name of the skill to update.
- `experience`: (Optional) A concise, actionable sentence describing the lesson learned (appended to existing).
- `description`: (Optional) A new summary of what the skill does (replaces existing).
- `instructions`: (Optional) The full markdown body explaining how to use the skill (replaces existing).
