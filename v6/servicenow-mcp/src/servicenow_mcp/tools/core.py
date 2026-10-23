"""
Core ServiceNow Platform Tools (CRUD, Schema, Query Validation, and Statistics).
Modeled with dry_run and pre-flight validation as in NowAIKit / aartiq.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient


def compute_diff(before: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Compute diff between existing record state and intended fields."""
    diff = {}
    for key, new_val in fields.items():
        old_val = before.get(key)
        if isinstance(old_val, dict) and "value" in old_val:
            old_val = old_val["value"]
        if str(old_val or "") != str(new_val or ""):
            diff[key] = {"from": old_val, "to": new_val}
    return diff


def validate_encoded_query(query: str) -> Dict[str, Any]:
    """Lint-check a ServiceNow encoded query string before running it."""
    issues = []
    suggestions = []

    if not query:
        return {"valid": True, "issues": [], "suggestions": []}

    # Check for raw unencoded spaces around operator
    if " = " in query or " != " in query:
        issues.append("Query contains unencoded spaces around '=' or '!=' operators.")
        suggestions.append("Replace spaces with direct operators (e.g., 'active=true' instead of 'active = true').")

    # Check for multiple conditions without '^'
    if " AND " in query or " and " in query:
        issues.append("ServiceNow queries use '^' as the AND delimiter, not 'AND'.")
        suggestions.append("Replace ' AND ' with '^' (e.g. 'active=true^priority=1').")

    if " OR " in query or " or " in query:
        issues.append("ServiceNow queries use '^OR' as the OR delimiter, not 'OR'.")
        suggestions.append("Replace ' OR ' with '^OR' (e.g. 'priority=1^ORpriority=2').")

    # Check for javascript execution
    if "javascript:" in query.lower():
        safe_functions = ["gs.now()", "gs.daysago", "gs.beginningoflastmonth()", "gs.endoflastmonth()", "gs.getuser()"]
        lower_q = query.lower()
        if not any(fn in lower_q for fn in safe_functions):
            issues.append("Query contains dynamic javascript: expression that may be restricted by security policies.")

    valid = len(issues) == 0
    return {
        "valid": valid,
        "query": query,
        "issues": issues,
        "suggestions": suggestions,
    }


def register_core_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="query_records",
        description="Query ServiceNow records from any table with encoded query filtering, field selection, limits, and sorting.",
    )
    def query_records(
        table: str,
        query: str = "",
        fields: str = "",
        limit: int = 10,
        offset: int = 0,
        order_by: str = "",
        display_value: str = "false",
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident', 'change_request', 'cmdb_ci').
            query: ServiceNow encoded query string (e.g. 'active=true^priority=1').
            fields: Comma-separated list of field names to retrieve.
            limit: Maximum records to return (default 10).
            offset: Record offset for pagination.
            order_by: Field name to sort by (prefix with '-' for descending, e.g. '-sys_created_on').
            display_value: 'true', 'false', or 'all' to return display values.
        """
        records = active_client.query_records(
            table=table,
            query=query,
            fields=fields,
            limit=limit,
            offset=offset,
            order_by=order_by,
            display_value=display_value,
        )
        return {
            "table": table,
            "count": len(records),
            "records": records,
        }

    @server.tool(
        name="get_record",
        description="Retrieve complete details of a specific record by table and sys_id.",
    )
    def get_record(
        table: str,
        sys_id: str,
        fields: str = "",
        display_value: str = "true",
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident', 'sys_user').
            sys_id: 32-character hexadecimal system ID.
            fields: Optional comma-separated fields to return.
            display_value: 'true' to return human-readable display values.
        """
        record = active_client.get_record(table=table, sys_id=sys_id, fields=fields, display_value=display_value)
        return {
            "table": table,
            "sys_id": sys_id,
            "record": record,
        }

    @server.tool(
        name="create_record",
        description="Create a new record in any ServiceNow table. Pass dry_run=true to preview payload without creating.",
    )
    def create_record(
        table: str,
        fields: Dict[str, Any],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name (e.g. 'incident', 'change_request').
            fields: Key-value dictionary of field names and their values.
            dry_run: If True, previews the resolved payload without modifying ServiceNow.
        """
        if dry_run:
            return {
                "dry_run": True,
                "table": table,
                "message": "Dry-run mode: record was NOT created.",
                "payload": fields,
            }
        result = active_client.create_record(table=table, fields=fields)
        return {
            "success": True,
            "table": table,
            "sys_id": result.get("sys_id"),
            "record": result,
        }

    @server.tool(
        name="update_record",
        description="Update an existing record in any ServiceNow table. Pass dry_run=true to preview before/after diff.",
    )
    def update_record(
        table: str,
        sys_id: str,
        fields: Dict[str, Any],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name.
            sys_id: 32-character hexadecimal sys_id of the record to update.
            fields: Key-value dictionary of fields to update.
            dry_run: If True, calculates and returns field diff without modifying ServiceNow.
        """
        if dry_run:
            existing = active_client.get_record(table=table, sys_id=sys_id)
            diff = compute_diff(existing, fields)
            return {
                "dry_run": True,
                "table": table,
                "sys_id": sys_id,
                "message": "Dry-run mode: record was NOT updated.",
                "diff": diff,
            }
        result = active_client.update_record(table=table, sys_id=sys_id, fields=fields)
        return {
            "success": True,
            "table": table,
            "sys_id": sys_id,
            "record": result,
        }

    @server.tool(
        name="delete_record",
        description="Delete a record from any ServiceNow table. Pass dry_run=true to preview without deleting.",
    )
    def delete_record(
        table: str,
        sys_id: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name.
            sys_id: System ID of the record to delete.
            dry_run: If True, fetches and previews record without deleting it.
        """
        if dry_run:
            existing = active_client.get_record(table=table, sys_id=sys_id)
            return {
                "dry_run": True,
                "table": table,
                "sys_id": sys_id,
                "message": "Dry-run mode: record was NOT deleted.",
                "target_record": existing,
            }
        result = active_client.delete_record(table=table, sys_id=sys_id)
        return {
            "success": True,
            "table": table,
            "sys_id": sys_id,
            "result": result,
        }

    @server.tool(
        name="get_table_schema",
        description="Get the structure, column definitions, data types, and labels for a ServiceNow table.",
    )
    def get_table_schema(table: str) -> Dict[str, Any]:
        """
        Args:
            table: Name of the table to inspect (e.g. 'incident', 'cmdb_ci').
        """
        columns = active_client.get_table_schema(table)
        return {
            "table": table,
            "column_count": len(columns),
            "columns": columns,
        }

    @server.tool(
        name="get_mandatory_fields",
        description="List fields made mandatory by Data Policies and Data Dictionary to prevent 403 Data Policy Exception errors.",
    )
    def get_mandatory_fields(table: str) -> Dict[str, Any]:
        """
        Args:
            table: Table name to inspect for mandatory requirements (e.g. 'incident').
        """
        return active_client.get_mandatory_fields(table)

    @server.tool(
        name="validate_query",
        description="Validate and lint a ServiceNow encoded query string before executing it.",
    )
    def validate_query(query: str) -> Dict[str, Any]:
        """
        Args:
            query: Encoded query string to validate (e.g. 'active=true^priority=1').
        """
        return validate_encoded_query(query)

    @server.tool(
        name="execute_stats",
        description="Perform server-side aggregations (count, average, min, max, sum, groupby) on any ServiceNow table.",
    )
    def execute_stats(
        table: str,
        query: str = "",
        count: bool = True,
        group_by: str = "",
        avg_fields: str = "",
        min_fields: str = "",
        max_fields: str = "",
        sum_fields: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            table: Table name to aggregate over.
            query: Optional filter query.
            count: Return record count (default True).
            group_by: Comma-separated field names to group by.
            avg_fields: Comma-separated field names for average calculation.
            min_fields: Comma-separated field names for minimum calculation.
            max_fields: Comma-separated field names for maximum calculation.
            sum_fields: Comma-separated field names for sum calculation.
        """
        stats = active_client.get_stats(
            table=table,
            query=query,
            count=count,
            group_by=group_by,
            avg_fields=avg_fields,
            min_fields=min_fields,
            max_fields=max_fields,
            sum_fields=sum_fields,
        )
        return {
            "table": table,
            "stats": stats,
        }
