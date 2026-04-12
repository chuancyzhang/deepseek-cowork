---
name: web-search
description: Provides capabilities to search the web and read online articles.
description_cn: 提供搜索互联网内容和读取网络文章的能力。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "2.0"
security_level: medium
allowed-tools: search_web read_article
---

# Web Search Skill

This skill provides a structured free web search pipeline and a markdown-first article extraction pipeline.

## Capabilities
1. **Search Web**: Search using free public search providers with fixed fallback order: DuckDuckGo -> Bing.
2. **Structured Output**: Both tools return JSON object strings with `ok`, `error`, metadata, and normalized content.
3. **Read Article**: Extract article text with fixed fallback order: `markdown.new` -> `defuddle.md` -> `r.jina.ai` -> `Scrapling`.
4. **Context Safety**: `read_article` returns cleaned text content only and does not return raw HTML by default.

## Usage Guidelines
- **Search Stability**: The search tool retries requests, records fallback chains, de-duplicates URLs, and limits over-representation from the same domain.
- **Domain Controls**: `search_web` supports `allowed_domains` and `blocked_domains`, but they are mutually exclusive.
- **Article Extraction**: `read_article` never treats raw HTML as a successful default response.
- **Response Contract**: Callers should parse JSON and inspect `ok`, `error`, `provider_used` / `extractor_used`, `results`, and `content`.
- **Fallback Order**: `markdown.new` → `defuddle.md` → `r.jina.ai` → `Scrapling`.
