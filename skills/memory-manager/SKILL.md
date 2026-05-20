---
name: memory-manager
description: Read and update memories.md in the history directory.
description_cn: 读取与更新历史目录中的 memories.md。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: low
allowed-tools: read_memories, write_memories
---

# Memory Manager Skill

This skill manages the memories.md file stored alongside chat history.

## Tools

### read_memories
Read the current memories.md content.

### write_memories
Append or replace content in memories.md.

## Current Runtime Notes
- `memories.md` is the long-term memory layer for stable preferences, durable project context, and reusable personal conventions.
- The UI-level `更新长期记忆` flow performs batch history scanning, preview/edit, backup, and `memories_update_state.json` tracking; use these direct tools only when the model has a narrow memory edit to make during a conversation.
- Do not store secrets, temporary debugging noise, or one-off implementation details.
- Memory writes are not read-only and must not be run through `parallel_tools`.
