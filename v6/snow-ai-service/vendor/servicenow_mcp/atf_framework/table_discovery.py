"""ATF runtime table and capability discovery.

This module probes a ServiceNow instance for the ATF runtime surface: core
tables, field availability, runner support, plugin hints, and permission
errors. It is used as the first-pass capability check before higher-level ATF
operations attempt to create tests, execute suites, or collect evidence.

The output is intentionally descriptive rather than binary. Callers need to
know not only whether ATF exists, but also which supporting tables, fields, and
runner modes are available so they can choose the correct execution path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from servicenow_mcp.atf_framework.models import DiscoverATFCapabilitiesParams
from servicenow_mcp.atf_framework.service import ATF_TABLES, CRITICAL_FIELDS, ATFServiceNowClient

QUICK_ATF_TABLES = [
    "sys_atf_test",
    "sys_atf_step",
    "sys_atf_test_suite",
    "sys_atf_test_suite_test",
    "sys_atf_test_result",
    "sys_atf_test_suite_result",
    "sys_atf_step_config",
    "sys_atf_schedule",
    "sys_atf_runner",
]


def discover_atf_capabilities_service(
    client: ATFServiceNowClient,
    params: Optional[DiscoverATFCapabilitiesParams] = None,
) -> Dict[str, Any]:
    """
    Discover ATF capabilities and table availability in the ServiceNow instance.
    
    Workflow:
    1. Choose a quick or deep table list depending on params.deep_check
    2. Check each table for existence and optionally fetch field lists
    3. Flag missing critical fields and missing permissions as warnings
    4. Optionally probe for Cloud Runner support via plugin records
    5. Return capability flags and recommended next actions
    
    Args:
        client: ServiceNow ATF client for table/field queries
        params: DiscoverATFCapabilitiesParams with check depth, field details, cloud runner
        
    Returns:
        Dict with capabilities (tests, test_steps, suites, result_collection, cloud_runner),
        table_report (field counts and availability), warnings, and recommended next_actions.
    """
    params = params or DiscoverATFCapabilitiesParams()
    warnings: List[str] = []
    missing_permissions: List[str] = []
    verified_tables = []
    missing_tables = []
    table_report: Dict[str, Any] = {}
    tables_to_check = ATF_TABLES if params.deep_check else QUICK_ATF_TABLES
    for table in tables_to_check:
        try:
            exists = client.check_table_exists(table)
            fields = (
                client.query_table_fields(table)
                if exists and (params.deep_check or table in CRITICAL_FIELDS)
                else []
            )
            table_report[table] = {
                "exists": exists,
                "field_count": len(fields),
                **({"fields": fields} if params.include_field_details else {}),
            }
            if exists:
                verified_tables.append(
                    {
                        "table": table,
                        "field_count": len(fields),
                        **({"fields": fields} if params.include_field_details else {}),
                    }
                )
                missing_fields = [
                    field for field in CRITICAL_FIELDS.get(table, []) if field not in fields
                ]
                if missing_fields:
                    warnings.append(
                        f"{table} is missing expected fields: {', '.join(missing_fields)}"
                    )
            else:
                missing_tables.append(table)
        except requests.exceptions.HTTPError as exc:
            missing_permissions.append(table)
            table_report[table] = {"exists": False, "field_count": 0, "error": str(exc)}
        except requests.exceptions.RequestException as exc:
            warnings.append(f"Could not verify {table}: {exc}")
            table_report[table] = {"exists": False, "field_count": 0, "error": str(exc)}
    capabilities = {
        "tests": table_report.get("sys_atf_test", {}).get("exists", False),
        "test_steps": table_report.get("sys_atf_step", {}).get("exists", False),
        "suites": table_report.get("sys_atf_test_suite", {}).get("exists", False),
        "suite_hierarchy": table_report.get("sys_atf_test_suite_test", {}).get("exists", False),
        "schedules": table_report.get("sys_atf_schedule", {}).get("exists", False),
        "client_runner": table_report.get("sys_atf_runner", {}).get("exists", False),
        "cloud_runner": False,
        "result_collection": table_report.get("sys_atf_test_result", {}).get("exists", False)
        or table_report.get("sys_atf_test_suite_result", {}).get("exists", False),
    }
    if params.check_cloud_runner:
        try:
            plugins = client.query_records(
                "v_plugin",
                "id=sn_atf_tg^ORidLIKEcloud_runner^ORnameLIKECloud Runner^ORnameLIKEATF Test "
                "Generator",
                fields=["id,name,active"],
                limit=10,
            )
            capabilities["cloud_runner"] = any(
                str(item.get("active")).lower() in {"true", "1"} for item in plugins
            )
        except Exception:
            warnings.append(
                "Cloud Runner plugin/capability could not be verified; using standard ATF runner "
                "guidance."
            )
    atf_available = capabilities["tests"] and capabilities["test_steps"]
    next_actions = []
    if not atf_available:
        next_actions.append("Activate Automated Test Framework or request table access.")
    if missing_permissions:
        next_actions.append("Grant ATF read/write roles or ACL access for missing tables.")
    return {
        "status": "success" if atf_available else "failed",
        "atf_available": atf_available,
        "tables": table_report,
        "capabilities": capabilities,
        "verified_tables": verified_tables,
        "missing_tables": missing_tables,
        "missing_permissions": missing_permissions,
        "supports_suites": capabilities["suites"],
        "supports_schedules": capabilities["schedules"],
        "supports_client_runner": capabilities["client_runner"],
        "supports_cloud_runner": capabilities["cloud_runner"],
        "cloud_runner_plugin_detected": capabilities["cloud_runner"],
        "warnings": warnings,
        "next_actions": next_actions,
    }
