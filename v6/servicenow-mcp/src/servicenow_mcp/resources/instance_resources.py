"""
ServiceNow MCP Resources.
Exposes ServiceNow instance information and schema via standard MCP Resource URIs.
"""

import json
from typing import Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient


def register_resources(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.resource("servicenow://instance/status")
    def instance_status() -> str:
        """Get live connection and health status of the configured ServiceNow instance."""
        conn = active_client.check_connection()
        return json.dumps(conn, indent=2)

    @server.resource("servicenow://tables/core")
    def core_tables() -> str:
        """List commonly used core ITSM, CMDB, and platform tables."""
        tables = [
            {"table": "incident", "label": "Incident", "category": "ITSM"},
            {"table": "problem", "label": "Problem", "category": "ITSM"},
            {"table": "change_request", "label": "Change Request", "category": "ITSM"},
            {"table": "change_task", "label": "Change Task", "category": "ITSM"},
            {"table": "sc_req_item", "label": "Requested Item", "category": "Service Catalog"},
            {"table": "sc_task", "label": "Catalog Task", "category": "Service Catalog"},
            {"table": "cmdb_ci", "label": "Configuration Item", "category": "CMDB"},
            {"table": "cmdb_ci_server", "label": "Server", "category": "CMDB"},
            {"table": "cmdb_ci_appl", "label": "Application", "category": "CMDB"},
            {"table": "kb_knowledge", "label": "Knowledge Article", "category": "Knowledge"},
            {"table": "sys_user", "label": "User", "category": "Administration"},
            {"table": "sys_user_group", "label": "Group", "category": "Administration"},
            {"table": "sys_update_set", "label": "Update Set", "category": "Development"},
            {"table": "sys_script", "label": "Business Rule", "category": "Scripting"},
            {"table": "sys_script_include", "label": "Script Include", "category": "Scripting"},
        ]
        return json.dumps(tables, indent=2)
