---
name: interaction
description: Provides interaction capabilities with the user.
description_cn: 提供与用户进行交互（提问、确认）的能力。
license: Apache-2.0
allowed-tools: ask_user_confirmation, ask_user, publish_feishu_artifact
---

# Interaction Skill

This skill provides interaction capabilities with the user.

## Tools

### ask_user_confirmation
Ask the user for confirmation (Yes/No) or input about a specific action or question.
If the user provides text input, it will be returned as "User replied: ...".

- **message** (string, required): The message to display to the user.

### ask_user
Alias of `ask_user_confirmation` for compatibility with model-generated tool names.

- **message** (string, required): The message to display to the user.

### publish_feishu_artifact
Publish generated files/images for user delivery and rendering.
Use this tool when:
- A task produced files and user needs them delivered or displayed.
- User asks to view image output or receive downloadable files.
- This tool is the only supported delivery entry for file/image handoff.
- This tool is Feishu-specific and should only be used for Feishu interactions.

- **items** (array, required): list of artifacts. Each item supports:
  - `path` or `url`
  - `name`
  - `mime`
  - `subtype` (`image` recommended for images)
  - `caption`
- **audience** (string, optional): `feishu` only (default: `feishu`)
- **tool_summary** (string, optional): summary text for timeline display
- **card_title** (string, optional): title used when sending post link messages

接收目标由系统内置配置与运行时渠道上下文自动解析（优先 `feishu_receive_id_type` / `feishu_receive_id`，其次当前 IM 事件上下文），不允许通过工具参数传入。

Output contract:
- Must return JSON string with `source_tool="publish_feishu_artifact"`.
- Must include `content_parts` (file/tool_event) and `delivery_result`.
- This tool is Feishu-only and does not target desktop delivery.
