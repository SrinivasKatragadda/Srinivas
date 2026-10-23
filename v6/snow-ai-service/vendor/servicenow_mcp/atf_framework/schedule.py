"""ATF schedule helpers.

This module manages ATF suite scheduling helpers and the supporting schedule
records needed to run tests on a recurring or release-triggered basis.
"""

from __future__ import annotations

from typing import Any, Dict, List

from servicenow_mcp.atf_framework.models import ScheduleATFSuiteParams
from servicenow_mcp.atf_framework.service import ATFServiceNowClient, safe_query, value
from servicenow_mcp.atf_framework.table_discovery import discover_atf_capabilities_service


def schedule_atf_suite_service(
    client: ATFServiceNowClient, params: ScheduleATFSuiteParams
) -> Dict[str, Any]:
    capabilities = discover_atf_capabilities_service(client)
    warnings: List[str] = []
    if not capabilities["capabilities"].get("schedules"):
        return {
            "status": "failed",
            "schedule_sys_id": "",
            "suite_sys_id": params.suite_sys_id,
            "warnings": ["ATF scheduling tables are unavailable or inaccessible."],
            "rollback": [],
        }
    if params.runner_strategy == "cloud_runner_if_available" and not capabilities[
        "capabilities"
    ].get("cloud_runner"):
        warnings.append(
            "Cloud Runner is unavailable; scheduling with standard ATF schedule metadata only."
        )
    suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
    if not suite:
        return {
            "status": "failed",
            "schedule_sys_id": "",
            "suite_sys_id": params.suite_sys_id,
            "warnings": ["ATF suite was not found."],
            "rollback": [],
        }
    payload = {
        "name": params.schedule_name,
        "active": "true",
        "run_start": params.start_time,
        "timezone": params.timezone,
        "type": params.schedule_type,
        "description": (
            f"ATF schedule for suite {params.suite_sys_id} using {params.runner_strategy}"
        ),
    }
    if params.dry_run:
        return {
            "status": "dry_run",
            "schedule_sys_id": "",
            "suite_sys_id": params.suite_sys_id,
            "schedule": payload,
            "warnings": warnings,
            "rollback": [],
        }
    schedule_result = client.create_atf_schedule(payload)
    schedule_record = schedule_result["record"]
    schedule_id = str(value(schedule_record.get("sys_id")))
    mapping = client.ensure_record(
        "sys_atf_schedule_test_suite",
        f"schedule={safe_query(schedule_id)}^test_suite={safe_query(params.suite_sys_id)}",
        {"schedule": schedule_id, "test_suite": params.suite_sys_id},
    )
    return {
        "status": schedule_result["operation"],
        "schedule_sys_id": schedule_id,
        "suite_sys_id": params.suite_sys_id,
        "schedule": schedule_record,
        "mapping": mapping,
        "warnings": warnings,
        "rollback": [
            {"action": "delete_record", "table": "sys_atf_schedule", "sys_id": schedule_id}
        ],
    }
