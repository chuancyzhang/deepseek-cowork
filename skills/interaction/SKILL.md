---
name: interaction
description: Provides interaction capabilities with the user.
description_cn: 提供与用户进行交互（提问、确认）的能力。
license: Apache-2.0
allowed-tools: request_user_approval, request_user_input, publish_artifacts
---

# Interaction Skill

This skill provides interaction capabilities with the user, plus enterprise-message artifact delivery for enterprise IM sessions.

## Tools

### request_user_approval
Ask the user to approve or reject a potentially important action.

- **message** (string, required): The message to display to the user.
- **title** (string, optional): dialog title.
- **severity** (string, optional): risk level hint (`low` / `medium` / `high`).
- **timeout_seconds** (number, optional): timeout before the request is cancelled.
- **details** (string, optional): extra context for the approval dialog.

### request_user_input
Ask the user for text, a single choice, or multiple choices.

- **message** (string, required): The message to display to the user.
- **title** (string, optional): dialog title.
- **input_mode** (string, optional): `text`, `choice`, or `multi_choice`.
- **options** (array, optional): list of selectable options.
- **allow_free_text** (boolean, optional): allow arbitrary free-text input.
- **timeout_seconds** (number, optional): timeout before the request is cancelled.

### publish_artifacts
Publish generated files/images for enterprise-message delivery.
Use this tool when:
- An enterprise-message task produced files and the user needs them delivered in that conversation.
- User asks to send image output or downloadable files into the current enterprise IM conversation.
- This tool is the supported delivery entry for enterprise-message file/image handoff.

- **items** (array, required): list of artifacts. Each item supports:
  - `path` or `url`
  - `name`
  - `mime`
  - `subtype` (`image` recommended for images)
  - `caption`
- **audience** (string, optional): `auto` or provider-specific target such as `feishu`
- **summary** (string, optional): summary text for timeline display
- **title** (string, optional): title used when sending IM post link messages

接收目标由系统内置配置与运行时渠道上下文自动解析（飞书优先 `feishu_receive_id_type` / `feishu_receive_id`，其次当前 IM 事件上下文），不允许通过工具参数传入。

Output contract:
- Must return a structured object with `source_tool="publish_artifacts"`.
- Must include `content_parts` (file/tool_event) and `delivery_result`.
- Tool availability is restricted to enterprise-message sessions; normal desktop chats should hand off files by mentioning local paths or links in the final reply.

## Current Runtime Notes
- Clarifying mode should use `request_user_input` for actual user questions after read-only exploration.
- Approval and input tools are interactive and must not be batched through `parallel_tools`.
- In desktop sessions, generated local files should be reported in the final answer rather than published through this skill.
