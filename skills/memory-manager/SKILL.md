---
name: memory-manager
description: Read summaries and discover editable long-term memory modules.
description_cn: 读取摘要并检索可编辑的长期记忆模块。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: low
allowed-tools: read_memories, write_memories, list_memory_modules, search_memory_modules, read_memory_module
---

# Memory Manager Skill

This skill manages the layered memory store saved alongside chat history.

## Tools

### read_memories
Read the applicable global and workspace summaries.

### write_memories
Append or replace the global summary with versioned storage.

### list_memory_modules / search_memory_modules / read_memory_module
Discover and read enabled global or current-workspace memory modules on demand.

## Current Runtime Notes
- The always-on layer contains the soul prompt plus global/current-workspace summaries. Detailed modules are searched and read only when relevant.
- The UI Memory Center owns generation review, module editing, backups, and `memories_update_state.json` tracking; use direct writes only when the user explicitly asks the model to remember something.
- Do not store secrets, temporary debugging noise, or one-off implementation details.
- Memory writes are not read-only and must not be run through `parallel_tools`.
