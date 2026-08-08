import asyncio
import sys


def _validate_mcp_sdk():
    """Fail before server startup when the WeKnora v1 server API is unavailable."""
    try:
        from mcp.server import Server
    except Exception as exc:
        raise RuntimeError(
            "WeKnora MCP requires the MCP Python SDK v1; importing mcp.server failed: " + str(exc)
        ) from exc
    if not callable(getattr(Server, "list_tools", None)):
        raise RuntimeError(
            "WeKnora MCP requires the MCP Python SDK v1 (<2.0.0); "
            "the current Server object has no list_tools method."
        )


if __name__ == "__main__":
    try:
        _validate_mcp_sdk()
        from weknora_mcp_server import run
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    asyncio.run(run())
