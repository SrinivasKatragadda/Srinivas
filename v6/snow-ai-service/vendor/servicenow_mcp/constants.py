"""
Constants for the ServiceNow MCP server.

These are non-secret configuration defaults that control server behavior.
They provide a single source of truth for the MCP transport, host, port, and
default tool-package selection.
"""

MCP_HOST = "0.0.0.0"
MCP_PORT = 8000
MCP_PATH = "/mcp"
MCP_TRANSPORT = "streamable-http"
MCP_LOG_LEVEL = "INFO"
MCP_TOOL_PACKAGE = "full"
