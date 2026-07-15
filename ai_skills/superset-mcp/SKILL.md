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

Development deployments may use `MCP_DEV_USERNAME` on the Superset side. For per-user access, enable JWT authentication on the Superset MCP service and ensure it accepts the JWT issued by the Superset security login API.

## Cowork Setup

Configure the skill:

- `SUPERSET_BASE_URL`: Superset Web/API root URL, such as `https://superset.example.com`.
- `SUPERSET_MCP_URL`: Streamable HTTP endpoint, usually `/mcp`.
- `SUPERSET_USERNAME`: the user's Superset username.
- `SUPERSET_PASSWORD`: the user's Superset password.
- `SUPERSET_PROVIDER`: `db` or `ldap`; defaults to `db`.
- `SUPERSET_MCP_TIMEOUT_SECONDS`: optional timeout, default 30 seconds.

When generating the MCP preset, Cowork calls `/api/v1/security/login` with `refresh: true`. Access and refresh tokens stay in memory only. Cowork refreshes the access token before expiry and logs in again after restart or an expired refresh token. The generated server is disabled by default; enable and test it in Settings > MCP.

If `/mcp` returns 401 after login succeeds, verify `MCP_AUTH_ENABLED`, the MCP JWT algorithm/signing key, and `MCP_USER_RESOLVER` in `superset_config.py`. Cowork does not silently fall back to a development user.

## Safety Rules

- Confirm target dashboards, charts, datasets, and databases before high-impact operations.
- Executing SQL, creating charts, updating dashboards, and other write-like operations depend on Superset RBAC/JWT permissions and should be scoped to the user-approved target.
- Do not use third-party `superset-mcp` npm packages for this skill; this integration targets the official Superset MCP server.
