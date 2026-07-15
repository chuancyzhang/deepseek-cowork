# Source

- Upstream skill: https://clawhub.ai/lyingbug/skills/weknora
- Upstream install command: `openclaw skills install @lyingbug/weknora`
- Official project and MCP configuration: https://github.com/Tencent/WeKnora
- Python MCP package: https://pypi.org/project/weknora-mcp-server/

Cowork bundles WeKnora as a default-off AI Skill, installs the official MCP package into the Skill Python sandbox, and injects credentials only into the stdio MCP process. A small local entrypoint calls the package server directly so startup text cannot corrupt MCP JSON-RPC stdout.
