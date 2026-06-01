import asyncio
import json
import os
import re
from contextlib import asynccontextmanager


TRANSPORT_STDIO = "stdio"
TRANSPORT_STREAMABLE_HTTP = "streamable_http"
DEFAULT_MCP_TIMEOUT_SECONDS = 30


def normalize_mcp_transport(value):
    text = str(value or "").strip().lower()
    if text in {"http", "streamable-http", "streamable_http", "streamablehttp"}:
        return TRANSPORT_STREAMABLE_HTTP
    return TRANSPORT_STDIO


def _resolve_timeout_seconds(server_config):
    raw_ms = server_config.get("startup_timeout_ms")
    if raw_ms not in (None, ""):
        try:
            return max(5, int(raw_ms) // 1000 or DEFAULT_MCP_TIMEOUT_SECONDS)
        except Exception:
            pass
    try:
        return max(5, int(server_config.get("timeout_seconds") or DEFAULT_MCP_TIMEOUT_SECONDS))
    except Exception:
        return DEFAULT_MCP_TIMEOUT_SECONDS


def mcp_transport_label(transport):
    return "Streamable HTTP" if normalize_mcp_transport(transport) == TRANSPORT_STREAMABLE_HTTP else "stdio"


def slugify_mcp_identifier(value, fallback="mcp"):
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    return text or fallback


def build_mcp_skill_name(server_id):
    return f"mcp-server-{slugify_mcp_identifier(server_id, fallback='server')}"


def build_mcp_tool_name(server_id, tool_name):
    return f"mcp__{slugify_mcp_identifier(server_id, fallback='server')}__{slugify_mcp_identifier(tool_name, fallback='tool')}"


def mcp_package_available():
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def describe_mcp_import_error(exc):
    missing_name = str(getattr(exc, "name", "") or "").strip()
    if missing_name == "mcp":
        return "Python package 'mcp' is not installed in the current runtime."
    if missing_name.startswith("mcp."):
        return (
            "Python package 'mcp' is incomplete in the current runtime. "
            f"Missing submodule: {missing_name}."
        )
    if missing_name:
        return (
            "Python package 'mcp' or one of its dependencies is unavailable in the current runtime. "
            f"Missing module: {missing_name}."
        )
    return (
        "Python package 'mcp' or one of its dependencies is unavailable in the current runtime. "
        f"Import error: {exc}"
    )


def _stringify_mapping(value):
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key, item in value.items():
        name = str(key or "").strip()
        if not name:
            continue
        normalized[name] = str(item if item is not None else "")
    return normalized


def _stringify_string_list(value):
    if not isinstance(value, list):
        return []
    values = []
    for item in value:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


def _as_plain_data(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_as_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_plain_data(item) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _as_plain_data(model_dump(mode="json"))
        except TypeError:
            return _as_plain_data(model_dump())
    if hasattr(value, "__dict__"):
        return {
            key: _as_plain_data(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _serialize_content_block(block):
    if isinstance(block, dict):
        return _as_plain_data(block)
    block_type = getattr(block, "type", "") or ""
    if block_type == "text":
        return {"type": "text", "text": str(getattr(block, "text", "") or "")}
    if block_type == "image":
        return {
            "type": "image",
            "data": str(getattr(block, "data", "") or ""),
            "mimeType": str(getattr(block, "mimeType", "") or getattr(block, "mime_type", "") or ""),
        }
    if block_type == "resource":
        return {
            "type": "resource",
            "resource": _as_plain_data(getattr(block, "resource", None)),
        }
    plain = _as_plain_data(block)
    return plain if isinstance(plain, dict) else {"type": block_type or "unknown", "value": plain}


def _extract_text_from_content(blocks):
    parts = []
    for block in blocks or []:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block.get("text")))
            continue
        if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
            parts.append(str(getattr(block, "text")))
    return "\n".join(part for part in parts if part).strip()


def _tool_to_payload(tool):
    schema = (
        getattr(tool, "inputSchema", None)
        or getattr(tool, "input_schema", None)
        or getattr(tool, "parameters", None)
        or {}
    )
    if not isinstance(schema, dict):
        schema = _as_plain_data(schema)
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}, "required": []}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return {
        "name": str(getattr(tool, "name", "") or ""),
        "description": str(getattr(tool, "description", "") or "").strip(),
        "input_schema": schema,
        "raw": _as_plain_data(tool),
    }


@asynccontextmanager
async def _open_streamable_http_transport(url, headers, timeout_seconds):
    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        streamable_http_client = None
    from mcp.client.streamable_http import streamablehttp_client

    if streamable_http_client is not None:
        import httpx

        async with httpx.AsyncClient(headers=headers or None, follow_redirects=True, timeout=timeout_seconds) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                yield streams
        return

    async with streamablehttp_client(
        url,
        headers=headers or None,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
    ) as streams:
        yield streams


@asynccontextmanager
async def _open_mcp_session(server_config):
    transport = normalize_mcp_transport(server_config.get("transport", server_config.get("type")))
    timeout_seconds = _resolve_timeout_seconds(server_config)

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError(describe_mcp_import_error(exc)) from exc

    if transport == TRANSPORT_STDIO:
        env = os.environ.copy()
        env.update(_stringify_mapping(server_config.get("env")))
        server_params = StdioServerParameters(
            command=str(server_config.get("command") or "").strip(),
            args=_stringify_string_list(server_config.get("args")),
            cwd=str(server_config.get("cwd") or "").strip() or None,
            env=env,
        )
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
                yield session, timeout_seconds
        return

    url = str(server_config.get("url") or "").strip()
    headers = _stringify_mapping(server_config.get("headers"))
    async with _open_streamable_http_transport(url, headers, timeout_seconds) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
            yield session, timeout_seconds


async def _list_mcp_server_tools_async(server_config):
    async with _open_mcp_session(server_config) as (session, timeout_seconds):
        result = await asyncio.wait_for(session.list_tools(), timeout=timeout_seconds)
    tools = []
    for tool in getattr(result, "tools", None) or []:
        payload = _tool_to_payload(tool)
        if payload["name"]:
            tools.append(payload)
    return tools


async def _call_mcp_tool_async(server_config, tool_name, arguments):
    async with _open_mcp_session(server_config) as (session, timeout_seconds):
        result = await asyncio.wait_for(
            session.call_tool(str(tool_name or "").strip(), arguments=arguments or {}),
            timeout=timeout_seconds,
        )
    content = [_serialize_content_block(item) for item in (getattr(result, "content", None) or [])]
    return {
        "server": str(server_config.get("id") or server_config.get("name") or "").strip(),
        "tool": str(tool_name or "").strip(),
        "is_error": bool(getattr(result, "isError", False) or getattr(result, "is_error", False)),
        "text": _extract_text_from_content(getattr(result, "content", None) or []),
        "content": content,
        "structured_content": _as_plain_data(
            getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        ),
        "meta": _as_plain_data(result),
    }


def _run_async(coro):
    return asyncio.run(coro)


def list_mcp_server_tools(server_config):
    server_name = str(server_config.get("name") or server_config.get("id") or "MCP Server").strip()
    if not bool(server_config.get("enabled", True)):
        return {"ok": False, "error": f"MCP server '{server_name}' is disabled.", "tools": []}
    try:
        tools = _run_async(_list_mcp_server_tools_async(server_config))
        return {"ok": True, "error": "", "tools": tools}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tools": []}


def test_mcp_server_connection(server_config):
    result = list_mcp_server_tools(server_config)
    if not result.get("ok"):
        return result
    tool_names = [item.get("name") for item in result.get("tools") or [] if item.get("name")]
    return {
        "ok": True,
        "error": "",
        "tool_count": len(tool_names),
        "tools": tool_names,
        "message": f"Connected successfully. Found {len(tool_names)} tools.",
    }


def call_mcp_tool(server_config, tool_name, arguments=None):
    server_name = str(server_config.get("name") or server_config.get("id") or "MCP Server").strip()
    if not bool(server_config.get("enabled", True)):
        return {"status": "error", "error": f"MCP server '{server_name}' is disabled."}
    try:
        payload = _run_async(_call_mcp_tool_async(server_config, tool_name, arguments or {}))
    except Exception as exc:
        return {
            "status": "error",
            "server": server_name,
            "tool": str(tool_name or "").strip(),
            "error": str(exc),
        }
    payload["status"] = "error" if payload.get("is_error") else "ok"
    return payload


def summarize_mcp_server(server_config):
    transport = normalize_mcp_transport(server_config.get("transport"))
    if transport == TRANSPORT_STDIO:
        command = str(server_config.get("command") or "").strip()
        args = _stringify_string_list(server_config.get("args"))
        summary = command
        if args:
            summary = f"{summary} {' '.join(args)}".strip()
        return summary or "未配置命令"
    return str(server_config.get("url") or "").strip() or "未配置 URL"
