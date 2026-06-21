---
name: memory-manager
description: Read and update layered long-term memory summaries.
description_cn: 读取与更新分层长期记忆摘要。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: low
allowed-tools: read_memories, write_memories
---

# Memory Manager Skill

This skill manages the layered memory store saved alongside chat history.

## Tools

### read_memories
Read the applicable global and workspace summaries.

### write_memories
Append or replace the global summary with versioned storage.

## Current Runtime Notes
- The always-on layer contains the soul prompt plus global/current-workspace summaries.
- The UI Memory Center owns generation review, summary editing, backups, and scoped `memories_update_state.json` tracking; use direct writes only when the user explicitly asks the model to remember something.
- Do not store secrets, temporary debugging noise, or one-off implementation details.
- Memory writes are not read-only and must not be run through `parallel_tools`.
