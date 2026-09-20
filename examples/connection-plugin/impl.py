"""Minimal host integration example. Configure URL when adapting this example."""


def connected_service_status(_context=None):
    access = (_context or {}).get("connections")
    if access is None:
        return {"ok": False, "error": "请先在账号与连接中配置并授权此能力。"}
    from core.connections.errors import ConnectionError
    try:
        return access.request("default", "read", "GET", "https://api.example.com/status")
    except ConnectionError as exc:
        return exc.public()


TOOL_EXPORTS = [{
    "name": "connected_service_status",
    "handler": connected_service_status,
    "description": "Read the configured company service status.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}]
