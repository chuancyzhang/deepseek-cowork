---
name: feishu-docs
description: Feishu document, wiki, drive, sheet, media, comment, and OAuth skill pack.
description_cn: 飞书文档、知识库、云空间、表格、媒体、评论和 OAuth 授权能力包。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
security_level: medium
---

# Feishu Docs

This bundled optional skill vendors the document-related parts of `hashSTACS-Global/feishu-skills`.

## Capabilities

- Authorize a Feishu user with OAuth Device Flow.
- Create Feishu Docs from Markdown.
- Fetch Feishu Docs or Wiki pages as Markdown.
- Search cloud docs, Wiki nodes, and Drive folder entries.
- Append to or overwrite document content.
- Download document files and attachments.
- Manage Feishu Drive folders, Wiki nodes, document comments, document media, and sheets.

## Runtime

- Configure `FEISHU_APP_ID` and `FEISHU_APP_SECRET` in the Cowork skill workbench.
- Run the `auth` script entry first when a Feishu user token is missing.
- Use each vendored sub-skill `SKILL.md` for exact script arguments before running a script entry.
