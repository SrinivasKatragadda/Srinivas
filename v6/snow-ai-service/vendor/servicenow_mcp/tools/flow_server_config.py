"""Compatibility shim for the Flow Designer server URL configuration.

This module keeps the public import path stable while delegating the actual
configuration values to the Flow Designer package configuration. The MCP code
imports from here so older call sites continue to work even if the underlying
config module moves.
"""

from servicenow_mcp.tools.flow_designer.config import (
    DEFAULT_FLOW_SERVER_URL,
    FLOW_SERVER_URL,
    get_flow_server_url,
)

__all__ = [
    "DEFAULT_FLOW_SERVER_URL",
    "FLOW_SERVER_URL",
    "get_flow_server_url",
]
