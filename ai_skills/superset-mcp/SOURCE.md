# Source

- Official MCP server deployment and authentication: https://superset.apache.org/admin-docs/configuration/mcp-server/
- Official extension MCP integration: https://superset.apache.org/developer-docs/extensions/mcp/

- Official security login API: https://superset.apache.org/developer-docs/api/create-security-login/
- Official security refresh API: https://superset.apache.org/developer-docs/api/create-security-refresh/

Cowork connects to the official Superset MCP service over Streamable HTTP. It obtains the user's access and refresh tokens from the Superset REST security API, keeps them in memory, and sends the access token as a Bearer header. It does not use third-party `superset-mcp` npm packages and does not start Superset itself.
