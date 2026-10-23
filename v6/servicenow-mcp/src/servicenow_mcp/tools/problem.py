"""
ITSM Problem Management Tools.
Provides problem investigation, root cause tracking, workarounds, and closure.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_problem_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    def _resolve_problem_sys_id(number_or_sysid: str) -> str:
        ident = number_or_sysid.strip()
        if len(ident) == 32 and not ident.upper().startswith("PRB"):
            return ident
        records = active_client.query_records(
            table="problem",
            query=f"number={ident}^ORsys_id={ident}",
            fields="sys_id,number",
            limit=1,
        )
        if not records:
            raise ServiceNowError(f"Problem '{ident}' not found.", code="NOT_FOUND", status_code=404)
        return records[0]["sys_id"]

    @server.tool(
        name="create_problem",
        description="Create a new ServiceNow Problem record to investigate root cause.",
    )
    def create_problem(
        short_description: str,
        description: str = "",
        urgency: int = 3,
        impact: int = 3,
        priority: Optional[int] = None,
        cmdb_ci: str = "",
        assignment_group: str = "",
        assigned_to: str = "",
        workaround: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: Summary of the problem.
            description: Detailed symptoms and scope.
            urgency: 1 (High), 2 (Medium), 3 (Low).
            impact: 1 (High), 2 (Medium), 3 (Low).
            priority: Calculated priority (1-5).
            cmdb_ci: Impacted Configuration Item sys_id.
            assignment_group: Assignment group name or sys_id.
            assigned_to: Assignee username or sys_id.
            workaround: Known temporary workaround.
        """
        payload: Dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "urgency": str(urgency),
            "impact": str(impact),
        }
        if priority is not None:
            payload["priority"] = str(priority)
        if cmdb_ci:
            payload["cmdb_ci"] = cmdb_ci
        if assignment_group:
            payload["assignment_group"] = assignment_group
        if assigned_to:
            payload["assigned_to"] = assigned_to
        if workaround:
            payload["workaround"] = workaround

        res = active_client.create_record("problem", payload)
        num = res.get("number", "Unknown")
        return {
            "success": True,
            "number": num,
            "sys_id": res.get("sys_id"),
            "summary": f"Problem {num} created successfully.",
            "record": res,
        }

    @server.tool(
        name="get_problem",
        description="Retrieve complete details of a problem by number (e.g. PRB0001234) or sys_id.",
    )
    def get_problem(number_or_sysid: str) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Problem number or sys_id.
        """
        sys_id = _resolve_problem_sys_id(number_or_sysid)
        record = active_client.get_record("problem", sys_id, display_value="true")
        return {
            "problem": record,
        }

    @server.tool(
        name="list_problems",
        description="List ServiceNow problems with state, priority, or query filters.",
    )
    def list_problems(
        state: str = "",
        priority: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        active_only: bool = True,
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Args:
            state: Problem state (e.g. 101=New, 102=Assess, 103=Root Cause Analysis, 104=Fix in Progress, 106=Resolved, 107=Closed).
            priority: Priority code.
            assigned_to: Assignee username.
            assignment_group: Assignment group name.
            active_only: Only active problems (default True).
            limit: Maximum count to return (default 15).
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if state:
            parts.append(f"state={state}")
        if priority:
            parts.append(f"priority={priority}")
        if assigned_to:
            parts.append(f"assigned_to.user_name={assigned_to}")
        if assignment_group:
            parts.append(f"assignment_group.name={assignment_group}")

        query = "^".join(parts) if parts else "active=true"
        fields = "number,short_description,state,priority,assigned_to,assignment_group,root_cause,workaround"
        problems = active_client.query_records(
            table="problem",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(problems),
            "problems": problems,
        }

    @server.tool(
        name="update_problem",
        description="Update fields on an existing problem (e.g. root_cause, workaround, state).",
    )
    def update_problem(
        number_or_sysid: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Problem number or sys_id.
            fields: Dictionary of fields to update.
        """
        sys_id = _resolve_problem_sys_id(number_or_sysid)
        res = active_client.update_record("problem", sys_id, fields)
        return {
            "success": True,
            "sys_id": sys_id,
            "record": res,
        }

    @server.tool(
        name="resolve_problem",
        description="Resolve a problem with root cause analysis and resolution notes.",
    )
    def resolve_problem(
        number_or_sysid: str,
        root_cause: str,
        resolution_code: str = "Fix Applied",
        resolution_notes: str = "Permanent fix identified and applied.",
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Problem number or sys_id.
            root_cause: Explanation of the identified root cause.
            resolution_code: Resolution code ('Fix Applied', 'Risk Accepted', 'Canceled').
            resolution_notes: Details of how the problem was resolved.
        """
        sys_id = _resolve_problem_sys_id(number_or_sysid)
        payload = {
            "state": "106",
            "root_cause": root_cause,
            "resolution_code": resolution_code,
            "close_notes": resolution_notes,
        }
        res = active_client.update_record("problem", sys_id, payload)
        return {
            "success": True,
            "message": f"Problem {number_or_sysid} resolved.",
            "record": res,
        }

    @server.tool(
        name="close_problem",
        description="Close a resolved problem record.",
    )
    def close_problem(
        number_or_sysid: str,
        close_notes: str = "Closed after verification.",
    ) -> Dict[str, Any]:
        """
        Args:
            number_or_sysid: Problem number or sys_id.
            close_notes: Final closure notes.
        """
        sys_id = _resolve_problem_sys_id(number_or_sysid)
        payload = {
            "state": "107",
            "close_notes": close_notes,
        }
        res = active_client.update_record("problem", sys_id, payload)
        return {
            "success": True,
            "message": f"Problem {number_or_sysid} closed.",
            "record": res,
        }
