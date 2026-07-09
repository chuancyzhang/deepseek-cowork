---
name: airflow
description: Queries, manages, and troubleshoots Apache Airflow using Astronomer Agents, astro-airflow-mcp, and the af CLI. Use for DAGs, DAG runs, task logs, import errors, Airflow health, connections, variables, pools, and REST API exploration.
kind: knowledge
source_type: bundled_plugin
default_enabled: false
---

# Airflow Operations

Use this skill for Apache Airflow operations and troubleshooting. It follows the Astronomer Agents Airflow workflow and supports both MCP and the `af` CLI.

## Setup

Configure the skill in Skill Center:

- `AIRFLOW_API_URL`: Airflow webserver base URL.
- `AIRFLOW_AUTH_TOKEN`: preferred token auth.
- `AIRFLOW_USERNAME` and `AIRFLOW_PASSWORD`: alternative username/password auth.
- `AF_READ_ONLY`: defaults to `true`; keep enabled unless the user explicitly wants write operations.

Authentication requires either `AIRFLOW_AUTH_TOKEN` or `AIRFLOW_USERNAME` plus `AIRFLOW_PASSWORD`.

## MCP Server

After saving configuration, generate the `airflow` MCP preset. It starts `astro-airflow-mcp` through `uvx` over `stdio` and injects the configured Airflow environment.

## CLI Workflow

Use the `run_af` script entry for direct `af` commands:

```json
{
  "skill_name": "airflow",
  "script_name": "run_af",
  "args": ["health"]
}
```

Common `af` commands:

| Intent | Command |
| --- | --- |
| Health check | `af health` |
| List DAGs | `af dags list` |
| Inspect DAG | `af dags explore <dag_id>` |
| List runs | `af runs list --dag-id <dag_id>` |
| Diagnose failed run | `af runs diagnose <dag_id> <run_id>` |
| Task logs | `af tasks logs <dag_id> <run_id> <task_id>` |
| Direct API discovery | `af api ls` |

See `api-reference.md` for direct REST API examples.

## Safety Rules

- Default to read-only operations.
- Do not trigger DAGs, pause/unpause DAGs, clear/delete runs, or create/update/delete variables/connections unless the user explicitly confirms the exact target.
- `run_af` blocks common write operations while `AF_READ_ONLY` is `true`.
- Run discovery commands with dry-run first when they can create tokens or persistent config.

## Related Upstream

This skill is adapted from Astronomer Agents and the local `D:\下载\airflow-main` skill package. Astronomer Agents provides `astro-airflow-mcp` and the `af` CLI used by this integration.
