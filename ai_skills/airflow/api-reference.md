# af api Reference

Use `af api` for Airflow REST API endpoints not covered by high-level commands.

## Discovery

```bash
af api ls
af api ls --filter variable
af api spec
```

## HTTP Methods

```bash
af api dags
af api dags/my_dag
af api dags -F limit=10 -F only_active=true
af api variables -X POST -F key=my_var -f value="my value"
af api dags/my_dag -X PATCH -F is_paused=false
af api variables/old_var -X DELETE
```

## Field Syntax

| Flag | Behavior |
| --- | --- |
| `-F key=value` | Auto-converts booleans, numbers, and null |
| `-f key=value` | Keeps the value as a raw string |
| `--body '{}'` | Sends raw JSON |
| `-F key=@file` | Reads value from file |

## Safety

Treat `POST`, `PATCH`, and `DELETE` as write operations. Confirm the exact resource and intended change before running them, and keep `AF_READ_ONLY=true` unless the user intentionally enables writes.
