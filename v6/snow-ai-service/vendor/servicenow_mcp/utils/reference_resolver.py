"""Reference resolution helpers for ServiceNow tools.

This module resolves human-friendly lookup requests into concrete ServiceNow
records. It is used by edit and ATF mutation workflows to translate names,
labels, numbers, and sys_ids into a consistent reference shape before a write
operation is attempted.
"""

from __future__ import annotations

from typing import Any, Dict, List

from servicenow_mcp.atf_framework.service import ATFServiceNowClient, safe_query, value
from servicenow_mcp.models.edit_models import ReferenceLookup


def _candidate(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a ServiceNow record to a candidate reference dict.
    
    Extracts sys_id and display_value, preferring user_name/name/number fields
    for better readability.
    
    Args:
        record: ServiceNow record dict
        
    Returns:
        Dict with sys_id, display_value, and full record.
    """
    sys_id = str(value(record.get("sys_id")) or "")
    display_value = (
        value(record.get("user_name"))
        or value(record.get("name"))
        or value(record.get("number"))
        or value(record.get("display_value"))
        or sys_id
    )
    return {
        "sys_id": sys_id,
        "display_value": display_value,
        "record": record,
    }


def resolve_reference(client: ATFServiceNowClient, lookup: ReferenceLookup) -> Dict[str, Any]:
    """
    Resolve a reference lookup to a single ServiceNow record.
    
    Searches a table for a record matching the lookup criteria:
    1. Checks if reference table is available
    2. Builds query with optional active filter
    3. Queries for matching records (limit: 1-100)
    4. Returns error if none, ambiguous (multiple), or success with single match
    
    Args:
        client: ServiceNow ATF client
        lookup: Reference lookup configuration
        
    Returns:
        Dict with status='success' and resolved sys_id/display_value if found,
        status='blocked' with reason if not found or ambiguous, or table unavailable.
    """
    if not client.check_table_exists(lookup.table):
        return {
            "status": "blocked",
            "reason": "reference_table_unavailable",
            "message": f"Reference table {lookup.table} is unavailable or blocked.",
            "candidates": [],
        }
    query = f"{lookup.lookup_field}={safe_query(lookup.lookup_value)}"
    if lookup.active_only:
        try:
            fields = set(client.query_table_fields(lookup.table))
        except Exception:
            fields = set()
        if "active" in fields:
            query += "^active=true"
    fields = list(
        dict.fromkeys(
            [
                "sys_id",
                lookup.lookup_field,
                "user_name",
                "name",
                "number",
                "active",
            ]
        )
    )
    records = client.query_records(lookup.table, query, fields=fields, limit=lookup.limit)
    candidates: List[Dict[str, Any]] = [_candidate(record) for record in records]
    if not candidates:
        return {
            "status": "blocked",
            "reason": "reference_not_found",
            "message": (
                f"No {lookup.table} record found where "
                f"{lookup.lookup_field}={lookup.lookup_value}. No update was made."
            ),
            "candidates": [],
        }
    if len(candidates) > 1:
        return {
            "status": "blocked",
            "reason": "multiple_reference_matches",
            "message": (
                f"Multiple {lookup.table} records matched "
                f"{lookup.lookup_field}={lookup.lookup_value}. No update was made."
            ),
            "candidates": candidates,
        }
    candidate = candidates[0]
    return {
        "status": "success",
        "table": lookup.table,
        "lookup_field": lookup.lookup_field,
        "lookup_value": lookup.lookup_value,
        "sys_id": candidate["sys_id"],
        "display_value": candidate["display_value"],
        "record": candidate["record"],
    }
