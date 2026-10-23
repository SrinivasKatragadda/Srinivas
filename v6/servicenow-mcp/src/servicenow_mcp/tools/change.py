"""
ITSM Change Management Tools.
Provides creation, querying, lifecycle tracking, and approval submission for Change Requests.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_change_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_change_sys_id(number_or_sysid: str) -> str:
        ident = number_or_sysid.strip()
        if len(ident) == 32 and not ident.upper().startswith("CHG"):
            return ident
        records = active_client.query_records(
            table="change_request",
            query=f"number={ident}^ORsys_id={ident}",
            fields="sys_id,number",
            limit=1,
        )
        if not records:
            raise ServiceNowError(f"Change request '{ident}' not found.", code="NOT_FOUND", status_code=404)
        return records[0]["sys_id"]

    @server.tool(
        name="create_change_request",
        description="Create a new ServiceNow change request (normal, standard, emergency) with plans and risk.",
    )
    def create_change_request(
        short_description: str,
        type: str = "normal",
        description: str = "",
        category: str = "Software",
        risk: str = "3",
        impact: str = "3",
        priority: str = "3",
        assignment_group: str = "",
        assigned_to: str = "",
        start_date: str = "",
        end_date: str = "",
        implementation_plan: str = "",
        backout_plan: str = "",
        test_plan: str = "",
        cmdb_ci: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: Brief summary of the change.
            type: Change type: 'normal', 'standard', 'emergency'.
            description: Detailed explanation and justification.
            category: Category (e.g. 'Software', 'Hardware', 'Network').
            risk: Risk level: 1=Very High, 2=High, 3=Moderate, 4=Low.
            impact: 1=High, 2=Medium, 3=Low.
            priority: 1=Critical, 2=High, 3=Moderate, 4=Low.
            assignment_group: Assignment group name or sys_id.
            assigned_to: Assignee username or sys_id.
            start_date: Planned start date (YYYY-MM-DD HH:MM:SS).
            end_date: Planned end date (YYYY-MM-DD HH:MM:SS).
            implementation_plan: Step-by-step rollout plan.
            backout_plan: Contingency rollback plan.
            test_plan: Verification and test procedure.
            cmdb_ci: Affected Configuration Item sys_id.
        """
        payload: Dict[str, Any] = {
            "short_description": short_description,
            "type": type.lower(),
            "description": description,
            "category": category,
            "risk": str(risk),
            "impact": str(impact),
            "priority": str(priority),
        }
        if assignment_group:
            payload["assignment_group"] = assignment_group
        if assigned_to:
            payload["assigned_to"] = assigned_to
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if implementation_plan:
            payload["implementation_plan"] = implementation_plan
        if backout_plan:
            payload["backout_plan"] = backout_plan
        if test_plan:
            payload["test_plan"] = test_plan
        if cmdb_ci:
            payload["cmdb_ci"] = cmdb_ci

        res = active_client.create_record("change_request", payload)
        num = res.get("number", "Unknown")
        return {
            "success": True,
            "number": num,
            "sys_id": res.get("sys_id"),
            "summary": f"Change request {num} created successfully.",
            "record": res,
        }

    @server.tool(
        name="get_change_request",
        description="Retrieve complete details of a change request by number (e.g. CHG0001234) or sys_id.",
    )
    def get_change_request(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Change number or sys_id.
        """
        sys_id = _resolve_change_sys_id(number_or_sysid)
        record = active_client.get_record("change_request", sys_id, display_value="true")
        return {
            "change_request": record,
        }

    @server.tool(
        name="list_change_requests",
        description="List ServiceNow change requests with state, type, risk, or query filters.",
    )
    def list_change_requests(
        state: str = "",
        type: str = "",
        risk: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        query: str = "",
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Args:
            state: Change state (e.g. '-5'=New, '-4'=Assess, '-3'=Authorize, '-2'=Scheduled, '-1'=Implement, '0'=Review, '3'=Closed).
            type: 'normal', 'standard', 'emergency'.
            risk: Risk level code.
            assigned_to: Assignee username.
            assignment_group: Assignment group name.
            query: Custom encoded query string.
            limit: Maximum count to return (default 15).
        """
        parts = []
        if state:
            parts.append(f"state={state}")
        if type:
            parts.append(f"type={type}")
        if risk:
            parts.append(f"risk={risk}")
        if assigned_to:
            parts.append(f"assigned_to.user_name={assigned_to}")
        if assignment_group:
            parts.append(f"assignment_group.name={assignment_group}")
        if query:
            parts.append(query)

        final_query = "^".join(parts) if parts else "active=true"
        fields = "number,short_description,state,type,risk,priority,assigned_to,assignment_group,start_date,end_date"
        changes = active_client.query_records(
            table="change_request",
            query=final_query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(changes),
            "changes": changes,
        }

    @server.tool(
        name="update_change_request",
        description="Update fields on an existing change request.",
    )
    def update_change_request(
        number_or_sysid: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Change number or sys_id.
            fields: Dictionary of fields to update.
        """
        sys_id = _resolve_change_sys_id(number_or_sysid)
        res = active_client.update_record("change_request", sys_id, fields)
        return {
            "success": True,
            "sys_id": sys_id,
            "record": res,
        }

    @server.tool(
        name="submit_change_for_approval",
        description="Move a draft change request to Assess / Authorize state to trigger approvals.",
    )
    def submit_change_for_approval(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Change number or sys_id.
        """
        sys_id = _resolve_change_sys_id(number_or_sysid)
        res = active_client.update_record("change_request", sys_id, {"state": "-4", "approval": "requested"})
        return {
            "success": True,
            "message": f"Change {number_or_sysid} submitted for approval.",
            "record": res,
        }

    @server.tool(
        name="close_change_request",
        description="Close a change request with close code and notes.",
    )
    def close_change_request(
        number_or_sysid: str,
        close_code: str = "successful",
        close_notes: str = "Completed successfully.",
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Change number or sys_id.
            close_code: Close code ('successful', 'unsuccessful', 'successful_issues').
            close_notes: Closure documentation.
        """
        sys_id = _resolve_change_sys_id(number_or_sysid)
        payload = {
            "state": "3",
            "close_code": close_code,
            "close_notes": close_notes,
        }
        res = active_client.update_record("change_request", sys_id, payload)
        return {
            "success": True,
            "message": f"Change request {number_or_sysid} closed.",
            "record": res,
        }
