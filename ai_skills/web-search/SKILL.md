---
name: web-search
description: Provides real-time web search and article extraction through AnySearch or Tavily.
description_cn: 通过 AnySearch 或 Tavily 提供实时网页搜索和正文提取。
license: Apache-2.0
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
metadata:
  author: deepseek-cowork team
  version: "3.0.1-cowork.1"
security_level: medium
allowed-tools: search_web read_web_article batch_search_web get_web_search_sub_domains register_anysearch_api_key
---

# Web Search Skill

Use `search_web` and `read_web_article` for current internet information. The configured provider is used unless a call explicitly selects `anysearch` or `tavily`.

## Provider rules

- Never switch providers silently. If a provider fails, report its root cause and ask the user before explicitly retrying with the other provider.
- Both providers support limited keyless access. Warn that rate limits and quotas are lower when `auth_mode` is `keyless`.
- Use AnySearch vertical search for finance, academic, travel, health, code, legal, security, and other supported domains. Call `get_web_search_sub_domains` before the vertical search to obtain required parameters.
- Use `batch_search_web` only with AnySearch and at most five queries.
- Search queries, extracted URLs, and credentials are sent to the selected third-party provider. Do not send secrets or sensitive personal data.

## AnySearch registration

`register_anysearch_api_key` creates an external account and persists a one-time credential. Before calling it, the AI must:

1. Explain that the email becomes the AnySearch username and a generated password is sent to it.
2. Obtain explicit consent both to create the account and to save the returned API Key locally.
3. Call the tool with `user_confirmed=true` only after that consent.

The tool never returns or logs the complete API Key. On success, tell the user to check both the inbox and spam folder for the generated password and verification email.

## Compatibility

- `search_web` and `read_web_article` keep the original Cowork tool names and normalized JSON response shape.
- `allowed_domains` and `blocked_domains` are mutually exclusive.
- Unsupported compatibility parameters fail explicitly; they are never ignored.
- `read_article` remains a Python compatibility alias for `read_web_article`.
