---
name: showdoc-mcp
description: ShowDoc MCP skill for searching and reading project API documentation through the mcp-showdoc stdio server.
kind: knowledge
source_type: bundled_plugin
default_enabled: false
---

# ShowDoc MCP

Use this skill when the user asks to search, list, or read ShowDoc API documentation.

## Setup

Configure the skill in the Skill Center:

- `SHOWDOC_HOST`: ShowDoc server URL, including protocol.
- `SHOWDOC_LOGIN_SECRET_KEY`: login secret key from ShowDoc user settings.
- `SHOWDOC_PROJECT_NAME`: the project name to lock this MCP server to.
- `SHOWDOC_USERNAME`: optional username.

After saving configuration, use "Generate / Update MCP Configuration" to create the `showdoc` stdio MCP server. The generated server is disabled by default; enable and test it in Settings > MCP.

## MCP Server

The generated preset uses:

```json
{
  "command": "npx",
  "args": ["-y", "mcp-showdoc", "--host", "...", "--login_secret_key", "...", "--project_name", "..."]
}
```

## Safety Rules

- Keep the project name scoped to the exact project the user wants to expose.
- Do not ask ShowDoc for unrelated projects unless the user changes the configuration.
- Treat `SHOWDOC_LOGIN_SECRET_KEY` as a secret.
