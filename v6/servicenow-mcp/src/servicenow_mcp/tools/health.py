"""
Health and Diagnostic Tools.
Provides connection checking, latency measurement, and instance metadata.
"""

from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient


def register_health_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="snow_check_connection",
        description="Verify connection and authentication to the configured ServiceNow instance.",
    )
    def snow_check_connection() -> Dict[str, Any]:
        """Tests connectivity and reports latency, instance name, and build."""
        return active_client.check_connection()

    @server.tool(
        name="get_instance_info",
        description="Retrieve instance URL, build version, and configured user details.",
    )
    def get_instance_info() -> Dict[str, Any]:
        """Returns instance URL, current user, and TLS configuration."""
        return {
            "instance_url": active_client.instance_url,
            "username": active_client.username,
            "verify_tls": active_client.verify_tls,
        }
