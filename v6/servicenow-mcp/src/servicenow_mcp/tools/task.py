"""
Task and Approval Management Tools.
Provides queries across the generic 'task' hierarchy and approval workflow actions.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


def register_task_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="list_tasks",
        description="List tasks across the ServiceNow task table (incidents, problems, changes, catalog tasks).",
    )
    def list_tasks(
        sys_class_name: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        state: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            sys_class_name: Filter by class (e.g. 'sc_task', 'change_task', 'incident').
            assigned_to: Filter by assigned username.
            assignment_group: Filter by assignment group.
            state: Filter by state.
            active_only: Only return active tasks (default True).
            limit: Maximum tasks to return.
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if sys_class_name:
            parts.append(f"sys_class_name={sys_class_name}")
        if assigned_to:
            parts.append(f"assigned_to.user_name={assigned_to}")
        if assignment_group:
            parts.append(f"assignment_group.name={assignment_group}")
        if state:
            parts.append(f"state={state}")

        query = "^".join(parts) if parts else "active=true"
        fields = "sys_id,number,sys_class_name,short_description,state,priority,assigned_to,assignment_group,due_date"
        tasks = active_client.query_records(
            table="task",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(tasks),
            "tasks": tasks,
        }

    @server.tool(
        name="get_task",
        description="Retrieve details of any task record by number or sys_id.",
    )
    def get_task(task_identifier: str) -> Dict[str, Any]:
        """
        Args:
            task_identifier: Task number (e.g. TASK001001, INC00123) or sys_id.
        """
        ident = task_identifier.strip()
        if len(ident) == 32:
            rec = active_client.get_record("task", ident, display_value="true")
        else:
            rows = active_client.query_records("task", query=f"number={ident}", limit=1, display_value="true")
            if not rows:
                raise ServiceNowError(f"Task '{ident}' not found.", code="NOT_FOUND", status_code=404)
            rec = rows[0]
        return {"task": rec}

    @server.tool(
        name="update_task",
        description="Update fields on any ServiceNow task record.",
    )
    def update_task(sys_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Args:
            sys_id: System ID of the task.
            fields: Fields to update.
        """
        res = active_client.update_record("task", sys_id, fields)
        return {"success": True, "sys_id": sys_id, "record": res}

    @server.tool(
        name="list_my_approvals",
        description="List pending approval requests from sysapproval_approver table.",
    )
    def list_my_approvals(
        approver: str = "",
        state: str = "requested",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            approver: Approver username or email (defaults to current configured user).
            state: Approval state: 'requested', 'approved', 'rejected', 'not_required'.
            limit: Maximum records to return.
        """
        user = approver or active_client.username
        query = f"state={state}"
        if user:
            query += f"^approver.user_name={user}^ORapprover.email={user}"

        fields = "sys_id,document_id,sysapproval,source_table,state,approver,sys_created_on,comments"
        approvals = active_client.query_records(
            table="sysapproval_approver",
            query=query,
            fields=fields,
            limit=limit,
            order_by="-sys_created_on",
            display_value="true",
        )
        return {
            "count": len(approvals),
            "approvals": approvals,
        }

    @server.tool(
        name="approve_request",
        description="Approve a pending approval request in ServiceNow.",
    )
    def approve_request(
        approval_sys_id: str,
        comments: str = "Approved via MCP integration.",
    ) -> Dict[str, Any]:
        """
        Args:
            approval_sys_id: System ID of the sysapproval_approver record.
            comments: Approval rationale comments.
        """
        payload = {
            "state": "approved",
            "comments": comments,
        }
        res = active_client.update_record("sysapproval_approver", approval_sys_id, payload)
        return {
            "success": True,
            "message": "Approval recorded.",
            "record": res,
        }

    @server.tool(
        name="reject_request",
        description="Reject a pending approval request in ServiceNow.",
    )
    def reject_request(
        approval_sys_id: str,
        comments: str = "Rejected via MCP integration.",
    ) -> Dict[str, Any]:
        """
        Args:
            approval_sys_id: System ID of the sysapproval_approver record.
            comments: Rejection rationale comments.
        """
        payload = {
            "state": "rejected",
            "comments": comments,
        }
        res = active_client.update_record("sysapproval_approver", approval_sys_id, payload)
        return {
            "success": True,
            "message": "Rejection recorded.",
            "record": res,
        }
