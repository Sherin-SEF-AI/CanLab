"""CanLab as an MCP server, so an assistant can drive the analysis.

Three ways to connect, one tool set (``canlab.core.mcp_tools``):

    canlab-mcp                       stdio, headless: Claude Desktop and
                                     Claude Code start it themselves and it
                                     loads captures on request
    canlab-mcp --http --port 8766    Streamable HTTP, headless: for ChatGPT
                                     connectors, Claude.ai connectors and
                                     Claude Code over http
    canlab-mcp --attach URL          stdio in, HTTP out: bridges a client that
                                     only speaks stdio (Claude Desktop) to the
                                     server running inside the CanLab window,
                                     so the assistant works on the live capture

The window runs its own HTTP server (Settings > MCP) over the loaded or live
frames; this file is the headless side and the bridge. Nothing here
transmits on a bus.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MCP_PATH = "/mcp"

INSTRUCTIONS = (
    "CanLab reverse-engineers CAN bus captures. Start with status; if nothing "
    "is loaded, load_log a capture. list_ids shows what is on the bus; the "
    "detect_* tools find counters, checksums, flags, value tables, field "
    "boundaries and multiplexers; add_dbc_signal defines what you conclude and "
    "export_dbc writes it out. Annotate moments (add_annotation) and "
    "rank_annotations to find the byte that tracked them. No tool transmits."
)


def build_server(tools=None, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 allow_remote: bool = False):
    """A FastMCP server carrying the CanLab tools."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    from canlab.core.mcp_tools import CanLabTools
    if tools is None:
        tools = CanLabTools()
    if allow_remote:
        # Reached through a tunnel, the Host header is the tunnel's name;
        # the DNS-rebinding check would refuse every request.
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"])
    mcp = FastMCP("canlab", instructions=INSTRUCTIONS, host=host, port=port,
                  streamable_http_path=MCP_PATH, transport_security=security,
                  log_level="WARNING")
    tools.register(mcp)
    return mcp


def http_app(mcp, token: str = ""):
    """The Streamable HTTP ASGI app, with an optional bearer token in front.

    Clients that can send headers (Claude Code, the --attach bridge, scripts)
    present ``Authorization: Bearer <token>``. Leave the token empty for
    clients that cannot, and keep the server on loopback.
    """
    app = mcp.streamable_http_app()
    if not token:
        return app
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(_Bearer)
    return app


# ── the stdio to HTTP bridge ────────────────────────────────────────────────

async def _bridge(url: str, token: str = "") -> None:
    """Serve stdio, forward every tool call to the HTTP server at url."""
    from mcp import types
    from mcp.client.session import ClientSession
    from mcp.shared._httpx_utils import create_mcp_http_client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                server = Server("canlab", instructions=init.instructions or INSTRUCTIONS)

                @server.list_tools()
                async def _list_tools() -> list[types.Tool]:
                    return (await session.list_tools()).tools

                @server.call_tool(validate_input=False)
                async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
                    return await session.call_tool(name, arguments)

                async with stdio_server() as (r, w):
                    await server.run(r, w, server.create_initialization_options())


# ── entry ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canlab-mcp",
                                description="CanLab tools over MCP, without the window.")
    p.add_argument("--http", action="store_true",
                   help="serve Streamable HTTP instead of stdio")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--token", default="",
                   help="bearer token to require (--http) or to send (--attach)")
    p.add_argument("--allow-remote", action="store_true",
                   help="accept any Host header, for use behind a tunnel")
    p.add_argument("--attach", metavar="URL",
                   help="bridge stdio to the MCP server at URL, e.g. the one in "
                        f"the CanLab window: http://127.0.0.1:{DEFAULT_PORT}{MCP_PATH}")
    p.add_argument("--load", metavar="CAPTURE",
                   help="load this capture before serving")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.attach:
        try:
            asyncio.run(_bridge(args.attach, args.token))
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"canlab-mcp: cannot reach {args.attach}: {e}", file=sys.stderr)
            return 1
        return 0

    from canlab.core.mcp_tools import CanLabTools
    tools = CanLabTools()
    if args.load:
        tools.load_log(args.load)
    mcp = build_server(tools, host=args.host, port=args.port,
                       allow_remote=args.allow_remote)
    if args.http:
        import uvicorn
        uvicorn.run(http_app(mcp, args.token), host=args.host, port=args.port,
                    log_level="warning")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
