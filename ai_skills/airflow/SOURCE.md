# Source

- Astronomer Agents: https://github.com/astronomer/agents
- Local reference package: `D:\下载\airflow-main`
- Main upstream concepts: `astro-airflow-mcp`, `af` CLI, Airflow operations skill.

Cowork adapts the Airflow operations entrypoint as a default-off bundled AI Skill. Credentials are stored through `config_fields`, an MCP server preset can be generated for `astro-airflow-mcp`, and the `run_af` script entry executes `af` commands with read-only protection by default.
