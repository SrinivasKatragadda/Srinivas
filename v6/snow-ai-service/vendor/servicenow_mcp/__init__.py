"""
ServiceNow MCP Server.

This package exposes the public MCP server entry point used by the workspace.
The implementation focuses on ServiceNow integrations, with most of the code
organized into domain-specific tool modules and shared helper layers.
"""

__version__ = "0.1.0"

from servicenow_mcp.server import ServiceNowMCP

__all__ = ["ServiceNowMCP"]
