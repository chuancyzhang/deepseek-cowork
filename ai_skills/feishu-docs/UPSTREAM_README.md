[中文](README.zh.md) | English

# feishu-skills

A complete set of Feishu (Lark / 飞书) integration skills for [OpenClaw](https://github.com/openclaw/openclaw) and [EnClaws](https://github.com/hashSTACS-Global/EnClaws).

Enables AI agents to read/write Feishu documents, send messages, manage calendars, tasks, and multi-dimensional tables — all authenticated with per-user OAuth tokens (not bot-owner tokens).

---

## 30-Second Feishu Setup

> **No more manual app creation.** Scan a QR code with Feishu, and your bot is ready.

After installation, just tell your agent:

```
Help me set up the Feishu plugin
```

The agent will generate a QR code link → you scan it with the Feishu app → bot credentials are automatically saved to your OpenClaw config. Done.

See [feishu-quick-setup](#skills-included) for details.

---

## Installation

```bash
git clone https://github.com/hashSTACS-Global/feishu-skills.git
cd feishu-skills
node install.js
```

---

## Skills Included

| Skill | Description |
|---|---|
| **feishu-quick-setup** | One-click Feishu bot creation via QR code scan. Automatically writes credentials to OpenClaw config. |
| **feishu-auth** | OAuth Device Flow auth hub. Shared by all other skills. |
| **feishu-create-doc** | Create Feishu Docs with Markdown content |
| **feishu-fetch-doc** | Read Feishu Docs / Wiki pages |
| **feishu-search-doc** | Search cloud docs, wiki spaces/nodes, and filter drive folder entries by name |
| **feishu-update-doc** | Update Feishu Docs (append or overwrite blocks) |
| **feishu-im-read** | Read Feishu IM chat history |
| **feishu-chat** | Search group chats, get chat details, list members |
| **feishu-calendar** | Create / query / update calendar events |
| **feishu-task** | Create / query / update tasks and task lists |
| **feishu-bitable** | Full CRUD on Bitable apps, tables, fields, records, and views |
| **feishu-docx-download** | Download file attachments from Feishu Wiki and extract text content (docx/pdf/pptx/xlsx/xls/html/rtf/epub/txt/csv, etc.) |
| **feishu-drive** | Feishu Drive folder operations (currently: list folder items, create folder) |
| **feishu-image-ocr** | Image OCR via Feishu API. Chinese/English, pure Node.js, zero extra deps. |
| **feishu-search-user** | Search users by keyword, get current user profile, or look up a user by `user_id` / `union_id` |
| **feishu-sheet** | Read/write Feishu Spreadsheets (Sheets) — info, read, write, append, find, create, export |
| **feishu-wiki** | Feishu Wiki space and node management — list/get/create spaces; list/get/create/move/copy nodes |


---

## Requirements

- **Node.js** ≥ 18 (uses built-in `fetch`)
- **[OpenClaw](https://github.com/openclaw/openclaw)** or **[EnClaws](https://github.com/hashSTACS-Global/EnClaws)** installed
- A Feishu app with the following permissions (see [Configuration](#configuration)):
  - `docs:doc`, `wiki:wiki:readonly`, `drive:drive`
  - `im:message`, `im:message:readonly`
  - `calendar:calendar`
  - `task:task`
  - `bitable:app`

---

## Configuration

### OpenClaw

Your `~/.openclaw/openclaw.json` must have a `channels.feishu` section with your app credentials:

```json
{
  "channels": {
    "feishu": {
      "appId": "cli_xxxxxxxxxxxxxxxx",
      "appSecret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    }
  },
  "tools": {
    "deny": [
      "feishu_doc",
      "feishu_create_doc",
      "feishu_fetch_doc",
      "feishu_update_doc",
      "feishu_wiki",
      "feishu_wiki_*",
      "feishu_drive_*",
      "feishu_search_doc_wiki",
      "feishu_chat",
      "feishu_chat_members",
      "feishu_im_*",
      "feishu_bitable_*",
      "feishu_calendar_*",
      "feishu_task_*",
      "feishu_pre_auth"
    ],
    "exec": {
      "security": "full",
      "ask": "off"
    }
  }
}
```

> **⚠️ IMPORTANT: `tools` must be at the top level, NOT inside `channels.feishu`.**
> OpenClaw's `channels.feishu.tools` uses a different schema (boolean flags only) — placing `deny` there will be **silently ignored** and the built-in plugin tools will remain active, causing the agent to bypass these skills entirely.

> **Why `tools.deny`?** OpenClaw has built-in Feishu plugin tools (`feishu_doc`, etc.). These skills replace them with richer, per-user OAuth versions. Denying the built-in tools ensures the agent uses these skills instead.

> **Why `tools.exec`?** These skills rely on the `exec` tool to run Node.js scripts. Without `"security": "full"` and `"ask": "off"`, the exec tool may be restricted or require manual confirmation for each invocation.

### EnClaws

Credentials are injected automatically via environment variables (`FEISHU_APP_ID`, `FEISHU_APP_SECRET`). No manual configuration needed.

---

## How Authentication Works

These skills use **Feishu OAuth Device Flow** — each user authorizes once via a link, and their token is stored locally in `feishu-auth/.tokens/<open_id>/`.

**First use flow:**
1. User asks the agent to do something (e.g., create a doc)
2. Script returns `{"error": "auth_required"}` with an auth URL
3. Agent presents an authorization card/link to the user
4. User clicks and authorizes in Feishu
5. Agent automatically retries the original operation
6. Token is saved; future calls are transparent

**Token lifecycle:**
- Access tokens auto-refresh via refresh token (no re-auth needed)
- Tokens are stored per user (`open_id`) per app (`appId`)
- Re-auth only needed if refresh token expires (~30 days of inactivity)

---

## Credential Resolution Order

Scripts resolve app credentials in this order:

1. **Environment variables** `FEISHU_APP_ID` + `FEISHU_APP_SECRET` ← EnClaws injects these
2. **`feishu-auth/config.json`** ← manual single-app setup
3. **`~/.openclaw/openclaw.json`** → `channels.feishu.appId/appSecret` ← OpenClaw standard

---

## Usage Examples

Once installed, just talk to your agent naturally:

```
Create a Feishu doc titled "Q1 OKR" with the following content...
Read this Feishu doc: https://xxx.feishu.cn/docx/...
Show me my calendar events for today
Create a task: submit the report by 3 PM tomorrow
Add a new record to this Bitable: ...
```

---

## Project Structure

```
feishu-skills/
├── feishu-auth/          # Auth hub (shared by all skills)
│   ├── SKILL.md
│   ├── auth.js           # Device Flow initiation + polling
│   └── token-utils.js    # Token read/write/refresh utilities
├── feishu-create-doc/
│   ├── SKILL.md
│   └── create-doc.js
├── feishu-fetch-doc/
│   ├── SKILL.md
│   └── fetch-doc.js
├── feishu-search-doc/
│   ├── SKILL.md
│   └── search-doc.js
├── feishu-update-doc/
│   ├── SKILL.md
│   └── update-doc.js
├── feishu-im-read/
│   ├── SKILL.md
│   └── im-read.js
├── feishu-chat/
│   ├── SKILL.md
│   └── chat.js
├── feishu-calendar/
│   ├── SKILL.md
│   └── calendar.js
├── feishu-task/
│   ├── SKILL.md
│   └── task.js
├── feishu-bitable/
│   ├── SKILL.md
│   └── bitables.js
├── feishu-docx-download/
│   ├── SKILL.md
│   ├── download-doc.js
│   └── extract.js
├── feishu-drive/
│   ├── SKILL.md
│   └── drive.js
├── feishu-image-ocr/
│   ├── SKILL.md
│   └── ocr.js            # Feishu OCR API caller
├── feishu-search-user/
│   ├── SKILL.md
│   └── search-user.js    # Search Feishu users by name
├── feishu-sheet/
│   ├── SKILL.md
│   └── sheet.js          # Spreadsheet read/write/find/create/export
├── feishu-wiki/
│   ├── SKILL.md
│   └── wiki.js           # Wiki space & node management
└── install.js            # Cross-platform installer
```

---

## Compatibility

| Environment | Status |
|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) (original) | ✅ Fully supported |
| [EnClaws](https://github.com/hashSTACS-Global/EnClaws) | ✅ Fully supported |
| macOS | ✅ |
| Linux | ✅ |
| Windows | ✅ |

