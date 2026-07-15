# Source

- Astronomer Agents: https://github.com/astronomer/agents
- Local reference package: `D:\下载\airflow-main`
- Main upstream concepts: `astro-airflow-mcp`, `af` CLI, Airflow operations skill.

Cowork adapts the Airflow operations entrypoint as a default-off bundled AI Skill. Credentials are stored through `config_fields`; both the MCP module and `run_af` execute from the isolated Skill Python dependency environment, with read-only protection enabled by default. The integration does not depend on a system `uvx` executable.
