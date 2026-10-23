"""
Authentication module for the ServiceNow MCP server.

This package re-exports the authentication manager used by the server and tool
layers. Keeping the public auth surface small makes it easier for the rest of
the codebase to share a single credential-handling implementation.
"""

from servicenow_mcp.auth.auth_manager import AuthManager

__all__ = ["AuthManager"]
