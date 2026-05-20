---
name: history-query
description: Query chat history by keywords and date ranges.
description_cn: 按关键词与时间范围检索聊天历史。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
security_level: low
allowed-tools: query_history, upsert_message_embedding, query_history_vector
---

# History Query Skill

This skill searches the local chat history stored in SQLite.

## Tools

### query_history
Query chat history with optional keywords and date range.

### upsert_message_embedding
Upsert a vector embedding for a message.

### query_history_vector
Vector search over message embeddings.

## Current Runtime Notes
- Use history search when the user asks to recall prior discussions, previous decisions, or past task outcomes.
- Keyword queries and vector queries are read-only and can be combined with other independent read-only lookups through `parallel_tools` when the current mode exposes them.
- History records may include session metadata such as clarifying-mode state, selected skills, SOP run state, and IM provider bindings.
