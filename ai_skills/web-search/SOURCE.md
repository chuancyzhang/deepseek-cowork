# Web Search upstream sources

## AnySearch

- Project: https://github.com/anysearch-ai/anysearch-skill
- Pinned release: `v3.0.1`
- Release commit: `caed9ea`
- License: Apache-2.0
- Integration: Cowork keeps its existing Python tool surface and adapts the AnySearch JSON-RPC `search`, `batch_search`, `get_sub_domains`, and `extract` operations.
- Registration endpoint: `https://api.anysearch.com/v1/auth/email/register`

Cowork does not bundle the unused cross-platform AnySearch CLI files. The provider contract and AI-facing workflow are adapted from the pinned release, and the required attribution is retained in `LICENSE.anysearch` and `NOTICE.anysearch`.

## Tavily

- Python SDK: https://github.com/tavily-ai/tavily-python
- Pinned package: `tavily-python==0.7.26`
- License: MIT
- Integration scope: `search` and `extract` only. Crawl, Map, and Research are intentionally out of scope.
