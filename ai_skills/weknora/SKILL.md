---
name: weknora
description: Import documents and perform knowledge retrieval through the official WeKnora MCP server. Use for uploading files, importing URLs or Markdown, listing knowledge bases, and hybrid search across knowledge bases.
kind: knowledge
source_type: bundled_plugin
default_enabled: false
---

# WeKnora

Use this skill when the user asks to manage or search a WeKnora knowledge base.

## Setup

Configure the skill in the Skill Center before use:

- `WEKNORA_BASE_URL`: WeKnora API base URL, usually ending in `/api/v1`.
- `WEKNORA_API_KEY`: API key from the WeKnora web UI.

Stop and ask the user to configure the skill if either value is missing.

Saving the Skill configuration automatically creates, updates, and enables the managed `weknora` MCP entry. Cowork installs `weknora-mcp-server` into the isolated Skill Python environment, starts it over stdio on demand, and injects both values into that process. Use the separate connection test when diagnostics are needed.

## API Workflow

The official MCP server performs requests using `WEKNORA_BASE_URL` and the header `X-API-Key: WEKNORA_API_KEY`.

Common endpoints:

| Intent | Endpoint |
| --- | --- |
| List knowledge bases | `GET /knowledge-bases` |
| View knowledge base details | `GET /knowledge-bases/:id` |
| Upload a file | `POST /knowledge-bases/:id/knowledge/file` |
| Import a URL | `POST /knowledge-bases/:id/knowledge/url` |
| Write Markdown knowledge | `POST /knowledge-bases/:id/knowledge/manual` |
| Check parsing progress | `GET /knowledge/:id` |
| Browse entries | `GET /knowledge-bases/:id/knowledge` |
| Search within one KB | `GET /knowledge-bases/:id/hybrid-search` |
| Search across KBs | `POST /knowledge-search` |

## Safety Rules

- Confirm the target knowledge base before upload, URL import, or Markdown creation.
- Editing existing Markdown knowledge with `PUT /knowledge/manual/:id` requires explicit user confirmation of the exact knowledge entry ID.
- Deleting a knowledge entry with `DELETE /knowledge/:id` requires explicit user confirmation of the exact knowledge entry ID. Do not infer deletion targets.
- When parsing fails, inspect `error_message` before retrying.

## Notes

- File upload uses `multipart/form-data`, not JSON.
- Search scores range from 0 to 1; higher is more relevant.
- `parse_status` values are usually `pending`, `processing`, `completed`, or `failed`.
