"""
ServiceNow System Properties Tools.
Allows querying and safely updating sys_properties.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_properties_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="get_system_property",
        description="Retrieve the value and description of a ServiceNow system property by exact name.",
    )
    def get_system_property(name: str) -> Dict[str, Any]:
        """
        Args:
            name: Exact property name (e.g. 'glide.servlet.uri', 'instance_name').
        """
        rows = active_client.query_records(
            table="sys_properties",
            query=f"name={name}",
            fields="sys_id,name,value,description,type",
            limit=1,
        )
        if not rows:
            raise ServiceNowError(f"System property '{name}' not found.", code="NOT_FOUND", status_code=404)
        return {"property": rows[0]}

    @server.tool(
        name="list_system_properties",
        description="Search system properties by prefix or description.",
    )
    def list_system_properties(
        search: str,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            search: Search term in name or description (e.g. 'glide.authenticate', 'rest').
            limit: Maximum count to return.
        """
        rows = active_client.query_records(
            table="sys_properties",
            query=f"nameLIKE{search}^ORdescriptionLIKE{search}",
            fields="sys_id,name,value,description",
            limit=limit,
        )
        return {"count": len(rows), "properties": rows}

    @server.tool(
        name="set_system_property",
        description="Set or update a system property in sys_properties.",
    )
    def set_system_property(
        name: str,
        value: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            name: Property name.
            value: Property value.
            description: Optional updated description.
        """
        rows = active_client.query_records(
            table="sys_properties",
            query=f"name={name}",
            fields="sys_id",
            limit=1,
        )
        if rows:
            sys_id = rows[0]["sys_id"]
            payload = {"value": value}
            if description:
                payload["description"] = description
            res = active_client.update_record("sys_properties", sys_id, payload)
            action = "updated"
        else:
            payload = {"name": name, "value": value}
            if description:
                payload["description"] = description
            res = active_client.create_record("sys_properties", payload)
            action = "created"

        return {
            "success": True,
            "action": action,
            "name": name,
            "value": value,
            "record": res,
        }
