---
name: interaction
description: Provides interaction capabilities with the user.
description_cn: 提供与用户进行交互（提问、确认）的能力。
license: Apache-2.0
allowed-tools: request_user_approval, request_user_input, publish_artifacts
---

# Interaction Skill

This core built-in Skill is exposed directly when allowed by the current run context. It provides user interaction plus channel-aware artifact delivery.

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
- **purpose** (string, optional): runtime intent marker; `grill_checkpoint` is reserved for the grilling summary decision.
- **options** (array, optional): list of selectable options.
- **allow_free_text** (boolean, optional): allow arbitrary free-text input.
- **timeout_seconds** (number, optional): timeout before the request is cancelled.

For task clarification, use questionnaire questions with mutually exclusive choices and put the recommended choice first. The runtime removes any caller-supplied custom choice and appends exactly one `自定义` option automatically; callers must not add it themselves.
Questionnaires issued while the runtime is in grilling mode never auto-select a recommended answer on timeout.

### publish_artifacts
Publish generated artifacts only when the current messaging channel advertises artifact delivery.
Use this tool when:
- A Feishu task needs local files, images, or links delivered in that conversation.
- A DingTalk or WeCom task needs an accessible URL delivered in that conversation.

Channel contract:
- Feishu: native local file/image upload and URL delivery.
- DingTalk / WeCom: URL-only delivery; local-only paths are reported as not delivered.
- QQ / WeChat: this Tool is not exposed.
- Desktop: this Tool is not exposed; report the real local path or URL in the final reply.

- **items** (array, required): list of artifacts. Each item supports:
  - `path` or `url`
  - `name`
  - `mime`
  - `subtype` (`image` recommended for images)
  - `caption`
- **audience** (string, optional): `auto`, `feishu`, `dingtalk`, or `wecom`
- **summary** (string, optional): summary text for timeline display
- **title** (string, optional): title used when sending IM post link messages

接收目标由系统内置配置与运行时渠道上下文自动解析（飞书优先 `feishu_receive_id_type` / `feishu_receive_id`，其次当前 IM 事件上下文），不允许通过工具参数传入。

Output contract:
- Must return a structured object with `source_tool="publish_artifacts"`.
- Must include `content_parts` (file/tool_event) and `delivery_result`.
- Tool availability is restricted to channels whose registry `artifact_delivery_mode` is `native` or `link`.
- Delivery success must be read from `delivery_result`; skipped or failed items must never be described as sent.

## Current Runtime Notes
- Clarifying mode should use `request_user_input` for actual user questions after read-only exploration.
- Approval and input tools are interactive and must not be batched through `parallel_tools`.
- In desktop, QQ, and WeChat sessions, generated local files should be reported as local-only rather than published through this skill.
