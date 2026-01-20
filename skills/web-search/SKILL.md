---
name: web-search
description: Provides capabilities to search the web and read online articles.
description_cn: 提供搜索互联网内容和读取网络文章的能力。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
security_level: medium
allowed-tools: search_web read_article
---

# Web Search Skill

This skill allows the agent to search the internet for information and extract content from web pages.

## Capabilities
1. **Search Web**: Search using DuckDuckGo to find relevant URLs and snippets.
2. **Read Article**: Extract main text content from a given URL (removing ads/navbars).

## Usage Guidelines
- **Privacy**: Searches are performed via DuckDuckGo (privacy-focused).
- **Rate Limits**: Avoid making excessive requests in a short loop.
- **Content**: Reading articles extracts text only; images and complex layouts are ignored.
