---
name: superset-mcp
description: Connect to the official Apache Superset MCP server over Streamable HTTP. Use for Superset MCP tools exposed by a running Superset instance at /mcp.
kind: knowledge
source_type: bundled_plugin
default_enabled: false
---

# Superset MCP

Use this skill when the user wants to connect Cowork to an official Apache Superset MCP server.

## Server Setup

Cowork does not start Superset. The user must run or expose the official Superset MCP service from the Superset side, for example:

```bash
superset mcp run --host 127.0.0.1 --port 5008
```

The default endpoint is:

```text
http://localhost:5008/mcp
```

Development deployments may use `MCP_DEV_USERNAME` on the Superset side. Production deployments should use Bearer/JWT authentication and rely on Superset RBAC and audit logging.

## Cowork Setup

Configure the skill:

- `SUPERSET_MCP_URL`: Streamable HTTP endpoint, usually `/mcp`.
- `SUPERSET_MCP_BEARER_TOKEN`: Bearer token for Superset MCP.
- `SUPERSET_MCP_TIMEOUT_SECONDS`: optional timeout, default 30 seconds.

After saving configuration, generate the MCP preset. The generated server is disabled by default; enable and test it in Settings > MCP.

## Safety Rules

- Confirm target dashboards, charts, datasets, and databases before high-impact operations.
- Executing SQL, creating charts, updating dashboards, and other write-like operations depend on Superset RBAC/JWT permissions and should be scoped to the user-approved target.
- Do not use third-party `superset-mcp` npm packages for this skill; this integration targets the official Superset MCP server.
