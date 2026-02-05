---
name: memory-manager
description: Read and update memories.md in the history directory.
description_cn: 读取与更新历史目录中的 memories.md。
license: Apache-2.0
metadata:
  author: cowork-team
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
