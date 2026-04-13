---
name: agent-manager
description: Tool-first multi-agent runtime controls for the current conversation.
description_cn: 面向工具的多代理运行时控制能力。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "2.0"
security_level: medium
allowed-tools: [spawn_agent, send_input, wait_agent, close_agent, list_agents]
---

# Agent Manager Skill

This skill exposes dedicated tools to manage persisted sub-agents in the current conversation.

## Capabilities
1. `spawn_agent`: create a sub-agent for a task.
2. `send_input`: continue an existing sub-agent with another user message.
3. `wait_agent`: wait until one or more sub-agents complete.
4. `close_agent`: stop and close a sub-agent.
5. `list_agents`: inspect persisted sub-agent summaries.

## Usage Guidelines
- These tools are only available to the main agent.
- Sub-agents are not allowed to spawn or manage other sub-agents.
- Agent records and transcripts are persisted in `ChatStorage` (`agents` and `agent_messages` tables).
