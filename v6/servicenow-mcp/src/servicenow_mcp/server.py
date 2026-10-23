"""
Main entry point for ServiceNow MCP Server in Python.
Supports Model Context Protocol (MCP) over stdio, sse, and streamable-http transports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional

import anyio
from mcp.server.mcpserver import MCPServer

from servicenow_mcp import __version__
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.config import (
    get_mcp_transport,
    get_mcp_host,
    get_mcp_port,
    get_instance_url,
    get_username,
)
from servicenow_mcp.tools import register_all_tools
from servicenow_mcp.resources import register_resources
from servicenow_mcp.prompts import register_prompts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],  # Send logs to stderr so stdout is reserved for MCP JSON-RPC
)
logger = logging.getLogger("servicenow_mcp")


def create_server(client: Optional[ServiceNowClient] = None) -> MCPServer:
    """Build and configure the ServiceNow MCPServer instance."""
    active_client = client or default_client

    server = MCPServer(
        name="servicenow-mcp",
        version=__version__,
        instructions=(
            "ServiceNow MCP Server: Connects AI models directly to ServiceNow ITSM, CMDB, "
            "Catalog, Knowledge, Scripting, and Operations. Execute read and write operations "
            "with built-in validation and dry-run safety."
        ),
    )

    # Register all tools, resources, and prompts
    register_all_tools(server, active_client)
    register_resources(server, active_client)
    register_prompts(server)

    return server


def print_tool_list(server: MCPServer):
    """Print all registered tools and their descriptions."""
    tools = anyio.run(server.list_tools)
    print(f"\nRegistered ServiceNow MCP Tools ({len(tools)} total):\n" + "=" * 60)
    for i, t in enumerate(tools, 1):
        desc = (t.description or "").split("\n")[0]
        print(f"{i:2d}. {t.name:<30} {desc}")
    print("=" * 60 + "\n")


def print_connection_check(client: ServiceNowClient):
    """Test and display ServiceNow connection information."""
    print(f"\nTesting connection to: {client.instance_url or '(not configured)'}")
    print(f"Configured User:        {client.username or '(not configured)'}")
    res = client.check_connection()
    print("\nResult:")
    print(json.dumps(res, indent=2))
    print()


def main():
    parser = argparse.ArgumentParser(
        description="ServiceNow Model Context Protocol (MCP) Server in pure Python"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=get_mcp_transport(),
        help="MCP transport protocol to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=get_mcp_host(),
        help="Host to bind for HTTP/SSE transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=get_mcp_port(),
        help="Port to bind for HTTP/SSE transport (default: 8002)",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List all registered tools and exit",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Test ServiceNow instance connection and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"servicenow-mcp {__version__}",
    )

    args = parser.parse_args()

    client = default_client
    server = create_server(client)

    if args.list_tools:
        print_tool_list(server)
        sys.exit(0)

    if args.test_connection:
        print_connection_check(client)
        sys.exit(0)

    logger.info("Starting ServiceNow MCP server v%s", __version__)
    logger.info("Instance URL: %s", client.instance_url or "Unset")
    logger.info("User: %s", client.username or "Unset")
    logger.info("Transport: %s", args.transport)

    if args.transport == "stdio":
        logger.info("Listening on stdio (MCP JSON-RPC)")
        server.run(transport="stdio")
    elif args.transport == "sse":
        logger.info("Starting SSE server on %s:%d/sse", args.host, args.port)
        asyncio.run(server.run_sse_async(host=args.host, port=args.port))
    elif args.transport == "streamable-http":
        logger.info("Starting streamable-http server on %s:%d/mcp", args.host, args.port)
        asyncio.run(server.run_streamable_http_async(host=args.host, port=args.port, streamable_http_path="/mcp"))


if __name__ == "__main__":
    main()
