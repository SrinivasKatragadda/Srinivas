"""ATF release evidence helpers.

This module collects and stores evidence records for ATF executions. It turns
test and suite result data into evidence entries that can be used for release
readiness reviews, audit trails, and remediation tracking.
"""

from __future__ import annotations

from typing import Any, Dict, List

from servicenow_mcp.atf_framework.execution import collect_atf_results_service
from servicenow_mcp.atf_framework.models import CollectATFEvidenceParams, CollectATFResultsParams
from servicenow_mcp.atf_framework.service import (
    ATFServiceNowClient,
    checksum,
    normalize,
    now_iso,
    scope_table,
    value,
)


def _evidence_item(release_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    status_text = str(result.get("status") or result.get("state") or result.get("result") or "")
    return {
        "release_name": release_name,
        "suite_name": result.get("test_suite") or result.get("suite"),
        "suite_sys_id": value(result.get("test_suite")) or value(result.get("suite")),
        "test_name": result.get("test"),
        "test_sys_id": value(result.get("test")),
        "execution_id": value(result.get("sys_id")),
        "result": status_text,
        "failed_step": "",
        "error_message": result.get("output") or result.get("message") or "",
        "executed_by": result.get("sys_created_by") or "",
        "executed_at": result.get("sys_created_on") or result.get("start_time") or "",
        "evidence_url": "",
        "risk_impact": "high" if "fail" in status_text.lower() else "low",
        "remediation_status": "required" if "fail" in status_text.lower() else "not_required",
    }


def ensure_atf_evidence_table(
    client: ATFServiceNowClient, scope: str = "mcp", dry_run: bool = False
) -> Dict[str, Any]:
    """
    Ensure an ATF evidence table exists in the ServiceNow instance for storing test evidence.
    
    Checks for existence of scoped evidence table. If not found and dry_run=False, creates
    the table with fields for release, suite, test, execution, result, and remediation tracking.
    
    Args:
        client: ServiceNow client for table operations
        scope: Application scope for scoped table name (default "mcp")
        dry_run: If True, returns planned creation without applying
        
    Returns:
        Dict with status (exists/created/planned), table name, operation, and created fields.
    """
    table = scope_table(scope, "atf_evidence")
    fields = [
        "release_name",
        "suite_name",
        "suite_sys_id",
        "test_name",
        "test_sys_id",
        "execution_id",
        "result",
        "failed_step",
        "error_message",
        "executed_by",
        "executed_at",
        "evidence_url",
        "risk_impact",
        "remediation_status",
        "checksum",
    ]
    if client.check_table_exists(table):
        return {"status": "exists", "table": table, "operation": "reused"}
    if dry_run:
        return {"status": "planned", "table": table, "operation": "dry_run", "fields": fields}
    table_record = client.create_record(
        "sys_db_object", {"name": table, "label": "ATF Evidence", "is_extendable": "false"}
    )
    created_fields = [
        client.create_record(
            "sys_dictionary",
            {
                "name": table,
                "element": field,
                "column_label": field.replace("_", " ").title(),
                "internal_type": "string",
                "max_length": 4000,
            },
        )
        for field in fields
    ]
    return {
        "status": "created",
        "table": table,
        "operation": "created",
        "record": normalize(table_record),
        "fields": [normalize(item) for item in created_fields],
    }


def collect_atf_evidence_service(
    client: ATFServiceNowClient, params: CollectATFEvidenceParams
) -> Dict[str, Any]:
    """
    Collect ATF test execution evidence and store in evidence table.
    
    Workflow:
    1. Ensure evidence table exists (create if needed)
    2. Query ATF results for specified suites/tests
    3. Extract evidence from results (release, test, execution, failure details)
    4. Store evidence in scoped evidence table
    5. Return evidence collection summary
    
    Args:
        client: ServiceNow ATF client for queries and record creation
        params: CollectATFEvidenceParams with suite_sys_ids, release_name, dry_run option
        
    Returns:
        Dict with status (success/failed), evidence_collected count, table_status,
        evidence_items, and any missing or blocked items.
    """
    table_status = ensure_atf_evidence_table(client, "mcp", params.dry_run)
    evidence_items: List[Dict[str, Any]] = []
    missing: List[str] = []
    for suite_id in params.suite_sys_ids or [None]:
        result_bundle = collect_atf_results_service(
            client,
            CollectATFResultsParams(
                suite_sys_id=suite_id, include_step_results=params.include_step_details
            ),
        )
        if not result_bundle["results"]:
            missing.append(f"No ATF results found for suite {suite_id or 'latest filters'}.")
        for result in result_bundle["results"]:
            item = _evidence_item(params.release_name, result)
            text = str(item["result"]).lower()
            if ("pass" in text and not params.include_passed_tests) or (
                "fail" in text and not params.include_failed_tests
            ):
                continue
            item["checksum"] = checksum(item)
            evidence_items.append(item)
    stored = []
    if not params.dry_run:
        table = table_status["table"]
        for item in evidence_items:
            stored.append(client.create_atf_evidence({"_table": table, **item}))
    return {
        "status": "success" if evidence_items else "partial",
        "evidence_package": {
            "release_name": params.release_name,
            "release_package_sys_id": params.release_package_sys_id,
            "table": table_status["table"],
            "collected_at": now_iso(),
            "operation": "dry_run" if params.dry_run else "upsert",
        },
        "evidence_items": evidence_items,
        "stored_records": stored,
        "missing_evidence": missing,
        "warnings": (
            []
            if evidence_items
            else ["ATF evidence could not be assembled from current result filters."]
        ),
    }
