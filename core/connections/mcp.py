"""Optional MCP credential injection without changing any model-facing tool schema."""
import copy
import os
import uuid

from .broker import ConnectionBroker, task_identity
from .errors import ConnectionError


def broker_for(config_manager=None):
    data_dir = getattr(config_manager, "data_dir", None)
    return ConnectionBroker(data_dir=data_dir if isinstance(data_dir, (str, os.PathLike)) else None)


def selected_connection(server, config_manager=None):
    from ..execution_authorization import current_authorization
    authorization = current_authorization()
    broker = broker_for(config_manager)
    if authorization is not None:
        binding = broker.store.binding(task_identity(authorization), "mcp:" + str(server.get("id", "")), "default")
        if binding:
            return binding["connection_id"]
    reference = server.get("connection_ref")
    selected = reference or broker.store.selection("mcp:" + str(server.get("id", "")), "default")
    return authorization.connection_route("mcp:" + str(server.get("id", "")), selected) if authorization else selected


def _error_from_exception(exc, *, writing=False):
    from ..mcp_client import _exception_leaves
    for leaf in _exception_leaves(exc):
        cause = getattr(leaf, "cause", leaf)
        response = getattr(cause, "response", None)
        status = getattr(response, "status_code", 0)
        if status == 401:
            return ConnectionError("authentication_required", status=401)
        if status == 403:
            return ConnectionError("forbidden", status=403)
    return ConnectionError("outcome_unknown" if writing else "unavailable")


def execute_mcp(server, config_manager=None, skill_manager=None, *, tool_name=None, arguments=None, context=None):
    from ..execution_authorization import current_authorization
    from ..mcp_client import (_run_async, _list_mcp_server_tools_async, _call_mcp_tool_async,
                             _ensure_runtime_skill_dependencies, normalize_mcp_transport, TRANSPORT_STREAMABLE_HTTP)
    broker = broker_for(config_manager)
    capability = "mcp:" + str(server.get("id", ""))
    operation = "discover" if tool_name is None else "call"
    authorization = current_authorization(context)
    task_id = task_identity(authorization) if authorization else "mcp-ui:" + uuid.uuid4().hex
    def cancelled():
        return authorization is not None and authorization._cancelled.is_set()
    try:
        if not server.get("enabled", True):
            raise ConnectionError("revoked")
        if normalize_mcp_transport(server.get("transport")) != TRANSPORT_STREAMABLE_HTTP:
            # Arbitrary subprocesses must not receive host vault credentials.
            raise ConnectionError("unsupported_operation")
        binding = broker.binding(task_id, capability, connection_id=selected_connection(server, config_manager),
                                 operations=[operation])
        effective = copy.deepcopy(server)
        if binding["template"]["adapter"] == "superset":
            effective["url"] = binding["template"]["parameters"].get("mcp_url") or effective.get("url")
        broker.check_target(binding["template"], effective.get("url", ""))
        dependency = _ensure_runtime_skill_dependencies(server, skill_manager=skill_manager)
        if dependency and not dependency.get("ok"):
            raise ConnectionError("dependency_missing")
        def perform(item):
            config = copy.deepcopy(effective)
            config.pop("auth", None)
            config["redact_errors"] = True
            headers = broker.adapters(item["template"]).headers(item["credentials"])
            config["headers"] = {k: v for k, v in config.get("headers", {}).items() if k.lower() not in {"authorization", *[h.lower() for h in headers]}}
            config["headers"].update(headers)
            try:
                if tool_name is None:
                    return _run_async(_list_mcp_server_tools_async(config))
                output = _run_async(_call_mcp_tool_async(config, tool_name, arguments or {}))
            except Exception as exc:
                raise _error_from_exception(exc, writing=tool_name is not None) from None
            output["status"] = "error" if output.get("is_error") else "ok"
            return output
        value = broker.execute(binding, operation, perform, cancelled=cancelled, safe_retry=tool_name is None,
                               wait_for_auth=authorization is not None)
        return {"ok": True, "error": "", "tools": value} if tool_name is None else value
    except ConnectionError as exc:
        if tool_name is None:
            return {**exc.public(), "tools": []}
        return {**exc.public(), "status": "error", "server": server.get("id"), "tool": tool_name}
