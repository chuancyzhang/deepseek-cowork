import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager


TRANSPORT_STDIO = "stdio"
TRANSPORT_STREAMABLE_HTTP = "streamable_http"
DEFAULT_MCP_TIMEOUT_SECONDS = 30
SUPERSET_AUTH_TYPE = "superset_password"
SUPERSET_TOKEN_REFRESH_SKEW_SECONDS = 60


logger = logging.getLogger(__name__)
_SUPERSET_TOKEN_CACHE = {}
_SUPERSET_TOKEN_CACHE_LOCK = threading.RLock()


class McpOperationError(RuntimeError):
    def __init__(self, stage, cause):
        self.stage = str(stage or "mcp")
        self.cause = cause
        super().__init__(str(cause))


def _exception_leaves(exc):
    children = getattr(exc, "exceptions", None)
    if children:
        leaves = []
        for child in children:
            leaves.extend(_exception_leaves(child))
        return leaves
    return [exc]


def _meaningful_exception_text(exc):
    messages = []
    for leaf in _exception_leaves(exc):
        text = str(leaf or "").strip()
        if not text or text.lower() in {"cancelled", "cancelled by cancel scope"}:
            continue
        if text not in messages:
            messages.append(text)
    return "; ".join(messages) or str(exc or "Unknown MCP error").strip()


def describe_mcp_operation_error(server_config, exc):
    stage = exc.stage if isinstance(exc, McpOperationError) else "mcp"
    cause = exc.cause if isinstance(exc, McpOperationError) else exc
    detail = _meaningful_exception_text(cause)
    lowered = detail.lower()
    source_skill = str(server_config.get("source_skill") or "").strip()
    url = str(server_config.get("url") or "").strip()
    if any(marker in lowered for marker in ("connection refused", "all connection attempts failed", "winerror 10061")):
        if source_skill == "superset-mcp":
            return (
                f"无法连接远程 Superset MCP 服务（{url or '未配置 URL'}）。"
                "请确认 Superset 侧已运行 `superset mcp run --host 0.0.0.0 --port 5008`，"
                "并检查端口、防火墙和反向代理。"
            )
        return f"MCP 连接被拒绝（阶段：{stage}）：{detail}"
    if any(marker in lowered for marker in ("ssl", "certificate", "wrong version number", "tls")):
        return f"MCP TLS 连接失败（阶段：{stage}，URL：{url or '未配置'}）：{detail}"
    if "401" in detail or "unauthorized" in lowered:
        if source_skill == "superset-mcp" or str((server_config.get("auth") or {}).get("type") or "") == SUPERSET_AUTH_TYPE:
            return (
                f"Superset MCP 拒绝了认证（阶段：{stage}）：{detail} "
                "请检查 MCP_AUTH_ENABLED、JWT 算法/签名密钥和 MCP_USER_RESOLVER。"
            )
    return f"MCP {stage} 失败：{detail}"


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


def _jwt_expiry(token):
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise ValueError("Superset returned an invalid JWT access token.")
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        expiry = int(decoded.get("exp"))
    except Exception as exc:
        raise ValueError("Superset access token does not contain a valid exp claim.") from exc
    if expiry <= 0:
        raise ValueError("Superset access token contains an invalid exp claim.")
    return expiry


def _auth_field(auth, key, default):
    return str(auth.get(key) or default).strip()


def _superset_auth_values(server_config, config_manager):
    auth = server_config.get("auth") if isinstance(server_config.get("auth"), dict) else {}
    skill_name = str(auth.get("skill_name") or "superset-mcp").strip()
    if not config_manager or not hasattr(config_manager, "get_skill_config"):
        raise ValueError("Superset managed authentication requires the Skill configuration manager.")
    values = config_manager.get_skill_config(skill_name)
    fields = {
        "base_url": _auth_field(auth, "base_url_field", "SUPERSET_BASE_URL"),
        "username": _auth_field(auth, "username_field", "SUPERSET_USERNAME"),
        "password": _auth_field(auth, "password_field", "SUPERSET_PASSWORD"),
        "provider": _auth_field(auth, "provider_field", "SUPERSET_PROVIDER"),
    }
    resolved = {
        name: str(values.get(field_name, "") or "").strip()
        for name, field_name in fields.items()
    }
    resolved["provider"] = resolved["provider"] or "db"
    missing = [name for name in ("base_url", "username", "password") if not resolved[name]]
    if missing:
        raise ValueError("Superset authentication is missing configuration: " + ", ".join(missing))
    if resolved["provider"] not in {"db", "ldap"}:
        raise ValueError("Superset provider must be 'db' or 'ldap'.")
    return resolved


def _superset_cache_key(server_config, values):
    password_fingerprint = hashlib.sha256(values["password"].encode("utf-8")).hexdigest()
    return (
        str(server_config.get("id") or server_config.get("name") or "superset").strip(),
        values["base_url"].rstrip("/"),
        values["username"],
        values["provider"],
        password_fingerprint,
    )


def _superset_response_error(response, action):
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("message") or payload.get("detail") or "").strip()
    except Exception:
        detail = ""
    suffix = f": {detail}" if detail else ""
    return RuntimeError(f"Superset {action} failed ({response.status_code} {response.reason_phrase}){suffix}")


def _superset_login(values, timeout_seconds):
    import httpx

    url = values["base_url"].rstrip("/") + "/api/v1/security/login"
    logger.info("mcp_auth.login.start provider=%s host=%s", values["provider"], values["base_url"])
    try:
        response = httpx.post(
            url,
            json={
                "username": values["username"],
                "password": values["password"],
                "provider": values["provider"],
                "refresh": True,
            },
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )
    except Exception:
        logger.exception("mcp_auth.login.error host=%s", values["base_url"])
        raise
    if response.status_code != 200:
        logger.error("mcp_auth.login.error host=%s status=%s", values["base_url"], response.status_code)
        raise _superset_response_error(response, "login")
    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
    refresh_token = str(payload.get("refresh_token") or "").strip() if isinstance(payload, dict) else ""
    if not access_token or not refresh_token:
        raise ValueError("Superset login response must contain access_token and refresh_token.")
    expiry = _jwt_expiry(access_token)
    logger.info("mcp_auth.login.finish host=%s", values["base_url"])
    return {"access_token": access_token, "refresh_token": refresh_token, "expires_at": expiry}


def _superset_refresh(values, cached, timeout_seconds):
    import httpx

    url = values["base_url"].rstrip("/") + "/api/v1/security/refresh"
    logger.info("mcp_auth.refresh.start host=%s", values["base_url"])
    try:
        response = httpx.post(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + cached["refresh_token"],
            },
            timeout=timeout_seconds,
            follow_redirects=True,
        )
    except Exception:
        logger.exception("mcp_auth.refresh.error host=%s", values["base_url"])
        raise
    if response.status_code == 401:
        logger.warning("mcp_auth.refresh.relogin host=%s", values["base_url"])
        return None
    if response.status_code != 200:
        logger.error("mcp_auth.refresh.error host=%s status=%s", values["base_url"], response.status_code)
        raise _superset_response_error(response, "token refresh")
    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
    if not access_token:
        raise ValueError("Superset refresh response must contain access_token.")
    refreshed = dict(cached)
    refreshed["access_token"] = access_token
    refreshed["expires_at"] = _jwt_expiry(access_token)
    logger.info("mcp_auth.refresh.finish host=%s", values["base_url"])
    return refreshed


def _superset_access_token(server_config, config_manager, force_login=False):
    values = _superset_auth_values(server_config, config_manager)
    cache_key = _superset_cache_key(server_config, values)
    timeout_seconds = _resolve_timeout_seconds(server_config)
    with _SUPERSET_TOKEN_CACHE_LOCK:
        cached = _SUPERSET_TOKEN_CACHE.get(cache_key)
        if force_login or not cached:
            cached = _superset_login(values, timeout_seconds)
        elif int(cached.get("expires_at") or 0) <= int(time.time()) + SUPERSET_TOKEN_REFRESH_SKEW_SECONDS:
            cached = _superset_refresh(values, cached, timeout_seconds)
            if cached is None:
                cached = _superset_login(values, timeout_seconds)
        _SUPERSET_TOKEN_CACHE[cache_key] = cached
        return cached["access_token"]


def prepare_mcp_server_config(server_config, config_manager=None, force_login=False):
    prepared = json.loads(json.dumps(server_config or {}, ensure_ascii=False))
    auth = prepared.get("auth") if isinstance(prepared.get("auth"), dict) else {}
    auth_type = str(auth.get("type") or "").strip().lower()
    if not auth_type:
        return prepared
    if auth_type != SUPERSET_AUTH_TYPE:
        raise ValueError(f"Unsupported MCP managed auth type: {auth_type}")
    token = _superset_access_token(prepared, config_manager, force_login=force_login)
    headers = _stringify_mapping(prepared.get("headers"))
    headers["Authorization"] = "Bearer " + token
    prepared["headers"] = headers
    return prepared


def clear_mcp_auth_cache():
    with _SUPERSET_TOKEN_CACHE_LOCK:
        _SUPERSET_TOKEN_CACHE.clear()


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
        runtime_skill = str(server_config.get("runtime_skill") or "").strip()
        if runtime_skill:
            from .sandbox_runtime import build_sandbox_env

            env = build_sandbox_env(
                workspace_dir=str(server_config.get("cwd") or "").strip() or None,
                skill_id=runtime_skill,
            )
        else:
            env = os.environ.copy()
        env.update(_stringify_mapping(server_config.get("env")))
        server_params = StdioServerParameters(
            command=str(server_config.get("command") or "").strip(),
            args=_stringify_string_list(server_config.get("args")),
            cwd=str(server_config.get("cwd") or "").strip() or None,
            env=env,
        )
        try:
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    try:
                        await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
                    except Exception as exc:
                        raise McpOperationError("initialize", exc) from exc
                    yield session, timeout_seconds
        except McpOperationError:
            raise
        except Exception as exc:
            raise McpOperationError("stdio 进程启动", exc) from exc
        return

    url = str(server_config.get("url") or "").strip()
    headers = _stringify_mapping(server_config.get("headers"))
    try:
        async with _open_streamable_http_transport(url, headers, timeout_seconds) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                try:
                    await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
                except Exception as exc:
                    raise McpOperationError("initialize", exc) from exc
                yield session, timeout_seconds
    except McpOperationError:
        raise
    except Exception as exc:
        raise McpOperationError("transport", exc) from exc


async def _list_mcp_server_tools_async(server_config):
    async with _open_mcp_session(server_config) as (session, timeout_seconds):
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=timeout_seconds)
        except Exception as exc:
            raise McpOperationError("tools/list", exc) from exc
    tools = []
    for tool in getattr(result, "tools", None) or []:
        payload = _tool_to_payload(tool)
        if payload["name"]:
            tools.append(payload)
    return tools


async def _call_mcp_tool_async(server_config, tool_name, arguments):
    async with _open_mcp_session(server_config) as (session, timeout_seconds):
        try:
            result = await asyncio.wait_for(
                session.call_tool(str(tool_name or "").strip(), arguments=arguments or {}),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            raise McpOperationError("tools/call", exc) from exc
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


def _managed_auth_error(server_config, exc):
    return describe_mcp_operation_error(server_config, exc)


def list_mcp_server_tools(server_config, config_manager=None):
    server_name = str(server_config.get("name") or server_config.get("id") or "MCP Server").strip()
    if not bool(server_config.get("enabled", True)):
        return {"ok": False, "error": f"MCP server '{server_name}' is disabled.", "tools": []}
    logger.info(
        "mcp_tools.list.start server=%s transport=%s",
        server_name,
        normalize_mcp_transport(server_config.get("transport")),
    )
    try:
        try:
            prepared = prepare_mcp_server_config(server_config, config_manager=config_manager)
        except Exception as exc:
            raise McpOperationError("认证", exc) from exc
        tools = _run_async(_list_mcp_server_tools_async(prepared))
        logger.info("mcp_tools.list.finish server=%s tool_count=%s", server_name, len(tools))
        return {"ok": True, "error": "", "tools": tools}
    except Exception as exc:
        error = _managed_auth_error(server_config, exc)
        logger.error("mcp_tools.list.error server=%s error=%s", server_name, error)
        return {"ok": False, "error": error, "tools": []}


def test_mcp_server_connection(server_config, config_manager=None):
    result = list_mcp_server_tools(server_config, config_manager=config_manager)
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


def call_mcp_tool(server_config, tool_name, arguments=None, config_manager=None):
    server_name = str(server_config.get("name") or server_config.get("id") or "MCP Server").strip()
    if not bool(server_config.get("enabled", True)):
        return {"status": "error", "error": f"MCP server '{server_name}' is disabled."}
    logger.info("mcp_tool.call.start server=%s tool=%s", server_name, str(tool_name or "").strip())
    try:
        try:
            prepared = prepare_mcp_server_config(server_config, config_manager=config_manager)
        except Exception as exc:
            raise McpOperationError("认证", exc) from exc
        payload = _run_async(_call_mcp_tool_async(prepared, tool_name, arguments or {}))
    except Exception as exc:
        error = _managed_auth_error(server_config, exc)
        logger.error("mcp_tool.call.error server=%s tool=%s error=%s", server_name, str(tool_name or "").strip(), error)
        return {
            "status": "error",
            "server": server_name,
            "tool": str(tool_name or "").strip(),
            "error": error,
        }
    payload["status"] = "error" if payload.get("is_error") else "ok"
    logger.info("mcp_tool.call.finish server=%s tool=%s status=%s", server_name, str(tool_name or "").strip(), payload["status"])
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
