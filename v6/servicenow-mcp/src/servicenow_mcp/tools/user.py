"""
User, Group, and Role Administration Tools.
Provides lookup, group membership management, and role inspection in ServiceNow.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_user_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_user_sys_id(user_identifier: str) -> str:
        ident = user_identifier.strip()
        if len(ident) == 32 and not ident.isdigit():
            return ident
        rows = active_client.query_records(
            table="sys_user",
            query=f"user_name={ident}^ORemail={ident}^ORsys_id={ident}",
            fields="sys_id,user_name,email",
            limit=1,
        )
        if not rows:
            raise ServiceNowError(f"User '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        return rows[0]["sys_id"]

    def _resolve_group_sys_id(group_identifier: str) -> str:
        ident = group_identifier.strip()
        if len(ident) == 32:
            return ident
        rows = active_client.query_records(
            table="sys_user_group",
            query=f"name={ident}^ORsys_id={ident}",
            fields="sys_id,name",
            limit=1,
        )
        if not rows:
            raise ServiceNowError(f"Group '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        return rows[0]["sys_id"]

    @server.tool(
        name="get_user",
        description="Look up complete ServiceNow user details by username, email address, or sys_id.",
    )
    def get_user(user_identifier: str) -> Dict[str, Any]:
        """
        Args:
            user_identifier: Username (user_name), email address, or sys_id.
        """
        sys_id = _resolve_user_sys_id(user_identifier)
        user = active_client.get_record("sys_user", sys_id, display_value="true")
        return {"user": user}

    @server.tool(
        name="list_users",
        description="List ServiceNow users with active status, department, or text filters.",
    )
    def list_users(
        department: str = "",
        title: str = "",
        search: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            department: Department name.
            title: Job title.
            search: Name or email search string.
            active_only: Only return active users (default True).
            limit: Maximum count to return (default 20).
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if department:
            parts.append(f"department.nameLIKE{department}")
        if title:
            parts.append(f"titleLIKE{title}")
        if search:
            parts.append(f"nameLIKE{search}^ORuser_nameLIKE{search}^ORemailLIKE{search}")

        query = "^".join(parts) if parts else "active=true"
        fields = "sys_id,user_name,name,email,title,department,company,active"
        users = active_client.query_records(
            table="sys_user",
            query=query,
            fields=fields,
            limit=limit,
            order_by="name",
            display_value="true",
        )
        return {
            "count": len(users),
            "users": users,
        }

    @server.tool(
        name="get_group",
        description="Find an assignment group's details by group name or sys_id from sys_user_group.",
    )
    def get_group(group_identifier: str) -> Dict[str, Any]:
        """
        Args:
            group_identifier: Group name or 32-character sys_id.
        """
        sys_id = _resolve_group_sys_id(group_identifier)
        group = active_client.get_record("sys_user_group", sys_id, display_value="true")
        return {"group": group}

    @server.tool(
        name="list_group_members",
        description="List all members belonging to an assignment group via sys_user_grmember.",
    )
    def list_group_members(group_identifier: str) -> Dict[str, Any]:
        """
        Args:
            group_identifier: Group name or sys_id.
        """
        group_id = _resolve_group_sys_id(group_identifier)
        members = active_client.query_records(
            table="sys_user_grmember",
            query=f"group={group_id}",
            fields="sys_id,user,user.user_name,user.email,user.name",
            display_value="true",
        )
        return {
            "group_sys_id": group_id,
            "member_count": len(members),
            "members": members,
        }

    @server.tool(
        name="add_user_to_group",
        description="Add a user to a ServiceNow assignment group.",
    )
    def add_user_to_group(
        user_identifier: str,
        group_identifier: str,
    ) -> Dict[str, Any]:
        """
        Args:
            user_identifier: Username, email, or sys_id of the user.
            group_identifier: Name or sys_id of the group.
        """
        user_id = _resolve_user_sys_id(user_identifier)
        group_id = _resolve_group_sys_id(group_identifier)

        # Check if already a member
        existing = active_client.query_records(
            table="sys_user_grmember",
            query=f"group={group_id}^user={user_id}",
            limit=1,
        )
        if existing:
            return {"already_member": True, "sys_id": existing[0]["sys_id"]}

        res = active_client.create_record("sys_user_grmember", {"user": user_id, "group": group_id})
        return {
            "success": True,
            "sys_id": res.get("sys_id"),
            "message": f"User {user_identifier} added to group {group_identifier}.",
        }

    @server.tool(
        name="remove_user_from_group",
        description="Remove a user from an assignment group.",
    )
    def remove_user_from_group(
        user_identifier: str,
        group_identifier: str,
    ) -> Dict[str, Any]:
        """
        Args:
            user_identifier: Username or sys_id.
            group_identifier: Group name or sys_id.
        """
        user_id = _resolve_user_sys_id(user_identifier)
        group_id = _resolve_group_sys_id(group_identifier)

        rows = active_client.query_records(
            table="sys_user_grmember",
            query=f"group={group_id}^user={user_id}",
            limit=1,
        )
        if not rows:
            return {"success": False, "message": "User was not in this group."}

        active_client.delete_record("sys_user_grmember", rows[0]["sys_id"])
        return {
            "success": True,
            "message": f"User {user_identifier} removed from group {group_identifier}.",
        }

    @server.tool(
        name="check_user_roles",
        description="List all roles granted to a user (direct and group-inherited) from sys_user_has_role.",
    )
    def check_user_roles(user_identifier: str) -> Dict[str, Any]:
        """
        Args:
            user_identifier: Username, email, or sys_id.
        """
        user_id = _resolve_user_sys_id(user_identifier)
        roles = active_client.query_records(
            table="sys_user_has_role",
            query=f"user={user_id}^state=active",
            fields="sys_id,role.name,granted_by,inherited",
            display_value="true",
        )
        role_names = [r.get("role.name") or r.get("role", {}).get("display_value", "") for r in roles]
        return {
            "user_sys_id": user_id,
            "role_count": len(roles),
            "role_names": sorted(list(set(role_names))),
            "details": roles,
        }
