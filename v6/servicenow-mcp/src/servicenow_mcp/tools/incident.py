"""
ITSM Incident Management Tools.
Provides full incident lifecycle management: create, query, update, resolve, close,
and work notes / comments.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_incident_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_incident_sys_id(number_or_sysid: str) -> str:
        ident = number_or_sysid.strip()
        if len(ident) == 32 and not ident.upper().startswith("INC"):
            return ident
        records = active_client.query_records(
            table="incident",
            query=f"number={ident}^ORsys_id={ident}",
            fields="sys_id,number",
            limit=1,
        )
        if not records:
            raise ServiceNowError(f"Incident '{ident}' was not found.", code="NOT_FOUND", status_code=404)
        return records[0]["sys_id"]

    def _resolve_ci_ref(value: str, table: str, label: str) -> str:
        """CI / service name or sys_id -> sys_id (exact name; refuses unknown or ambiguous names)."""
        ident = value.strip()
        is_id = len(ident) == 32 and all(c in "0123456789abcdef" for c in ident)
        rows = active_client.query_records(table, query=f"sys_id={ident}" if is_id else f"name={ident}",
                                           fields="sys_id,name", limit=3)
        if not rows:
            raise ServiceNowError(f"No {label} matches '{ident}'", code="NOT_FOUND", status_code=404)
        if len(rows) > 1:
            raise ServiceNowError(f"'{ident}' matches several {label} records — pass the sys_id", code="AMBIGUOUS",
                                  detail=", ".join(r["sys_id"] for r in rows))
        return rows[0]["sys_id"]

    @server.tool(
        name="create_incident",
        description=(
            "Create a new ServiceNow incident ticket with short description, caller, urgency, impact, and category. "
            "Optionally link the affected CI (cmdb_ci) and service (business_service); with a CI, an active incident "
            "with the same short description on that CI is reported instead of creating a duplicate."
        ),
    )
    def create_incident(
        short_description: str,
        description: str = "",
        caller_id: str = "",
        urgency: int = 3,
        impact: int = 3,
        priority: Optional[int] = None,
        category: str = "inquiry",
        subcategory: str = "",
        assignment_group: str = "",
        assigned_to: str = "",
        cmdb_ci: str = "",
        business_service: str = "",
        allow_duplicate: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: Brief summary of the incident (required).
            description: Detailed description of the issue.
            caller_id: Username or sys_id of the reporting user.
            urgency: 1 (High), 2 (Medium), 3 (Low).
            impact: 1 (High), 2 (Medium), 3 (Low).
            priority: Calculated automatically by ServiceNow or overridden (1-5).
            category: Incident category (e.g. 'hardware', 'software', 'network', 'inquiry').
            subcategory: Subcategory if applicable.
            assignment_group: Assignment group name or sys_id.
            assigned_to: Assignee username or sys_id.
            cmdb_ci: Affected configuration item, name or sys_id (validated in cmdb_ci).
            business_service: Affected service, name or sys_id (validated in cmdb_ci_service).
            allow_duplicate: Create even if an active incident with the same short description exists on the CI.
        """
        payload: Dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "urgency": str(urgency),
            "impact": str(impact),
            "category": category,
        }
        if caller_id:
            payload["caller_id"] = caller_id
        if priority is not None:
            payload["priority"] = str(priority)
        if subcategory:
            payload["subcategory"] = subcategory
        if assignment_group:
            payload["assignment_group"] = assignment_group
        if assigned_to:
            payload["assigned_to"] = assigned_to
        if cmdb_ci:
            payload["cmdb_ci"] = _resolve_ci_ref(cmdb_ci, "cmdb_ci", "CI")
        if business_service:
            payload["business_service"] = _resolve_ci_ref(business_service, "cmdb_ci_service", "service")
        if payload.get("cmdb_ci") and not allow_duplicate:
            dup = active_client.query_records(
                "incident",
                query=f"active=true^cmdb_ci={payload['cmdb_ci']}"
                      f"^short_description={short_description.replace('^', '^^')}",
                fields="sys_id,number,state",
                limit=1,
            )
            if dup:
                return {
                    "success": False,
                    "duplicate": True,
                    "existing_number": dup[0].get("number"),
                    "existing_sys_id": dup[0].get("sys_id"),
                    "message": f"Active incident {dup[0].get('number')} already covers this CI with the same short "
                               "description. Update it, or call again with allow_duplicate=true.",
                }

        res = active_client.create_record("incident", payload)
        num = res.get("number", "Unknown")
        sys_id = res.get("sys_id", "")
        return {
            "success": True,
            "number": num,
            "sys_id": sys_id,
            "summary": f"Incident {num} created successfully.",
            "record": res,
        }

    @server.tool(
        name="get_incident",
        description="Retrieve complete incident details by incident number (e.g. INC0001234) or sys_id.",
    )
    def get_incident(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Incident number (INC...) or 32-character sys_id.
        """
        sys_id = _resolve_incident_sys_id(number_or_sysid)
        record = active_client.get_record("incident", sys_id, display_value="true")
        return {
            "incident": record,
        }

    @server.tool(
        name="list_incidents",
        description="List ServiceNow incidents with state, assignment, urgency, or text filters.",
    )
    def list_incidents(
        state: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        category: str = "",
        search: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            state: State code: 1=New, 2=In Progress, 3=On Hold, 6=Resolved, 7=Closed, 8=Canceled.
            assigned_to: Assignee username.
            assignment_group: Group name.
            category: Category name.
            search: Search term inside short_description or description.
            active_only: Only return active incidents (default True).
            limit: Maximum count to return (default 20).
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if state:
            parts.append(f"state={state}")
        if assigned_to:
            parts.append(f"assigned_to.user_name={assigned_to}")
        if assignment_group:
            parts.append(f"assignment_group.name={assignment_group}")
        if category:
            parts.append(f"category={category}")
        if search:
            parts.append(f"short_descriptionLIKE{search}^ORdescriptionLIKE{search}")

        query = "^".join(parts) if parts else "active=true"
        fields = "number,short_description,state,priority,urgency,impact,assigned_to,assignment_group,caller_id,sys_created_on"
        incidents = active_client.query_records(
            table="incident",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(incidents),
            "incidents": incidents,
        }

    @server.tool(
        name="update_incident",
        description="Update fields on an existing incident by number or sys_id.",
    )
    def update_incident(
        number_or_sysid: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Incident number (INC...) or sys_id.
            fields: Dictionary of fields to update (e.g. {'state': '2', 'urgency': '1'}).
        """
        sys_id = _resolve_incident_sys_id(number_or_sysid)
        res = active_client.update_record("incident", sys_id, fields)
        return {
            "success": True,
            "sys_id": sys_id,
            "record": res,
        }

    @server.tool(
        name="resolve_incident",
        description="Resolve an incident with close code and resolution notes (sets state to 6 - Resolved).",
    )
    def resolve_incident(
        number_or_sysid: str,
        resolution_code: str = "Solved (Permanently)",
        resolution_notes: str = "Resolved by agent via MCP integration.",
        close_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Incident number or sys_id.
            resolution_code: Close code (e.g. 'Solved (Permanently)', 'Solved (Work Around)').
            resolution_notes: Notes describing the resolution.
            close_notes: Optional alternative field for resolution notes.
        """
        sys_id = _resolve_incident_sys_id(number_or_sysid)
        notes = close_notes or resolution_notes
        payload = {
            "state": "6",
            "close_code": resolution_code,
            "close_notes": notes,
        }
        res = active_client.update_record("incident", sys_id, payload)
        return {
            "success": True,
            "message": f"Incident {number_or_sysid} resolved.",
            "record": res,
        }

    @server.tool(
        name="close_incident",
        description="Close a resolved incident (sets state to 7 - Closed).",
    )
    def close_incident(
        number_or_sysid: str,
        close_notes: str = "Closed via MCP integration.",
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Incident number or sys_id.
            close_notes: Closing remarks.
        """
        sys_id = _resolve_incident_sys_id(number_or_sysid)
        payload = {
            "state": "7",
            "close_notes": close_notes,
        }
        res = active_client.update_record("incident", sys_id, payload)
        return {
            "success": True,
            "message": f"Incident {number_or_sysid} closed.",
            "record": res,
        }

    @server.tool(
        name="add_work_note",
        description="Add an internal work note (not visible to end users) to any ServiceNow task/incident.",
    )
    def add_work_note(
        table: str,
        sys_id: str,
        note: str,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident', 'change_request').
            sys_id: Record sys_id.
            note: Work note text to append.
        """
        res = active_client.update_record(table, sys_id, {"work_notes": note})
        return {
            "success": True,
            "message": "Work note added.",
            "record": res,
        }

    @server.tool(
        name="add_comment",
        description="Add a customer-visible comment to any ServiceNow ticket.",
    )
    def add_comment(
        table: str,
        sys_id: str,
        comment: str,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident').
            sys_id: Record sys_id.
            comment: Customer-visible comment text.
        """
        res = active_client.update_record(table, sys_id, {"comments": comment})
        return {
            "success": True,
            "message": "Comment added.",
            "record": res,
        }
