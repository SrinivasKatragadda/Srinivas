"""
Update Set and Scoped Application Tools.
Allows tracking customizations, creating development update sets, and managing application scopes.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_updateset_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="list_update_sets",
        description="List update sets from sys_update_set with state and application scope.",
    )
    def list_update_sets(
        state: str = "in progress",
        application: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            state: 'in progress', 'complete', or empty for all.
            application: Application scope name or sys_id.
            limit: Maximum count to return.
        """
        parts = []
        if state:
            parts.append(f"state={state}")
        if application:
            parts.append(f"application.nameLIKE{application}^ORapplication={application}")

        query = "^".join(parts) if parts else ""
        fields = "sys_id,name,state,application,application.name,sys_created_by,sys_updated_on"
        update_sets = active_client.query_records(
            table="sys_update_set",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_updated_on",
            display_value="true",
        )
        return {"count": len(update_sets), "update_sets": update_sets}

    @server.tool(
        name="create_update_set",
        description="Create a new in-progress Update Set in ServiceNow.",
    )
    def create_update_set(
        name: str,
        description: str = "",
        application_scope: str = "global",
    ) -> Dict[str, Any]:
        """
        Args:
            name: Meaningful name for the update set.
            description: Description of the changes.
            application_scope: Application scope sys_id or 'global'.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "state": "in progress",
            "application": application_scope,
        }
        res = active_client.create_record("sys_update_set", payload)
        return {
            "success": True,
            "name": name,
            "sys_id": res.get("sys_id"),
            "record": res,
        }

    @server.tool(
        name="complete_update_set",
        description="Mark an update set as Complete (ready for export or retrieval).",
    )
    def complete_update_set(update_set_sys_id: str) -> Dict[str, Any]:
        """
        Args:
            update_set_sys_id: System ID of the update set.
        """
        res = active_client.update_record("sys_update_set", update_set_sys_id, {"state": "complete"})
        return {
            "success": True,
            "message": "Update set marked as complete.",
            "record": res,
        }

    @server.tool(
        name="list_custom_applications",
        description="List custom / scoped applications in ServiceNow (sys_app and sys_scope).",
    )
    def list_custom_applications(limit: int = 25) -> Dict[str, Any]:
        """
        Args:
            limit: Maximum applications to return.
        """
        fields = "sys_id,name,scope,version,active,vendor"
        apps = active_client.query_records(
            table="sys_app",
            query="active=true",
            fields=fields,
            limit=limit,
            order_by="name",
            display_value="true",
        )
        return {"count": len(apps), "applications": apps}
