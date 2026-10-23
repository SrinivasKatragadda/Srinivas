"""MCP handlers for the ServiceNow ATF Framework Support layer.

This is the orchestration layer that binds the ATF framework services to MCP
tool handlers. It translates incoming parameter models into service calls,
applies response compaction, and routes common user requests such as test
generation, suite management, execution, coverage analysis, readiness checks,
and failure explanations.

The module intentionally stays close to the tool boundary: it prepares inputs,
invokes the lower-level ATF services, and shapes the response payloads into the
format the MCP server expects.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from servicenow_mcp.atf_framework.coverage import (
    analyze_atf_coverage_service,
    classify_update,
)
from servicenow_mcp.atf_framework.evidence import collect_atf_evidence_service
from servicenow_mcp.atf_framework.execution import (
    collect_atf_results_service,
    run_atf_execution_service,
)
from servicenow_mcp.atf_framework.failure_analysis import (
    explain_atf_failures_service,
)
from servicenow_mcp.atf_framework.models import (
    ACLGenerationParams,
    AddTestsToATFSuiteParams,
    AnalyzeATFCoverageParams,
    ATFArtifactTarget,
    ATFConfigureStepsParams,
    ATFExecutionRequest,
    ATFFourSuitePackParams,
    ATFQualityGateRequest,
    ATFReadinessParams,
    ATFRequestRouterParams,
    ATFRunAndCollectParams,
    ATFStepBulkCreateParams,
    ATFStepBulkUpdateParams,
    ATFStepCloneParams,
    ATFStepCreateParams,
    ATFStepDeleteParams,
    ATFStepGetParams,
    ATFStepListParams,
    ATFStepPlan,
    ATFStepReorderParams,
    ATFStepUpdateParams,
    ATFSuiteCloneParams,
    ATFSuiteCreateRequest,
    ATFSuiteDeleteParams,
    ATFSuiteGetParams,
    ATFSuiteListParams,
    ATFSuiteUpdateParams,
    ATFTestCloneParams,
    ATFTestCreateRequest,
    ATFTestDeleteParams,
    ATFTestGetParams,
    ATFTestListParams,
    ATFTestUpdateParams,
    AddTestsToATFSuiteParams,
    BusinessRuleGenerationParams,
    CatalogItemGenerationParams,
    ClientScriptGenerationParams,
    CollectATFEvidenceParams,
    CollectATFResultsParams,
    DashboardGenerationParams,
    DiscoverATFCapabilitiesParams,
    ExplainATFFailuresParams,
    FlowGenerationParams,
    GenerateATFTestPlanParams,
    IncidentATFSuiteRunParams,
    IncidentFlowATFSuiteRunParams,
    ManualTestFallbackParams,
    NotificationGenerationParams,
    RecordProducerGenerationParams,
    ReportGenerationParams,
    RegressionPackParams,
    RemoveTestsFromATFSuiteParams,
    ReorderTestsInATFSuiteParams,
    RestIntegrationGenerationParams,
    RunExistingATFParams,
    ScheduleATFSuiteParams,
    ScopedAppGenerationParams,
    ScriptIncludeGenerationParams,
    TableGenerationParams,
    UIPolicyGenerationParams,
    UpdateSetGenerationParams,
)
from servicenow_mcp.atf_framework.quality_gate import validate_atf_quality_gate_service
from servicenow_mcp.atf_framework.schedule import schedule_atf_suite_service
from servicenow_mcp.atf_framework.service import (
    ATFServiceNowClient,
    artifact_table_for,
    normalize,
    now_iso,
    record_ref,
    safe_query,
    value,
)
from servicenow_mcp.atf_framework.table_discovery import discover_atf_capabilities_service
from servicenow_mcp.atf_framework.templates import (
    ATF_SUITE_ORDER,
    ATF_SUITE_STRUCTURE,
    select_templates,
)
from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig


def _client(config: ServerConfig, auth_manager: AuthManager) -> ATFServiceNowClient:
    """Create a shared ATF ServiceNow client for tool handlers."""
    return ATFServiceNowClient(config, auth_manager)


def _dump(model: Any) -> Dict[str, Any]:
    """Serialize a Pydantic model or plain object to a dictionary."""
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _error(operation: str, exc: Exception) -> Dict[str, Any]:
    """Return a consistent failed-operation payload for MCP handlers."""
    return {
        "status": "failed",
        "operation": operation,
        "warnings": [],
        "errors": [{"message": str(exc), "type": exc.__class__.__name__}],
        "evidence": [],
        "rollback": [],
    }


_ATF_TEST_COMPACT_FIELDS = [
    "sys_id",
    "name",
    "active",
    "description",
    "sys_scope",
    "sys_package",
    "sys_created_on",
    "sys_updated_on",
]

_ATF_RESULT_COMPACT_FIELDS = [
    "table",
    "sys_id",
    "test",
    "test_suite",
    "status",
    "state",
    "result",
    "output",
    "message",
    "sys_created_on",
    "sys_updated_on",
    "start_time",
    "end_time",
    "url",
    "list_url",
]


def _compact_fields(record: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    normalized = normalize(record)
    compact: Dict[str, Any] = {}
    for field in fields:
        if field in normalized:
            compact[field] = normalized[field]
    if "sys_id" not in compact and record.get("sys_id"):
        compact["sys_id"] = value(record.get("sys_id"))
    return compact


def _compact_atf_test(record: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_fields(record, _ATF_TEST_COMPACT_FIELDS)


def _compact_atf_result(record: Dict[str, Any]) -> Dict[str, Any]:
    compact = _compact_fields(record, _ATF_RESULT_COMPACT_FIELDS)
    return {key: val for key, val in compact.items() if val not in (None, "")}


def _append_filters(base_query: str, params: RunExistingATFParams) -> str:
    parts = [base_query] if base_query else []
    if params.active is not None:
        parts.append(f"active={str(params.active).lower()}")
    if params.application_scope:
        parts.append(f"sys_scope={safe_query(params.application_scope)}")
    return "^".join(parts)


def _dedupe_by_sys_id(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for record in records:
        record_id = str(value(record.get("sys_id")) or "")
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        deduped.append(record)
    return deduped


def _resolve_existing_atf_test(
    client: ATFServiceNowClient, params: RunExistingATFParams
) -> Dict[str, Any]:
    if params.test_sys_id:
        test = client.get_record(
            "sys_atf_test", params.test_sys_id, fields=_ATF_TEST_COMPACT_FIELDS
        )
        if not test:
            return {
                "status": "blocked",
                "reason": "test_not_found",
                "message": f"ATF test {params.test_sys_id} was not found.",
                "candidates": [],
            }
        return {"status": "success", "test": test, "candidates": [_compact_atf_test(test)]}

    name = (params.test_name or "").strip()
    query = (params.query or "").strip()
    if not name and not query:
        return {
            "status": "blocked",
            "reason": "missing_target",
            "message": "Provide test_sys_id, test_name, or query for the existing ATF test.",
            "candidates": [],
        }

    records: List[Dict[str, Any]] = []
    if name:
        records = client.query_records(
            "sys_atf_test",
            _append_filters(f"name={safe_query(name)}", params),
            fields=_ATF_TEST_COMPACT_FIELDS,
            limit=params.max_candidates,
        )
        if not records:
            records = client.query_records(
                "sys_atf_test",
                _append_filters(f"nameLIKE{safe_query(name)}", params),
                fields=_ATF_TEST_COMPACT_FIELDS,
                limit=params.max_candidates,
            )
    else:
        for base_query in (f"nameLIKE{safe_query(query)}", f"descriptionLIKE{safe_query(query)}"):
            records.extend(
                client.query_records(
                    "sys_atf_test",
                    _append_filters(base_query, params),
                    fields=_ATF_TEST_COMPACT_FIELDS,
                    limit=params.max_candidates,
                )
            )
        records = _dedupe_by_sys_id(records)

    candidates = [_compact_atf_test(record) for record in records[: params.max_candidates]]
    if not records:
        return {
            "status": "blocked",
            "reason": "test_not_found",
            "message": "No ATF test matched the supplied target.",
            "candidates": [],
        }
    if len(records) > 1:
        return {
            "status": "blocked",
            "reason": "multiple_matches",
            "message": "Multiple ATF tests matched. No test was run.",
            "candidates": candidates,
        }
    return {"status": "success", "test": records[0], "candidates": candidates}


def _extract_quoted_text(text: str) -> Optional[str]:
    match = re.search(r'"([^"]+)"|\'([^\']+)\'', text)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def _extract_sys_id(text: str) -> Optional[str]:
    match = re.search(r"\b[0-9a-fA-F]{32}\b", text)
    return match.group(0) if match else None


def _route_existing_atf_run(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ATFRequestRouterParams,
    request: str,
) -> Optional[Dict[str, Any]]:
    if "run" not in request or not ("atf" in request or "aft" in request):
        return None
    if any(term in request for term in ("create", "generate", "new suite", "new test")):
        return None

    sys_id = _extract_sys_id(params.user_request)
    test_name = _extract_quoted_text(params.user_request)
    should_route = bool(sys_id or test_name or "existing" in request or "result" in request)
    if not should_route:
        return None

    runner_strategy = params.runner_strategy
    if runner_strategy == "standard" and "standard" not in request:
        runner_strategy = "cloud_runner_if_available"
    run_params = RunExistingATFParams(
        test_sys_id=sys_id,
        test_name=test_name,
        runner_strategy=runner_strategy,
        dry_run=params.dry_run or not params.auto_execute,
        wait_for_completion=True,
        include_step_results=True,
        include_failure_details=True,
        include_test_details=True,
    )
    planned_calls = [
        {
            "tool": "atf_framework.run_existing_atf_and_show_results",
            "input": _dump(run_params),
            "reason": (
                "Request asks to run an existing ATF test and show compact execution results."
            ),
        }
    ]
    warnings: List[str] = []
    if not sys_id and not test_name:
        warnings.append(
            "Provide a test_sys_id or put the exact ATF test name in quotes to run an existing "
            "test."
        )
    result = None
    if params.auto_execute and (sys_id or test_name):
        result = run_existing_atf_and_show_results(config, auth_manager, run_params)
    return {
        "status": "executed" if result else "planned",
        "operation": "route_atf_request",
        "matched_intent": "run_existing_atf_and_show_results",
        "planned_calls": planned_calls,
        "result": result,
        "warnings": warnings,
    }


def _template_to_request(
    template: Dict[str, Any],
    target: ATFArtifactTarget,
    application_scope: str,
    dry_run: bool,
    name_prefix: Optional[str] = None,
) -> ATFTestCreateRequest:
    name = (
        f"{name_prefix or target.artifact_name or target.artifact_sys_id or target.artifact_type} -"
        f" {template['name']}"
    )
    steps = [ATFStepPlan(**step) for step in template["steps"]]
    return ATFTestCreateRequest(
        test_name=name[:255],
        description=(
            f"{template['description']} Target: {target.artifact_type} "
            f"{target.artifact_sys_id or target.artifact_name or ''}"
        ),
        application_scope=application_scope,
        target_artifact=target,
        test_category=template["category"],
        steps=steps,
        cleanup_strategy="delete_created_records" if template.get("cleanup") else "none",
        dry_run=dry_run,
    )


def _empty_four_suite_plan() -> Dict[str, Dict[str, Any]]:
    return {
        suite_key: {
            **ATF_SUITE_STRUCTURE[suite_key],
            "tests": [],
            "test_count": 0,
        }
        for suite_key in ATF_SUITE_ORDER
    }


def _four_suite_plan_from_templates(templates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan = _empty_four_suite_plan()
    for template in templates:
        suite_key = template.get("suite_key") or "security_regression"
        if suite_key not in plan:
            suite_key = "security_regression"
        plan[suite_key]["tests"].append(
            {
                "template_id": template.get("template_id"),
                "name": template.get("name"),
                "artifact_type": template.get("artifact_type"),
                "category": template.get("category"),
                "description": template.get("description"),
            }
        )
        plan[suite_key]["test_count"] = len(plan[suite_key]["tests"])
    return [plan[suite_key] for suite_key in ATF_SUITE_ORDER]


def _four_suite_plan_from_generated(created_tests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan = _empty_four_suite_plan()
    for item in created_tests:
        suite_key = item.get("suite_key") or "security_regression"
        if suite_key not in plan:
            suite_key = "security_regression"
        plan[suite_key]["tests"].append(
            {
                "template_id": item.get("template_id"),
                "test_name": item.get("test_name"),
                "test_sys_id": item.get("test_sys_id"),
                "status": item.get("status"),
                "category": item.get("test_category"),
            }
        )
        plan[suite_key]["test_count"] = len(plan[suite_key]["tests"])
    return [plan[suite_key] for suite_key in ATF_SUITE_ORDER]


def _validate_target(client: ATFServiceNowClient, target: ATFArtifactTarget) -> List[str]:
    warnings: List[str] = []
    table = artifact_table_for(target.artifact_type, target.artifact_table)
    if target.artifact_type == "table":
        table_name = target.artifact_name or target.artifact_sys_id or target.artifact_table
        if table_name and not client.check_table_exists(table_name):
            warnings.append(f"Target table {table_name} could not be verified.")
        return warnings
    if target.artifact_sys_id:
        try:
            if not client.get_record(table, target.artifact_sys_id):
                warnings.append(
                    f"Target artifact {target.artifact_sys_id} was not found in {table}."
                )
        except Exception as exc:
            warnings.append(f"Could not verify target artifact in {table}: {exc}")
    elif target.artifact_name:
        try:
            if not client.query_records(
                table,
                f"name={safe_query(target.artifact_name)}^ORtitle={safe_query(target.artifact_name)}",
                limit=1,
            ):
                warnings.append(
                    f"Target artifact named {target.artifact_name} was not found in {table}."
                )
        except Exception as exc:
            warnings.append(f"Could not verify target artifact name in {table}: {exc}")
    return warnings


def _test_detail(
    client: ATFServiceNowClient,
    test: Dict[str, Any],
    include_steps: bool = True,
    include_suite_membership: bool = True,
    include_results: bool = False,
) -> Dict[str, Any]:
    test = normalize(test)
    test_id = str(value(test.get("sys_id")) or "")
    if include_steps and test_id:
        test["steps"] = client.query_atf_steps(test_id)
        test["step_count"] = len(test["steps"])
    if include_suite_membership and test_id:
        test["suite_membership"] = client.query_test_suite_memberships(test_id)
    if include_results and test_id:
        test["results"] = client.query_atf_results(test_sys_id=test_id)
    return test


def _suite_detail(
    client: ATFServiceNowClient,
    suite: Dict[str, Any],
    include_tests: bool = True,
    include_test_steps: bool = False,
    include_results: bool = False,
) -> Dict[str, Any]:
    suite = normalize(suite)
    suite_id = str(value(suite.get("sys_id")) or "")
    if include_tests and suite_id:
        mappings = client.query_suite_tests(suite_id)
        tests = []
        for mapping in mappings:
            test_id = str(value(mapping.get("test")) or "")
            if not test_id:
                continue
            try:
                test = client.get_record("sys_atf_test", test_id)
            except Exception:
                test = {"sys_id": test_id}
            tests.append(
                {
                    "mapping": mapping,
                    "test": _test_detail(client, test, include_test_steps, False, False),
                }
            )
        suite["tests"] = tests
        suite["test_count"] = len(tests)
    if include_results and suite_id:
        suite["results"] = client.query_atf_results(suite_sys_id=suite_id)
    return suite


def _copy_payload(
    record: Dict[str, Any], overrides: Dict[str, Any], excluded: set[str]
) -> Dict[str, Any]:
    payload = {key: value(val) for key, val in normalize(record).items() if key not in excluded}
    payload.update({key: val for key, val in overrides.items() if val is not None})
    return payload


def _step_payload_from_plan(
    client: ATFServiceNowClient,
    step: ATFStepPlan,
    configure_native_step: bool = True,
) -> tuple[Dict[str, Any], str]:
    base_payload = {
        "order": step.order,
        "description": f"{step.step_type}: {step.description}",
        "summary": step.expected_result,
        "_step_type": step.step_type,
        "_step_values": _dump(step).get("config", {}),
    }
    if not configure_native_step:
        return {
            "order": step.order,
            "description": base_payload["description"],
            "summary": step.expected_result,
            "values": _dump(step).get("config", {}),
        }, "metadata_only"
    payload = client.configured_atf_step_payload(base_payload)
    status = (
        "configured"
        if any(payload.get(field) for field in ("step_config", "config", "step_config_id"))
        else "metadata_only"
    )
    return payload, status


def _step_detail(
    client: ATFServiceNowClient,
    step: Dict[str, Any],
    include_test: bool = False,
    include_config_preview: bool = True,
) -> Dict[str, Any]:
    detail = normalize(step)
    test_id = str(value(detail.get("test")) or "")
    if include_test and test_id:
        try:
            detail["test_record"] = normalize(client.get_record("sys_atf_test", test_id))
        except Exception:
            detail["test_record"] = {"sys_id": test_id}
    if include_config_preview:
        inferred_type = _infer_step_type_from_record(detail)
        preview, configuration_status = _step_payload_from_plan(
            client,
            ATFStepPlan(
                order=int(value(detail.get("order")) or 0),
                step_type=inferred_type,  # type: ignore[arg-type]
                description=str(value(detail.get("description")) or ""),
                config={},
                expected_result=str(
                    value(detail.get("summary")) or value(detail.get("description")) or ""
                ),
            ),
            configure_native_step=True,
        )
        detail["inferred_step_type"] = inferred_type
        detail["configuration_status"] = (
            "configured"
            if any(
                value(detail.get(field)) for field in ("step_config", "config", "step_config_id")
            )
            else configuration_status
        )
        detail["config_preview_fields"] = sorted(preview.keys())
    return detail


def _test_inventory_entry(
    test_result: Dict[str, Any],
    suite_result: Dict[str, Any],
    planned_steps: List[ATFStepPlan],
) -> Dict[str, Any]:
    actual_by_order: Dict[int, Dict[str, Any]] = {}
    for bucket_name in ("created_steps", "reused_steps"):
        for item in test_result.get(bucket_name, []):
            planned = item.get("planned_step") or item.get("step") or {}
            try:
                order = int(planned.get("order", 0))
            except Exception:
                order = 0
            if order:
                actual_by_order[order] = item
    steps = []
    for step in sorted(planned_steps, key=lambda item: item.order):
        actual = actual_by_order.get(step.order, {})
        record = normalize(actual.get("record", {}))
        steps.append(
            {
                "order": step.order,
                "step_type": step.step_type,
                "step_name": step.description,
                "description": step.description,
                "expected_result": step.expected_result,
                "config": _dump(step).get("config", {}),
                "is_automatable": step.is_automatable,
                "operation": actual.get("operation", "planned"),
                "configuration_status": actual.get("configuration_status", "planned"),
                "step_sys_id": value(record.get("sys_id")),
                "step_table": "sys_atf_step" if record else None,
                "record": record,
            }
        )
    return {
        "test_name": test_result.get("test_name"),
        "test_sys_id": test_result.get("test_sys_id"),
        "test_table": "sys_atf_test",
        "suite_name": suite_result.get("suite_name"),
        "suite_sys_id": suite_result.get("suite_sys_id"),
        "suite_table": "sys_atf_test_suite",
        "step_count": len(steps),
        "steps": steps,
    }


def create_atf_test_service(
    client: ATFServiceNowClient, params: ATFTestCreateRequest
) -> Dict[str, Any]:
    """
    Create a new ATF test with steps based on the provided test plan.

    Workflow:
    1. Discover ATF capabilities (available tables, features)
    2. Validate target artifact (reference in step actions)
    3. Separate automatable steps from manual fallback steps
    4. For dry_run, return planned changes without applying
    5. Create test record, then create/reuse step records
    6. Collect evidence and rollback information

    Args:
        client: ServiceNow ATF client for API operations
        params: Test creation request with name, steps, target artifact

    Returns:
        Dict with status (success/failed/dry_run), created test sys_id, steps,
        warnings, evidence, and rollback information.
    """
    capabilities = discover_atf_capabilities_service(client)
    warnings = list(capabilities.get("warnings", []))
    if not capabilities["capabilities"].get("tests") or not capabilities["capabilities"].get(
        "test_steps"
    ):
        return {
            "status": "failed",
            "test_sys_id": "",
            "test_name": params.test_name,
            "created_steps": [],
            "reused_steps": [],
            "manual_fallback_steps": [],
            "warnings": warnings + ["Required ATF test or step tables are unavailable."],
            "evidence": [],
            "rollback": [],
        }
    warnings.extend(_validate_target(client, params.target_artifact))
    manual = [
        _dump(step)
        for step in params.steps
        if not step.is_automatable or step.step_type == "manual_instruction"
    ]
    automatable = [
        step
        for step in params.steps
        if step.is_automatable and step.step_type != "manual_instruction"
    ]
    if params.dry_run:
        return {
            "status": "dry_run",
            "test_sys_id": "",
            "test_name": params.test_name,
            "created_steps": [
                {"operation": "planned", "step": _dump(step)} for step in automatable
            ],
            "reused_steps": [],
            "manual_fallback_steps": manual,
            "warnings": warnings,
            "evidence": [
                {"type": "atf_test_plan", "target_artifact": _dump(params.target_artifact)}
            ],
            "rollback": [],
        }
    test_payload = {
        "name": params.test_name,
        "description": params.description,
        "active": "true",
    }
    test_result = client.create_atf_test(test_payload)
    test_record = normalize(test_result["record"])
    test_id = str(value(test_record.get("sys_id")))
    created_steps = []
    reused_steps = []
    for step in automatable:
        configured_payload, configuration_status = _step_payload_from_plan(client, step)
        step_result = client.create_atf_step(
            test_id,
            configured_payload,
        )
        bucket = reused_steps if step_result["operation"] == "reused" else created_steps
        bucket.append(
            {
                "operation": step_result["operation"],
                "record": normalize(step_result["record"]),
                "planned_step": _dump(step),
                "configuration_status": configuration_status,
            }
        )
        if configuration_status != "configured":
            warnings.append(
                f"ATF step config for {step.step_type} was not auto-discovered; created metadata "
                f"step for review."
            )
    mapping = client.map_artifact_to_atf_test(
        params.application_scope,
        {
            "artifact_type": params.target_artifact.artifact_type,
            "artifact_table": artifact_table_for(
                params.target_artifact.artifact_type, params.target_artifact.artifact_table
            ),
            "artifact_sys_id": params.target_artifact.artifact_sys_id or "",
            "artifact_name": params.target_artifact.artifact_name or "",
            "test_sys_id": test_id,
            "test_category": params.test_category,
            "coverage_type": "atf",
            "last_generated_at": now_iso(),
            "active": "true",
            "notes": params.description,
        },
        dry_run=False,
    )
    status = "partial" if manual else test_result["operation"]
    return {
        "status": status,
        "test_sys_id": test_id,
        "test_name": params.test_name,
        "created_steps": created_steps,
        "reused_steps": reused_steps,
        "manual_fallback_steps": manual,
        "warnings": warnings,
        "evidence": [
            record_ref("sys_atf_test", test_record, test_result["operation"]),
            {"type": "artifact_mapping", "mapping": mapping},
        ],
        "rollback": (
            [{"action": "delete_record", "table": "sys_atf_test", "sys_id": test_id}]
            if test_result["operation"] == "created"
            else []
        ),
    }


def create_atf_suite_service(
    client: ATFServiceNowClient, params: ATFSuiteCreateRequest
) -> Dict[str, Any]:
    """
    Create a new ATF test suite and add tests to it.

    Workflow:
    1. Discover ATF capabilities (check if suite tables exist)
    2. If dry_run, return planned suite creation without applying
    3. Create suite record with name and description
    4. Add tests to suite in specified order (creates or reuses suite-test links)
    5. Track which tests were newly added vs reused
    6. Return suite info, added/existing tests, and rollback details

    Args:
        client: ServiceNow ATF client for API operations
        params: Suite creation request with name, description, test sys_ids

    Returns:
        Dict with status (success/failed/dry_run), suite sys_id, added_tests (new suite-test
        records), existing_tests (reused records), warnings, and rollback information.
    """
    capabilities = discover_atf_capabilities_service(client)
    warnings = list(capabilities.get("warnings", []))
    if not capabilities["capabilities"].get("suites"):
        return {
            "status": "failed",
            "suite_sys_id": "",
            "suite_name": params.suite_name,
            "added_tests": [],
            "existing_tests": [],
            "warnings": warnings + ["ATF suite tables are unavailable."],
            "rollback": [],
        }
    if params.dry_run:
        return {
            "status": "dry_run",
            "suite_sys_id": "",
            "suite_name": params.suite_name,
            "added_tests": params.test_sys_ids,
            "existing_tests": [],
            "warnings": warnings,
            "rollback": [],
        }
    suite_result = client.create_atf_suite(
        {"name": params.suite_name, "description": params.description or "", "active": "true"}
    )
    suite_record = normalize(suite_result["record"])
    suite_id = str(value(suite_record.get("sys_id")))
    added: List[Dict[str, Any]] = []
    existing: List[Dict[str, Any]] = []
    for index, test_id in enumerate(params.test_sys_ids, 1):
        result = client.add_test_to_suite(suite_id, test_id, index)
        target = existing if result["operation"] == "reused" else added
        target.append(
            {
                "test_sys_id": test_id,
                "operation": result["operation"],
                "record": normalize(result["record"]),
            }
        )
    return {
        "status": suite_result["operation"],
        "suite_sys_id": suite_id,
        "suite_name": params.suite_name,
        "added_tests": added,
        "existing_tests": existing,
        "warnings": warnings,
        "rollback": (
            [{"action": "delete_record", "table": "sys_atf_test_suite", "sys_id": suite_id}]
            if suite_result["operation"] == "created"
            else []
        ),
    }


def _generate_from_templates(
    client: ATFServiceNowClient,
    artifact_type: str,
    artifact_sys_id: str,
    artifact_name: Optional[str],
    application_scope: str,
    test_depth: str,
    dry_run: bool,
    include_negative: bool = True,
    include_security: bool = True,
    artifact_table: Optional[str] = None,
) -> Dict[str, Any]:
    target = ATFArtifactTarget(
        artifact_type=artifact_type,
        artifact_table=artifact_table or artifact_table_for(artifact_type),
        artifact_sys_id=artifact_sys_id,
        artifact_name=artifact_name,
    )
    created = []
    manual = []
    warnings = []
    selected_templates = select_templates(
        artifact_type, test_depth, include_negative, include_security
    )
    for template in selected_templates:
        result = create_atf_test_service(
            client,
            _template_to_request(template, target, application_scope, dry_run, artifact_name),
        )
        result["template_id"] = template.get("template_id")
        result["template_name"] = template.get("name")
        result["test_category"] = template.get("category")
        result["suite_key"] = template.get("suite_key")
        result["suite_name"] = template.get("suite_name")
        created.append(result)
        manual.extend(result.get("manual_fallback_steps", []))
        warnings.extend(result.get("warnings", []))
    status = (
        "dry_run"
        if dry_run
        else (
            "partial"
            if manual or any(item["status"] in {"partial", "failed"} for item in created)
            else "success"
        )
    )
    return {
        "status": status,
        artifact_type: _dump(target),
        "created_tests": created,
        "suite_structure": _four_suite_plan_from_generated(created),
        "manual_fallbacks": manual,
        "required_test_data": [
            {"strategy": "deterministic_template_defaults", "artifact_type": artifact_type}
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "rollback": [rollback for item in created for rollback in item.get("rollback", [])],
    }


def discover_atf_capabilities(
    config: ServerConfig, auth_manager: AuthManager, params: DiscoverATFCapabilitiesParams
) -> Dict[str, Any]:
    """
    Public wrapper for ATF capability discovery.

    Initializes ServiceNow client and delegates to discover_atf_capabilities_service.
    Catches and formats exceptions into error responses.

    Args:
        config: ServiceNow server configuration
        auth_manager: Authentication manager for client initialization
        params: Discovery parameters (deep_check, include_field_details, check_cloud_runner)

    Returns:
        Dict with capabilities, table_report, warnings, or error details if discovery fails.
    """
    try:
        return discover_atf_capabilities_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.discover_atf_capabilities", exc)


def analyze_atf_coverage(
    config: ServerConfig, auth_manager: AuthManager, params: AnalyzeATFCoverageParams
) -> Dict[str, Any]:
    """Analyze ATF coverage for ServiceNow MCP workflows.

    This wrapper keeps the public MCP entry point thin while still ensuring the
    coverage analysis returns structured errors instead of raw exceptions.
    """
    try:
        return analyze_atf_coverage_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.analyze_atf_coverage", exc)


def list_atf_tests(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestListParams
) -> Dict[str, Any]:
    """List ATF tests for ServiceNow MCP workflows.

    Produces a compact list view with optional step and suite membership data
    for use in the MCP server response payloads.
    """
    try:
        client = _client(config, auth_manager)
        tests = [
            _test_detail(client, test, params.include_steps, params.include_suite_membership, False)
            for test in client.list_atf_tests(
                params.query, params.application_scope, params.active, params.limit
            )
        ]
        return {
            "status": "success",
            "operation": "list_atf_tests",
            "count": len(tests),
            "tests": tests,
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.list_atf_tests", exc)


def get_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestGetParams
) -> Dict[str, Any]:
    """Get ATF test for ServiceNow MCP workflows.

    Fetches one ATF test and expands related information only when requested by
    the caller.
    """
    try:
        client = _client(config, auth_manager)
        record = client.get_record("sys_atf_test", params.test_sys_id)
        if not record:
            return {
                "status": "failed",
                "operation": "get_atf_test",
                "test": {},
                "warnings": ["ATF test not found."],
            }
        return {
            "status": "success",
            "operation": "get_atf_test",
            "test": _test_detail(
                client,
                record,
                params.include_steps,
                params.include_suite_membership,
                params.include_results,
            ),
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.get_atf_test", exc)


def update_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestUpdateParams
) -> Dict[str, Any]:
    """Update ATF test for ServiceNow MCP workflows.

    Supports dry-run previews and returns a rollback payload that captures the
    previous ATF test state before modification.
    """
    try:
        client = _client(config, auth_manager)
        payload = {}
        if params.name is not None:
            payload["name"] = params.name
        if params.description is not None:
            payload["description"] = params.description
        if params.active is not None:
            payload["active"] = str(params.active).lower()
        before = client.get_record("sys_atf_test", params.test_sys_id)
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "update_atf_test",
                "test_sys_id": params.test_sys_id,
                "planned_payload": payload,
                "before": normalize(before),
                "rollback": [],
            }
        updated = client.update_record("sys_atf_test", params.test_sys_id, payload)
        return {
            "status": "success",
            "operation": "update_atf_test",
            "test": normalize(updated),
            "evidence": [record_ref("sys_atf_test", updated, "updated")],
            "rollback": [
                {
                    "action": "update_record",
                    "table": "sys_atf_test",
                    "sys_id": params.test_sys_id,
                    "payload": normalize(before),
                }
            ],
        }
    except Exception as exc:
        return _error("atf_framework.update_atf_test", exc)


def delete_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestDeleteParams
) -> Dict[str, Any]:
    """Delete ATF test for ServiceNow MCP workflows.

    Performs a guarded delete and reports the linked steps and suite
    memberships so the caller can review blast radius before committing.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        steps = client.query_atf_steps(params.test_sys_id) if params.delete_steps else []
        memberships = client.query_test_suite_memberships(params.test_sys_id)
        if params.dry_run or not params.force:
            return {
                "status": "dry_run",
                "operation": "delete_atf_test",
                "requires": "Set dry_run=false and force=true to delete.",
                "test": normalize(test),
                "steps_to_delete": steps,
                "suite_memberships": memberships,
                "warnings": [
                    "Deleting a test may affect suites. Suite mappings are reported for review."
                ],
            }
        deleted_steps = (
            client.delete_atf_steps_for_test(params.test_sys_id) if params.delete_steps else []
        )
        deleted_test = client.delete_record("sys_atf_test", params.test_sys_id)
        return {
            "status": "success",
            "operation": "delete_atf_test",
            "deleted_test": deleted_test,
            "deleted_steps": deleted_steps,
            "suite_memberships": memberships,
            "rollback": [
                {"action": "recreate_record", "table": "sys_atf_test", "payload": normalize(test)}
            ],
        }
    except Exception as exc:
        return _error("atf_framework.delete_atf_test", exc)


def clone_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestCloneParams
) -> Dict[str, Any]:
    """Clone ATF test for ServiceNow MCP workflows.

    Duplicates the source test and, when requested, its steps while preserving
    rollback data for the newly created copy.
    """
    try:
        client = _client(config, auth_manager)
        source = client.get_record("sys_atf_test", params.source_test_sys_id)
        if not source:
            return {
                "status": "failed",
                "operation": "clone_atf_test",
                "warnings": ["Source ATF test not found."],
            }
        source_name = str(value(source.get("name")) or params.source_test_sys_id)
        payload = _copy_payload(
            source,
            {
                "name": params.cloned_test_name or f"{source_name} - Clone",
                "description": params.cloned_description or value(source.get("description")),
            },
            {
                "sys_id",
                "sys_created_on",
                "sys_created_by",
                "sys_updated_on",
                "sys_updated_by",
                "number",
            },
        )
        steps = client.query_atf_steps(params.source_test_sys_id) if params.include_steps else []
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "clone_atf_test",
                "source_test": normalize(source),
                "planned_test_payload": payload,
                "steps_to_clone": steps,
            }
        created = client.create_record("sys_atf_test", payload)
        created_id = str(value(created.get("sys_id")))
        cloned_steps = []
        for step in steps:
            step_payload = _copy_payload(
                step,
                {"test": created_id},
                {
                    "sys_id",
                    "test",
                    "sys_created_on",
                    "sys_created_by",
                    "sys_updated_on",
                    "sys_updated_by",
                },
            )
            cloned_steps.append(client.create_atf_step(created_id, step_payload))
        return {
            "status": "success",
            "operation": "clone_atf_test",
            "source_test_sys_id": params.source_test_sys_id,
            "cloned_test_sys_id": created_id,
            "cloned_test": normalize(created),
            "cloned_steps": cloned_steps,
            "rollback": [
                {"action": "delete_record", "table": "sys_atf_test", "sys_id": created_id}
            ],
        }
    except Exception as exc:
        return _error("atf_framework.clone_atf_test", exc)


def list_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepListParams
) -> Dict[str, Any]:
    """List ATF test steps for ServiceNow MCP workflows.

    Returns the ordered step list for a test and can optionally include the
    parent test and configuration preview data for each step.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        steps = [
            _step_detail(client, step, params.include_test, params.include_config_preview)
            for step in client.query_atf_steps(params.test_sys_id)[: params.limit]
        ]
        return {
            "status": "success",
            "operation": "list_atf_test_steps",
            "test": normalize(test),
            "test_sys_id": params.test_sys_id,
            "count": len(steps),
            "steps": steps,
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.list_atf_test_steps", exc)


def get_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepGetParams
) -> Dict[str, Any]:
    """Get ATF test step for ServiceNow MCP workflows.

    Fetches a single step record and expands its related test or configuration
    metadata when requested.
    """
    try:
        client = _client(config, auth_manager)
        step = client.get_record("sys_atf_step", params.step_sys_id)
        if not step:
            return {
                "status": "failed",
                "operation": "get_atf_test_step",
                "step": {},
                "warnings": ["ATF step not found."],
            }
        return {
            "status": "success",
            "operation": "get_atf_test_step",
            "step": _step_detail(client, step, params.include_test, params.include_config_preview),
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.get_atf_test_step", exc)


def add_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepCreateParams
) -> Dict[str, Any]:
    """Add ATF test step.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        if not test:
            return {
                "status": "failed",
                "operation": "add_atf_test_step",
                "warnings": ["ATF test not found."],
                "step": {},
            }
        payload, configuration_status = _step_payload_from_plan(
            client, params.step, params.configure_native_step
        )
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "add_atf_test_step",
                "test": normalize(test),
                "planned_payload": payload,
                "planned_step": _dump(params.step),
                "configuration_status": configuration_status,
                "warnings": (
                    []
                    if configuration_status == "configured"
                    else [
                        "Native ATF step config was not auto-discovered; step would be metadata-"
                        "only."
                    ]
                ),
            }
        result = client.create_atf_step(params.test_sys_id, payload)
        record = normalize(result["record"])
        return {
            "status": result["operation"],
            "operation": "add_atf_test_step",
            "test_sys_id": params.test_sys_id,
            "step_sys_id": value(record.get("sys_id")),
            "step": record,
            "planned_step": _dump(params.step),
            "configuration_status": configuration_status,
            "evidence": [record_ref("sys_atf_step", record, result["operation"])],
            "rollback": (
                [
                    {
                        "action": "delete_record",
                        "table": "sys_atf_step",
                        "sys_id": value(record.get("sys_id")),
                    }
                ]
                if result["operation"] == "created"
                else []
            ),
            "warnings": (
                []
                if configuration_status == "configured"
                else [
                    "Native ATF step config was not auto-discovered; created metadata-only step "
                    "for review."
                ]
            ),
        }
    except Exception as exc:
        return _error("atf_framework.add_atf_test_step", exc)


def create_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepCreateParams
) -> Dict[str, Any]:
    """Create ATF test step.

    This helper supports MCP workflow operations and returns normalized results.
    """
    result = add_atf_test_step(config, auth_manager, params)
    result["operation"] = "create_atf_test_step"
    return result


def add_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepBulkCreateParams
) -> Dict[str, Any]:
    """Add ATF test steps.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        if not test:
            return {
                "status": "failed",
                "operation": "add_atf_test_steps",
                "warnings": ["ATF test not found."],
                "added_steps": [],
            }
        existing_steps = client.query_atf_steps(params.test_sys_id)
        if params.dry_run:
            planned = [
                {
                    "planned_step": _dump(step),
                    "payload": _step_payload_from_plan(client, step, params.configure_native_steps)[
                        0
                    ],
                    "configuration_status": _step_payload_from_plan(
                        client, step, params.configure_native_steps
                    )[1],
                }
                for step in params.steps
            ]
            return {
                "status": "dry_run",
                "operation": "add_atf_test_steps",
                "test": normalize(test),
                "existing_steps_to_replace": (
                    existing_steps if params.replace_existing_steps else []
                ),
                "planned_steps": planned,
                "warnings": [],
            }
        deleted_steps = (
            client.delete_atf_steps_for_test(params.test_sys_id)
            if params.replace_existing_steps
            else []
        )
        added_steps = []
        warnings = []
        for step in params.steps:
            payload, configuration_status = _step_payload_from_plan(
                client, step, params.configure_native_steps
            )
            result = client.create_atf_step(params.test_sys_id, payload)
            added_steps.append(
                {
                    "operation": result["operation"],
                    "record": normalize(result["record"]),
                    "planned_step": _dump(step),
                    "configuration_status": configuration_status,
                }
            )
            if configuration_status != "configured":
                warnings.append(
                    f"ATF step config for {step.step_type} was not auto-discovered; created "
                    f"metadata-only step for review."
                )
        return {
            "status": "success",
            "operation": "add_atf_test_steps",
            "test_sys_id": params.test_sys_id,
            "deleted_steps": deleted_steps,
            "added_steps": added_steps,
            "warnings": warnings,
            "rollback": (
                [{"action": "restore_steps", "table": "sys_atf_step", "records": existing_steps}]
                if params.replace_existing_steps
                else []
            ),
        }
    except Exception as exc:
        return _error("atf_framework.add_atf_test_steps", exc)


def create_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepBulkCreateParams
) -> Dict[str, Any]:
    """Create ATF test steps.

    This helper supports MCP workflow operations and returns normalized results.
    """
    result = add_atf_test_steps(config, auth_manager, params)
    result["operation"] = "create_atf_test_steps"
    return result


def _step_update_payload(
    client: ATFServiceNowClient, before: Dict[str, Any], update: Any, configure_native_step: bool
) -> Dict[str, Any]:
    update_data = _dump(update)
    payload: Dict[str, Any] = {}
    if update_data.get("order") is not None:
        payload["order"] = update_data["order"]
    if update_data.get("description") is not None:
        prefix = f"{update_data['step_type']}: " if update_data.get("step_type") else ""
        payload["description"] = f"{prefix}{update_data['description']}"
    if update_data.get("expected_result") is not None:
        payload["summary"] = update_data["expected_result"]
    if update_data.get("step_type") or update_data.get("config") is not None:
        step_type = update_data.get("step_type") or _infer_step_type_from_record(before)
        description = update_data.get("description") or str(value(before.get("description")) or "")
        if description.lower().startswith(f"{step_type}:"):
            description = description.split(":", 1)[1].strip()
        step_plan = ATFStepPlan(
            order=int(update_data.get("order") or value(before.get("order")) or 0),
            step_type=step_type,  # type: ignore[arg-type]
            description=description,
            config=update_data.get("config") or {},
            expected_result=update_data.get("expected_result")
            or str(value(before.get("summary")) or ""),
        )
        configured_payload, _ = _step_payload_from_plan(client, step_plan, configure_native_step)
        payload.update(
            {
                key: val
                for key, val in configured_payload.items()
                if key not in {"order", "description", "summary"} or key not in payload
            }
        )
    return payload


def update_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepUpdateParams
) -> Dict[str, Any]:
    """Update ATF test step.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        before = client.get_record("sys_atf_step", params.step_sys_id)
        if not before:
            return {
                "status": "failed",
                "operation": "update_atf_test_step",
                "warnings": ["ATF step not found."],
                "step": {},
            }
        payload = _step_update_payload(client, before, params, params.configure_native_step)
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "update_atf_test_step",
                "step_sys_id": params.step_sys_id,
                "before": normalize(before),
                "planned_payload": payload,
                "rollback": [],
            }
        updated = client.update_record("sys_atf_step", params.step_sys_id, payload)
        return {
            "status": "success",
            "operation": "update_atf_test_step",
            "step": normalize(updated),
            "evidence": [record_ref("sys_atf_step", updated, "updated")],
            "rollback": [
                {
                    "action": "update_record",
                    "table": "sys_atf_step",
                    "sys_id": params.step_sys_id,
                    "payload": normalize(before),
                }
            ],
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.update_atf_test_step", exc)


def update_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepBulkUpdateParams
) -> Dict[str, Any]:
    """Update ATF test steps.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        if not test:
            return {
                "status": "failed",
                "operation": "update_atf_test_steps",
                "warnings": ["ATF test not found."],
                "updated_steps": [],
            }
        existing = client.query_atf_steps(params.test_sys_id)
        existing_by_id = {
            str(value(step.get("sys_id"))): step for step in existing if value(step.get("sys_id"))
        }
        missing = [
            item.step_sys_id for item in params.updates if item.step_sys_id not in existing_by_id
        ]
        if missing:
            return {
                "status": "failed",
                "operation": "update_atf_test_steps",
                "test_sys_id": params.test_sys_id,
                "warnings": [
                    f"Step ids are not in test {params.test_sys_id}: {', '.join(missing)}"
                ],
                "updated_steps": [],
            }
        planned = []
        for item in params.updates:
            before = existing_by_id[item.step_sys_id]
            planned.append(
                {
                    "step_sys_id": item.step_sys_id,
                    "before": normalize(before),
                    "planned_payload": _step_update_payload(
                        client, before, item, params.configure_native_steps
                    ),
                }
            )
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "update_atf_test_steps",
                "test": normalize(test),
                "test_sys_id": params.test_sys_id,
                "planned_updates": planned,
                "rollback": [],
                "warnings": [],
            }
        updated = []
        for item in planned:
            updated_record = client.update_record(
                "sys_atf_step", item["step_sys_id"], item["planned_payload"]
            )
            updated.append({"step_sys_id": item["step_sys_id"], "step": normalize(updated_record)})
        return {
            "status": "success",
            "operation": "update_atf_test_steps",
            "test_sys_id": params.test_sys_id,
            "updated_steps": updated,
            "evidence": [record_ref("sys_atf_step", item["step"], "updated") for item in updated],
            "rollback": [
                {
                    "action": "update_record",
                    "table": "sys_atf_step",
                    "sys_id": item["step_sys_id"],
                    "payload": item["before"],
                }
                for item in planned
            ],
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.update_atf_test_steps", exc)


def delete_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepDeleteParams
) -> Dict[str, Any]:
    """Delete ATF test step.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        step = client.get_record("sys_atf_step", params.step_sys_id)
        if params.dry_run or not params.force:
            return {
                "status": "dry_run",
                "operation": "delete_atf_test_step",
                "requires": "Set dry_run=false and force=true to delete.",
                "step": normalize(step),
                "warnings": [
                    "Deleting an ATF step changes test behavior. Review suite coverage before "
                    "committing."
                ],
            }
        deleted = client.delete_record("sys_atf_step", params.step_sys_id)
        return {
            "status": "success",
            "operation": "delete_atf_test_step",
            "deleted_step": normalize(deleted),
            "rollback": [
                {"action": "recreate_record", "table": "sys_atf_step", "payload": normalize(step)}
            ],
        }
    except Exception as exc:
        return _error("atf_framework.delete_atf_test_step", exc)


def clone_atf_test_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepCloneParams
) -> Dict[str, Any]:
    """Clone ATF test step.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        source = client.get_record("sys_atf_step", params.source_step_sys_id)
        if not source:
            return {
                "status": "failed",
                "operation": "clone_atf_test_step",
                "warnings": ["Source ATF step not found."],
            }
        target_test_id = params.target_test_sys_id or str(value(source.get("test")) or "")
        if not target_test_id:
            return {
                "status": "failed",
                "operation": "clone_atf_test_step",
                "warnings": ["Target test sys_id is required."],
            }
        payload = _copy_payload(
            source,
            {"test": target_test_id, "order": params.order or value(source.get("order"))},
            {"sys_id", "sys_created_on", "sys_created_by", "sys_updated_on", "sys_updated_by"},
        )
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "clone_atf_test_step",
                "source_step": normalize(source),
                "planned_payload": payload,
            }
        result = client.create_atf_step(target_test_id, payload)
        record = normalize(result["record"])
        return {
            "status": result["operation"],
            "operation": "clone_atf_test_step",
            "source_step_sys_id": params.source_step_sys_id,
            "cloned_step_sys_id": value(record.get("sys_id")),
            "cloned_step": record,
            "rollback": (
                [
                    {
                        "action": "delete_record",
                        "table": "sys_atf_step",
                        "sys_id": value(record.get("sys_id")),
                    }
                ]
                if result["operation"] == "created"
                else []
            ),
        }
    except Exception as exc:
        return _error("atf_framework.clone_atf_test_step", exc)


def reorder_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFStepReorderParams
) -> Dict[str, Any]:
    """Reorder ATF test steps.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        existing = client.query_atf_steps(params.test_sys_id)
        existing_ids = {
            str(value(step.get("sys_id"))) for step in existing if value(step.get("sys_id"))
        }
        missing = [
            step_id for step_id in params.ordered_step_sys_ids if step_id not in existing_ids
        ]
        if missing:
            return {
                "status": "failed",
                "operation": "reorder_atf_test_steps",
                "warnings": [
                    f"Step ids are not in test {params.test_sys_id}: {', '.join(missing)}"
                ],
                "updated_steps": [],
            }
        planned = [
            {"step_sys_id": step_id, "order": index}
            for index, step_id in enumerate(params.ordered_step_sys_ids, 1)
        ]
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "reorder_atf_test_steps",
                "planned_updates": planned,
                "before": existing,
            }
        updated = [
            normalize(
                client.update_record("sys_atf_step", item["step_sys_id"], {"order": item["order"]})
            )
            for item in planned
        ]
        return {
            "status": "success",
            "operation": "reorder_atf_test_steps",
            "test_sys_id": params.test_sys_id,
            "updated_steps": updated,
            "rollback": [{"action": "restore_order", "table": "sys_atf_step", "records": existing}],
        }
    except Exception as exc:
        return _error("atf_framework.reorder_atf_test_steps", exc)


def list_atf_suites(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteListParams
) -> Dict[str, Any]:
    """List ATF suites.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        suites = [
            _suite_detail(client, suite, params.include_tests, False, params.include_results)
            for suite in client.list_atf_suites(
                params.query, params.application_scope, params.active, params.limit
            )
        ]
        return {
            "status": "success",
            "operation": "list_atf_suites",
            "count": len(suites),
            "suites": suites,
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.list_atf_suites", exc)


def get_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteGetParams
) -> Dict[str, Any]:
    """Get ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        record = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        if not record:
            return {
                "status": "failed",
                "operation": "get_atf_suite",
                "suite": {},
                "warnings": ["ATF suite not found."],
            }
        return {
            "status": "success",
            "operation": "get_atf_suite",
            "suite": _suite_detail(
                client,
                record,
                params.include_tests,
                params.include_test_steps,
                params.include_results,
            ),
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.get_atf_suite", exc)


def _infer_step_type_from_record(step: Dict[str, Any]) -> str:
    text = " ".join(str(step.get(key, "")) for key in ("description", "name", "summary")).lower()
    if ":" in str(step.get("description", "")):
        candidate = str(step.get("description", "")).split(":", 1)[0].strip()
        if candidate:
            return candidate
    signals = [
        ("impersonate", "impersonate"),
        ("impersonation", "impersonate"),
        ("open", "open_form"),
        ("populate", "set_field_values"),
        ("set field", "set_field_values"),
        ("submit", "submit_form"),
        ("assert", "assert_record"),
        ("validate", "assert_record"),
        ("rest", "send_rest_request"),
        ("cleanup", "cleanup_record"),
        ("delete", "cleanup_record"),
    ]
    return next((step_type for token, step_type in signals if token in text), "assert_record")


def _extract_requested_user_identifier(
    request: str, explicit_user: Optional[str] = None
) -> Optional[str]:
    if explicit_user:
        return explicit_user.strip()
    patterns = [
        r"\buser\s*(?:id|name)\s*(?:to|=|:)?\s*([A-Za-z0-9_.@-]+)",
        r"\buser\s*(?:to|=|:)\s*([A-Za-z0-9_.@-]+)",
        r"\bas\s+user\s+([A-Za-z0-9_.@-]+)",
        r"\bimpersonat(?:e|ing|ion)\s+([A-Za-z0-9_.@-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .,'\"")
            if candidate and candidate.lower() not in {"id", "name", "user"}:
                return candidate
    return None


def _extract_requested_step_order(request: str) -> Optional[int]:
    match = re.search(
        r"(?:step\s*)?(\d+)\s*[\).:-]?\s*(?:\*\*)?\s*user\s+impersonation",
        request,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1))
    match = re.search(r"\bstep\s*(\d+)\b", request, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _build_impersonation_config(
    client: ATFServiceNowClient, user_identifier: str
) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    resolved = client.resolve_user(user_identifier)
    user_value = (
        str(value(resolved.get("sys_id")) or user_identifier) if resolved else user_identifier
    )
    if not resolved:
        warnings.append(
            f"User {user_identifier} could not be resolved in sys_user; using the supplied "
            f"identifier in the ATF step config."
        )
    return {
        "user_id": user_value,
        "user": user_value,
        "user_name": (
            str(value(resolved.get("user_name")) or user_identifier)
            if resolved
            else user_identifier
        ),
    }, warnings


def _route_impersonation_step_edit(
    client: ATFServiceNowClient,
    config: ServerConfig,
    params: ATFRequestRouterParams,
) -> Optional[Dict[str, Any]]:
    request = params.user_request
    lowered = request.lower()
    if not any(term in lowered for term in ("impersonat", "user impersonation")):
        return None
    if not any(term in lowered for term in ("edit", "update", "change", "make", "set")):
        return None
    user_identifier = _extract_requested_user_identifier(request, params.test_user)
    if not user_identifier:
        return None

    step_order = _extract_requested_step_order(request)
    apply_all_tests = any(
        term in lowered for term in ("all available", "all atf", "all tests", "every test")
    )
    tests = client.list_atf_tests(
        application_scope=params.application_scope if not apply_all_tests else None, limit=500
    )
    impersonation_config, config_warnings = _build_impersonation_config(client, user_identifier)
    planned_updates: List[Dict[str, Any]] = []
    candidate_tests = []

    for test in tests:
        test_id = str(value(test.get("sys_id")) or "")
        if not test_id:
            continue
        steps = client.query_atf_steps(test_id)
        matching_steps = []
        for step in steps:
            try:
                order = int(value(step.get("order")) or 0)
            except Exception:
                order = 0
            inferred_type = _infer_step_type_from_record(step)
            if inferred_type != "impersonate":
                continue
            if step_order is not None and order != step_order:
                continue
            step_id = str(value(step.get("sys_id")) or "")
            if not step_id:
                continue
            update = {
                "step_sys_id": step_id,
                "input_name_or_label": "user_id",
                "reference_table": "sys_user",
                "reference_lookup_field": "user_name",
                "reference_lookup_value": user_identifier,
            }
            planned_updates.append(
                {
                    "test_sys_id": test_id,
                    "test": normalize(test),
                    "update": update,
                    "before": normalize(step),
                }
            )
            matching_steps.append(update)
        if matching_steps:
            candidate_tests.append(
                {"test_sys_id": test_id, "test": normalize(test), "updates": matching_steps}
            )

    planned_calls = [
        {
            "tool": "servicenow_atf_update_step_input",
            "input": {
                "test_sys_id": item["test_sys_id"],
                "step_sys_id": update["step_sys_id"],
                "input_name_or_label": "user_id",
                "reference_table": "sys_user",
                "reference_lookup_field": "user_name",
                "reference_lookup_value": user_identifier,
                "dry_run": not params.auto_execute or params.dry_run,
            },
            "reason": (
                "Update native ATF User Impersonation input storage, resolving sys_user by user_"
                "name."
            ),
        }
        for item in candidate_tests
        for update in item["updates"]
    ]

    warnings = list(config_warnings)
    if not planned_updates:
        warnings.append("No matching ATF impersonation steps were found.")
    results = []
    if params.auto_execute and not params.dry_run:
        from servicenow_mcp.models.atf_models import ATFStepInputUpdateRequest
        from servicenow_mcp.services.edit_service import AtfEditService

        edit_service = AtfEditService(client, config)
        for item in candidate_tests:
            for update_item in item["updates"]:
                results.append(
                    edit_service.update_step_input(
                        ATFStepInputUpdateRequest(
                            test_sys_id=item["test_sys_id"],
                            step_sys_id=update_item["step_sys_id"],
                            input_name_or_label="user_id",
                            reference_table="sys_user",
                            reference_lookup_field="user_name",
                            reference_lookup_value=user_identifier,
                            dry_run=False,
                        )
                    )
                )
    return {
        "status": "executed" if results else "planned",
        "operation": "route_atf_request",
        "matched_intent": "edit_atf_impersonation_steps",
        "target_user": user_identifier,
        "target_user_config": impersonation_config,
        "planned_calls": planned_calls,
        "planned_updates": planned_updates,
        "result": results if results else None,
        "warnings": warnings,
    }


def configure_atf_test_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFConfigureStepsParams
) -> Dict[str, Any]:
    """Handle configure ATF test steps.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        test = client.get_record("sys_atf_test", params.test_sys_id)
        if not test:
            return {
                "status": "failed",
                "operation": "configure_atf_test_steps",
                "warnings": ["ATF test not found."],
                "configured_steps": [],
                "unconfigured_steps": [],
            }
        steps = client.query_atf_steps(params.test_sys_id)
        configured = []
        unconfigured = []
        planned = []
        for step in steps:
            step_id = str(value(step.get("sys_id")) or "")
            has_config = any(
                value(step.get(field)) for field in ("step_config", "config", "step_config_id")
            )
            if has_config and not params.overwrite_existing_config:
                configured.append({"step": step, "operation": "reused"})
                continue
            step_type = _infer_step_type_from_record(step)
            payload = client.configured_atf_step_payload(
                {
                    "order": value(step.get("order")),
                    "description": value(step.get("description")),
                    "summary": value(step.get("summary")) or value(step.get("description")),
                    "_step_type": step_type,
                    "_step_values": {},
                }
            )
            if not any(payload.get(field) for field in ("step_config", "config", "step_config_id")):
                unconfigured.append(
                    {"step": step, "reason": f"No ATF step config discovered for {step_type}"}
                )
                continue
            planned.append({"step_sys_id": step_id, "step_type": step_type, "payload": payload})
            if not params.dry_run and step_id:
                updated = client.update_record("sys_atf_step", step_id, payload)
                configured.append(
                    {"step": normalize(updated), "operation": "updated", "step_type": step_type}
                )
        return {
            "status": "dry_run" if params.dry_run else "success",
            "operation": "configure_atf_test_steps",
            "test_sys_id": params.test_sys_id,
            "planned_updates": planned,
            "configured_steps": configured,
            "unconfigured_steps": unconfigured,
            "warnings": (
                []
                if not unconfigured
                else ["Some ATF steps still need manual step config selection."]
            ),
        }
    except Exception as exc:
        return _error("atf_framework.configure_atf_test_steps", exc)


def validate_atf_readiness(
    config: ServerConfig, auth_manager: AuthManager, params: ATFReadinessParams
) -> Dict[str, Any]:
    """Validate ATF readiness.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        capabilities = discover_atf_capabilities_service(client)
        blockers: List[str] = []
        warnings = list(capabilities.get("warnings", []))
        if not capabilities.get("atf_available"):
            blockers.append("ATF test and step tables are unavailable.")
        if params.runner_strategy == "cloud_runner_if_available" and not capabilities[
            "capabilities"
        ].get("cloud_runner"):
            warnings.append("Cloud Runner is unavailable; use standard or scheduled client runner.")
        if params.runner_strategy in {
            "client_runner",
            "scheduled_client_runner",
        } and not capabilities["capabilities"].get("client_runner"):
            warnings.append("Client runner table/capability was not verified.")
        target: Dict[str, Any] = {}
        tests = []
        if params.test_sys_id:
            test = client.get_record("sys_atf_test", params.test_sys_id)
            target = {"type": "test", "sys_id": params.test_sys_id, "record": normalize(test)}
            tests.append(_test_detail(client, test, True, True, params.include_results))
        elif params.suite_sys_id:
            suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
            target = {"type": "suite", "sys_id": params.suite_sys_id, "record": normalize(suite)}
            suite_detail = _suite_detail(client, suite, True, True, params.include_results)
            tests = [item["test"] for item in suite_detail.get("tests", [])]
            if not tests:
                blockers.append("ATF suite has no tests.")
        else:
            blockers.append("Provide test_sys_id or suite_sys_id.")
        unconfigured_steps = []
        for test in tests:
            if not test.get("steps"):
                warnings.append(f"ATF test {test.get('sys_id')} has no steps.")
            for step in test.get("steps", []):
                if not any(
                    value(step.get(field)) for field in ("step_config", "config", "step_config_id")
                ):
                    unconfigured_steps.append({"test_sys_id": test.get("sys_id"), "step": step})
        if unconfigured_steps:
            blockers.append("One or more ATF steps are missing native step configuration.")
        ready = not blockers
        return {
            "status": "ready" if ready else "not_ready",
            "operation": "validate_atf_readiness",
            "target": target,
            "capabilities": capabilities.get("capabilities", {}),
            "tests_checked": len(tests),
            "unconfigured_steps": unconfigured_steps,
            "blockers": blockers,
            "warnings": warnings,
            "recommended_actions": (
                [
                    "Run atf_framework.configure_atf_test_steps for tests with unconfigured steps.",
                    "Use atf_framework.schedule_atf_suite when a client runner is required.",
                ]
                if not ready
                else ["Run the ATF test or suite."]
            ),
        }
    except Exception as exc:
        return _error("atf_framework.validate_atf_readiness", exc)


def route_atf_request(
    config: ServerConfig, auth_manager: AuthManager, params: ATFRequestRouterParams
) -> Dict[str, Any]:
    """Route ATF request.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        request = params.user_request.lower()
        planned_calls: List[Dict[str, Any]] = []
        warnings: List[str] = []
        incident_flow_terms = (
            "system admin",
            "admin",
            "incident flow",
            "incident lifecycle",
            "lifecycle",
            "in progress",
            "resolved",
            "closed",
            "resolution",
            "test the incident flow",
        )
        impersonation_edit = _route_impersonation_step_edit(client, config, params)
        if impersonation_edit:
            return impersonation_edit
        existing_atf_run = _route_existing_atf_run(config, auth_manager, params, request)
        if existing_atf_run:
            return existing_atf_run
        if "incident" in request and any(term in request for term in incident_flow_terms):
            flow_params = IncidentFlowATFSuiteRunParams(
                application_scope=params.application_scope,
                admin_user=params.test_user or "admin",
                dry_run=params.dry_run or not params.auto_execute,
                run_after_create=params.auto_execute,
                runner_strategy=params.runner_strategy,
            )
            planned_calls.append(
                {
                    "tool": "atf_framework.create_incident_flow_atf_suite_and_run",
                    "input": _dump(flow_params),
                    "reason": (
                        "Request asks to create/run a native ATF suite for the Incident lifecycle "
                        "as an admin user."
                    ),
                }
            )
            result = None
            if params.auto_execute:
                result = create_incident_flow_atf_suite_and_run(config, auth_manager, flow_params)
            return {
                "status": "executed" if result else "planned",
                "operation": "route_atf_request",
                "matched_intent": "create_incident_flow_atf_suite_and_run",
                "planned_calls": planned_calls,
                "result": result,
                "warnings": warnings,
            }
        if "incident" in request and ("suite" in request or "test" in request):
            incident_params = IncidentATFSuiteRunParams(
                application_scope=params.application_scope,
                test_user=params.test_user,
                dry_run=params.dry_run or not params.auto_execute,
                run_after_create=params.auto_execute,
                runner_strategy=params.runner_strategy,
            )
            planned_calls.append(
                {
                    "tool": "atf_framework.create_incident_atf_suite_and_run",
                    "input": _dump(incident_params),
                    "reason": "Request mentions Incident plus ATF suite/test creation.",
                }
            )
            result = None
            if params.auto_execute:
                result = create_incident_atf_suite_and_run(config, auth_manager, incident_params)
            return {
                "status": "executed" if result else "planned",
                "operation": "route_atf_request",
                "matched_intent": "create_incident_atf_suite_and_run",
                "planned_calls": planned_calls,
                "result": result,
                "warnings": warnings,
            }
        if "step" in request and any(
            term in request
            for term in (
                "add",
                "create",
                "edit",
                "update",
                "delete",
                "remove",
                "reorder",
                "configure",
            )
        ):
            edit_all = any(
                term in request
                for term in (
                    "all step",
                    "all test step",
                    "bulk",
                    "multiple",
                    "edit all",
                    "update all",
                )
            )
            planned_calls.append(
                {
                    "tool": (
                        "atf_framework.update_atf_test_steps"
                        if edit_all
                        else "atf_framework.create_atf_test_step"
                    ),
                    "input": (
                        {
                            "test_sys_id": "<test_sys_id>",
                            "updates": [
                                {
                                    "step_sys_id": "<step_sys_id>",
                                    "order": 1,
                                    "description": "Updated step description",
                                    "expected_result": "Updated expected result",
                                }
                            ],
                            "dry_run": True,
                        }
                        if edit_all
                        else {
                            "test_sys_id": "<test_sys_id>",
                            "step": {
                                "order": 1,
                                "step_type": "assert_record",
                                "description": "Describe the ATF step to add",
                                "config": {},
                                "expected_result": "Expected result",
                                "is_automatable": True,
                            },
                            "dry_run": True,
                        }
                    ),
                    "reason": (
                        "Request mentions ATF step editing. Use step CRUD tools with the target "
                        "test_sys_id."
                    ),
                }
            )
            return {
                "status": "planned",
                "operation": "route_atf_request",
                "matched_intent": "edit_atf_test_steps",
                "planned_calls": planned_calls,
                "result": None,
                "warnings": [
                    "A concrete test_sys_id and step details are required before mutating ATF "
                    "steps."
                ],
            }
        if "coverage" in request or "untested" in request:
            planned_calls.append(
                {
                    "tool": "atf_framework.analyze_atf_coverage",
                    "input": _dump(
                        AnalyzeATFCoverageParams(
                            application_scope=params.application_scope, dry_run=True
                        )
                    ),
                    "reason": "Request asks for coverage or untested artifacts.",
                }
            )
        elif "run" in request:
            planned_calls.append(
                {
                    "tool": "atf_framework.run_atf_and_collect_results",
                    "input": _dump(
                        ATFRunAndCollectParams(runner_strategy=params.runner_strategy, dry_run=True)
                    ),
                    "reason": "Request asks to run ATF and get results.",
                }
            )
            warnings.append("A suite_sys_id or test_sys_id is required to run ATF.")
        elif "list" in request and "suite" in request:
            planned_calls.append(
                {
                    "tool": "atf_framework.list_atf_suites",
                    "input": _dump(ATFSuiteListParams()),
                    "reason": "Request asks to list ATF suites.",
                }
            )
        elif "list" in request and "test" in request:
            planned_calls.append(
                {
                    "tool": "atf_framework.list_atf_tests",
                    "input": _dump(ATFTestListParams()),
                    "reason": "Request asks to list ATF tests.",
                }
            )
        else:
            planned_calls.append(
                {
                    "tool": "atf_framework.generate_atf_test_plan",
                    "input": _dump(
                        GenerateATFTestPlanParams(
                            target_type="artifact",
                            target_identifier=params.user_request[:200],
                            artifact_type="custom",
                            dry_run=True,
                        )
                    ),
                    "reason": "No specialized ATF intent matched; generate a safe plan first.",
                }
            )
        return {
            "status": "planned",
            "operation": "route_atf_request",
            "matched_intent": "generic_atf_request",
            "planned_calls": planned_calls,
            "result": None,
            "warnings": warnings,
        }
    except Exception as exc:
        return _error("atf_framework.route_atf_request", exc)


def create_incident_atf_suite_and_run(
    config: ServerConfig, auth_manager: AuthManager, params: IncidentATFSuiteRunParams
) -> Dict[str, Any]:
    """Create incident ATF suite and run.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        capabilities = discover_atf_capabilities_service(client)
        warnings = list(capabilities.get("warnings", []))
        if not capabilities.get("atf_available"):
            return {
                "status": "failed",
                "operation": "create_incident_atf_suite_and_run",
                "warnings": warnings
                + ["ATF is not available or required ATF tables are not accessible."],
                "test": {},
                "suite": {},
                "run": {},
            }
        if not client.check_table_exists("incident"):
            return {
                "status": "failed",
                "operation": "create_incident_atf_suite_and_run",
                "warnings": warnings + ["Incident table could not be verified."],
                "test": {},
                "suite": {},
                "run": {},
            }
        for field in ("short_description", "description", "category", "urgency", "impact"):
            try:
                if not client.check_field_exists("incident", field):
                    warnings.append(
                        f"Incident field {field} could not be verified; ATF step will still be "
                        f"planned."
                    )
            except Exception as exc:
                warnings.append(f"Could not verify incident field {field}: {exc}")
        field_values = {
            "short_description": params.short_description,
            "description": params.description,
        }
        if params.category is not None:
            field_values["category"] = params.category
        if params.urgency is not None:
            field_values["urgency"] = params.urgency
        if params.impact is not None:
            field_values["impact"] = params.impact
        steps: List[ATFStepPlan] = []
        order = 1
        resolved_user: Optional[Dict[str, Any]] = None
        if params.test_user:
            resolved_user = client.resolve_user(params.test_user)
            if not resolved_user or not value(resolved_user.get("sys_id")):
                return {
                    "status": "failed",
                    "operation": "create_incident_atf_suite_and_run",
                    "warnings": warnings
                    + [
                        f"Could not resolve test_user '{params.test_user}' to a sys_user.sys_id. "
                        "Provide a valid user_name, email, name, or sys_id."
                    ],
                    "test": {},
                    "suite": {},
                    "run": {},
                }
            user_sys_id = str(value(resolved_user.get("sys_id")))
            user_name = str(
                value(resolved_user.get("user_name"))
                or value(resolved_user.get("name"))
                or params.test_user
            )
            steps.append(
                ATFStepPlan(
                    order=order,
                    step_type="impersonate",
                    description=f"Impersonate ATF test user {user_name}",
                    config={
                        "user": user_sys_id,
                        "user_id": user_sys_id,
                        "user_sys_id": user_sys_id,
                        "sys_user": user_sys_id,
                        "user_name": user_name,
                    },
                    expected_result="ATF session runs as the requested test user.",
                )
            )
            order += 1
        steps.extend(
            [
                ATFStepPlan(
                    order=order,
                    step_type="open_form",
                    description="Open a new Incident form",
                    config={"table": "incident", "form_action": "new"},
                    expected_result="Incident form opens.",
                ),
                ATFStepPlan(
                    order=order + 1,
                    step_type="set_field_values",
                    description="Populate required Incident fields",
                    config={"table": "incident", "values": field_values},
                    expected_result="Incident fields are populated.",
                ),
                ATFStepPlan(
                    order=order + 2,
                    step_type="submit_form",
                    description="Submit the Incident form",
                    config={"table": "incident", "action": "insert"},
                    expected_result="Incident record is inserted.",
                ),
                ATFStepPlan(
                    order=order + 3,
                    step_type="assert_record",
                    description="Assert the Incident record was created",
                    config={
                        "table": "incident",
                        "query": f"short_description={safe_query(params.short_description)}",
                        "expected_fields": field_values,
                    },
                    expected_result="Created Incident can be found and field values match.",
                ),
                ATFStepPlan(
                    order=order + 4,
                    step_type="cleanup_record",
                    description="Clean up the test Incident record",
                    config={
                        "table": "incident",
                        "query": f"short_description={safe_query(params.short_description)}",
                    },
                    expected_result="Test Incident is removed or marked for cleanup.",
                ),
            ]
        )
        test_result = create_atf_test_service(
            client,
            ATFTestCreateRequest(
                test_name=params.test_name,
                description=(
                    "Native ServiceNow ATF test for impersonating a test user, creating an "
                    "incident, "
                    "asserting creation, and cleaning up test data."
                ),
                application_scope=params.application_scope,
                target_artifact=ATFArtifactTarget(
                    artifact_type="table",
                    artifact_table="incident",
                    artifact_sys_id="incident",
                    artifact_name="incident",
                ),
                test_category="functional",
                steps=steps,
                test_data={
                    "incident": field_values,
                    "test_user": resolved_user or params.test_user,
                },
                cleanup_strategy="delete_created_records",
                dry_run=params.dry_run,
            ),
        )
        test_id = test_result.get("test_sys_id")
        suite_result = create_atf_suite_service(
            client,
            ATFSuiteCreateRequest(
                suite_name=params.suite_name,
                description="ATF suite for creating and validating an Incident.",
                application_scope=params.application_scope,
                suite_type="smoke",
                test_sys_ids=[test_id] if test_id else [],
                dry_run=params.dry_run,
            ),
        )
        run_result: Dict[str, Any] = {
            "status": "skipped",
            "reason": "run_after_create=false or dry_run=true",
        }
        if params.run_after_create and not params.dry_run and suite_result.get("suite_sys_id"):
            run_result = run_atf_execution_service(
                client,
                ATFExecutionRequest(
                    suite_sys_id=suite_result["suite_sys_id"],
                    runner_strategy=params.runner_strategy,
                    wait_for_completion=params.wait_for_completion,
                    timeout_seconds=params.timeout_seconds,
                    store_evidence=True,
                    dry_run=False,
                ),
            )
        inventory = [_test_inventory_entry(test_result, suite_result, steps)]
        return {
            "status": (
                "dry_run"
                if params.dry_run
                else (
                    "success"
                    if test_result.get("status") in {"created", "reused", "updated", "partial"}
                    and suite_result.get("status") in {"created", "reused", "updated", "partial"}
                    else "partial"
                )
            ),
            "operation": "create_incident_atf_suite_and_run",
            "created_records": {
                "test_sys_id": test_result.get("test_sys_id"),
                "suite_sys_id": suite_result.get("suite_sys_id"),
                "test_table": "sys_atf_test",
                "suite_table": "sys_atf_test_suite",
            },
            "test_inventory": inventory,
            "suite_inventory": {
                "suite_name": suite_result.get("suite_name"),
                "suite_sys_id": suite_result.get("suite_sys_id"),
                "suite_table": "sys_atf_test_suite",
                "test_count": len(inventory),
                "tests": inventory,
            },
            "test": test_result,
            "suite": suite_result,
            "run": run_result,
            "result_summary": run_result.get("result_summary", run_result.get("summary", {})),
            "warnings": warnings
            + test_result.get("warnings", [])
            + suite_result.get("warnings", []),
            "next_actions": [
                "If run status is blocked, start/configure the required ATF runner in ServiceNow "
                "and rerun the suite.",
                "Review any metadata_only steps and map them to instance-specific ATF step "
                "configs if needed.",
            ],
        }
    except Exception as exc:
        return _error("atf_framework.create_incident_atf_suite_and_run", exc)


def create_incident_flow_atf_suite_and_run(
    config: ServerConfig, auth_manager: AuthManager, params: IncidentFlowATFSuiteRunParams
) -> Dict[str, Any]:
    """Create incident flow ATF suite and run.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        capabilities = discover_atf_capabilities_service(client)
        warnings = list(capabilities.get("warnings", []))
        operation = "create_incident_flow_atf_suite_and_run"
        if not capabilities.get("atf_available"):
            return {
                "status": "failed",
                "operation": operation,
                "created_records": {},
                "test": {},
                "suite": {},
                "run": {},
                "warnings": warnings
                + ["ATF is not available or required ATF tables are not accessible."],
            }
        if not client.check_table_exists("incident"):
            return {
                "status": "failed",
                "operation": operation,
                "created_records": {},
                "test": {},
                "suite": {},
                "run": {},
                "warnings": warnings + ["Incident table could not be verified."],
            }

        incident_fields = (
            "short_description",
            "description",
            "category",
            "urgency",
            "impact",
            "caller_id",
            "state",
            "assigned_to",
            "assignment_group",
            "work_notes",
            "close_code",
            "close_notes",
        )
        missing_fields = []
        for field in incident_fields:
            try:
                if not client.check_field_exists("incident", field):
                    missing_fields.append(field)
            except Exception as exc:
                warnings.append(f"Could not verify incident field {field}: {exc}")
        if missing_fields:
            warnings.append(
                "These Incident fields could not be verified and may need instance-specific ATF "
                "mapping: " + ", ".join(missing_fields)
            )

        resolved_admin = client.resolve_user(params.admin_user)
        if not resolved_admin or not value(resolved_admin.get("sys_id")):
            return {
                "status": "failed",
                "operation": operation,
                "created_records": {},
                "test": {},
                "suite": {},
                "run": {},
                "warnings": warnings
                + [
                    f"Could not resolve admin_user '{params.admin_user}' to a sys_user.sys_id. "
                    "Provide a valid user_name, email, name, or sys_id."
                ],
            }
        admin_sys_id = str(value(resolved_admin.get("sys_id")))
        admin_name = str(
            value(resolved_admin.get("user_name"))
            or value(resolved_admin.get("name"))
            or params.admin_user
        )
        warnings.append(
            "Incident state values use standard ServiceNow values: New=1, In Progress=2, "
            "Resolved=6, Closed=7. "
            "If this instance customizes Incident states, update the generated ATF step values."
        )

        initial_values: Dict[str, Any] = {
            "short_description": params.short_description,
            "description": params.description,
            "caller_id": admin_sys_id,
        }
        if params.category is not None:
            initial_values["category"] = params.category
        if params.urgency is not None:
            initial_values["urgency"] = params.urgency
        if params.impact is not None:
            initial_values["impact"] = params.impact
        if params.assignment_group:
            initial_values["assignment_group"] = params.assignment_group

        in_progress_values: Dict[str, Any] = {
            "state": "2",
            "assigned_to": admin_sys_id,
            "work_notes": "ATF test transitioning incident to In Progress.",
        }
        if params.assignment_group:
            in_progress_values["assignment_group"] = params.assignment_group
        resolved_values = {
            "state": "6",
            "close_code": "Solved (Permanently)",
            "close_notes": "ATF test - incident resolved through automated test flow.",
            "work_notes": "Incident resolved by ATF as part of lifecycle validation.",
        }
        closed_values = {
            "state": "7",
            "close_notes": "ATF test - incident workflow completed and closed.",
            "work_notes": "Incident closed by ATF after lifecycle validation.",
        }
        base_query = f"short_description={safe_query(params.short_description)}"
        steps = [
            ATFStepPlan(
                order=1,
                step_type="impersonate",
                description=f"Impersonate system admin user {admin_name}",
                config={
                    "user": admin_sys_id,
                    "user_id": admin_sys_id,
                    "user_sys_id": admin_sys_id,
                    "sys_user": admin_sys_id,
                    "user_name": admin_name,
                },
                expected_result="ATF session runs as the resolved system admin user.",
            ),
            ATFStepPlan(
                order=2,
                step_type="open_form",
                description="Open a new Incident form",
                config={"table": "incident", "form_action": "new"},
                expected_result="Incident form opens.",
            ),
            ATFStepPlan(
                order=3,
                step_type="set_field_values",
                description="Populate Incident fields for admin lifecycle test",
                config={"table": "incident", "values": initial_values},
                expected_result="Incident fields are populated.",
            ),
            ATFStepPlan(
                order=4,
                step_type="submit_form",
                description="Submit the Incident form",
                config={"table": "incident", "action": "insert"},
                expected_result="Incident record is inserted.",
            ),
            ATFStepPlan(
                order=5,
                step_type="assert_record",
                description="Assert Incident was created in New state",
                config={
                    "table": "incident",
                    "query": base_query,
                    "expected_fields": {**initial_values, "state": "1"},
                },
                expected_result="Created Incident exists and starts in New state.",
            ),
            ATFStepPlan(
                order=6,
                step_type="set_field_values",
                description="Move Incident to In Progress",
                config={"table": "incident", "query": base_query, "values": in_progress_values},
                expected_result="Incident is ready to be updated to In Progress.",
            ),
            ATFStepPlan(
                order=7,
                step_type="submit_form",
                description="Save In Progress Incident update",
                config={"table": "incident", "action": "update"},
                expected_result="Incident update is submitted.",
            ),
            ATFStepPlan(
                order=8,
                step_type="assert_record",
                description="Assert Incident is In Progress",
                config={
                    "table": "incident",
                    "query": base_query,
                    "expected_fields": {"state": "2", "assigned_to": admin_sys_id},
                },
                expected_result="Incident state is In Progress and assigned to the admin user.",
            ),
            ATFStepPlan(
                order=9,
                step_type="set_field_values",
                description="Resolve the Incident",
                config={"table": "incident", "query": base_query, "values": resolved_values},
                expected_result="Incident is ready to be resolved.",
            ),
            ATFStepPlan(
                order=10,
                step_type="submit_form",
                description="Save Resolved Incident update",
                config={"table": "incident", "action": "update"},
                expected_result="Resolved update is submitted.",
            ),
            ATFStepPlan(
                order=11,
                step_type="assert_record",
                description="Assert Incident is Resolved",
                config={
                    "table": "incident",
                    "query": base_query,
                    "expected_fields": {"state": "6", "close_code": "Solved (Permanently)"},
                },
                expected_result="Incident state is Resolved with close code.",
            ),
            ATFStepPlan(
                order=12,
                step_type="set_field_values",
                description="Close the Incident",
                config={"table": "incident", "query": base_query, "values": closed_values},
                expected_result="Incident is ready to be closed.",
            ),
            ATFStepPlan(
                order=13,
                step_type="submit_form",
                description="Save Closed Incident update",
                config={"table": "incident", "action": "update"},
                expected_result="Closed update is submitted.",
            ),
            ATFStepPlan(
                order=14,
                step_type="assert_record",
                description="Assert Incident lifecycle completed in Closed state",
                config={
                    "table": "incident",
                    "query": base_query,
                    "expected_fields": {"state": "7"},
                },
                expected_result="Incident lifecycle completed through Closed state.",
            ),
            ATFStepPlan(
                order=15,
                step_type="cleanup_record",
                description="Clean up lifecycle test Incident",
                config={"table": "incident", "query": base_query},
                expected_result="Lifecycle test Incident is removed or marked for cleanup.",
            ),
        ]

        test_result = create_atf_test_service(
            client,
            ATFTestCreateRequest(
                test_name=params.test_name,
                description=(
                    "Native ServiceNow ATF test for impersonating a system admin, creating an "
                    "Incident, "
                    "moving it New -> In Progress -> Resolved -> Closed, asserting each state, "
                    "and cleaning up."
                ),
                application_scope=params.application_scope,
                target_artifact=ATFArtifactTarget(
                    artifact_type="table",
                    artifact_table="incident",
                    artifact_sys_id="incident",
                    artifact_name="incident",
                ),
                test_category="functional",
                steps=steps,
                test_data={
                    "incident": initial_values,
                    "admin_user": {
                        "sys_id": admin_sys_id,
                        "user_name": admin_name,
                    },
                    "state_flow": ["1", "2", "6", "7"],
                },
                cleanup_strategy="delete_created_records",
                dry_run=params.dry_run,
            ),
        )
        test_id = test_result.get("test_sys_id")
        suite_result = create_atf_suite_service(
            client,
            ATFSuiteCreateRequest(
                suite_name=params.suite_name,
                description=(
                    "ATF suite for validating the Incident lifecycle as a system admin user."
                ),
                application_scope=params.application_scope,
                suite_type="regression",
                test_sys_ids=[test_id] if test_id else [],
                dry_run=params.dry_run,
            ),
        )
        run_result: Dict[str, Any] = {
            "status": "skipped",
            "reason": "run_after_create=false or dry_run=true",
        }
        if params.run_after_create and not params.dry_run and suite_result.get("suite_sys_id"):
            run_result = run_atf_execution_service(
                client,
                ATFExecutionRequest(
                    suite_sys_id=suite_result["suite_sys_id"],
                    runner_strategy=params.runner_strategy,
                    wait_for_completion=params.wait_for_completion,
                    timeout_seconds=params.timeout_seconds,
                    store_evidence=True,
                    dry_run=False,
                ),
            )
        overall_status = (
            "dry_run"
            if params.dry_run
            else (
                "success"
                if test_result.get("status") in {"created", "reused", "updated", "partial"}
                and suite_result.get("status") in {"created", "reused", "updated", "partial"}
                else "partial"
            )
        )
        inventory = [_test_inventory_entry(test_result, suite_result, steps)]
        return {
            "status": overall_status,
            "operation": operation,
            "created_records": {
                "test_sys_id": test_result.get("test_sys_id"),
                "suite_sys_id": suite_result.get("suite_sys_id"),
                "test_table": "sys_atf_test",
                "suite_table": "sys_atf_test_suite",
            },
            "test_inventory": inventory,
            "suite_inventory": {
                "suite_name": suite_result.get("suite_name"),
                "suite_sys_id": suite_result.get("suite_sys_id"),
                "suite_table": "sys_atf_test_suite",
                "test_count": len(inventory),
                "tests": inventory,
            },
            "test": test_result,
            "suite": suite_result,
            "run": run_result,
            "result_summary": run_result.get("result_summary", run_result.get("summary", {})),
            "warnings": warnings
            + test_result.get("warnings", [])
            + suite_result.get("warnings", []),
            "next_actions": [
                "Review run.status and failed_steps; the tool returns ServiceNow record sys_ids "
                "instead of setup instructions.",
                "If run status is blocked, start/configure the required ATF runner in ServiceNow "
                "and rerun the suite.",
            ],
        }
    except Exception as exc:
        return _error("atf_framework.create_incident_flow_atf_suite_and_run", exc)


def generate_atf_test_plan(
    config: ServerConfig, auth_manager: AuthManager, params: GenerateATFTestPlanParams
) -> Dict[str, Any]:
    """Generate ATF test plan.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        artifact_type = params.artifact_type or "custom"
        target = {
            "type": params.target_type,
            "identifier": params.target_identifier,
            "artifact_type": artifact_type,
            "artifact_sys_id": params.artifact_sys_id,
        }
        templates = select_templates(
            artifact_type,
            params.testing_depth,
            params.include_negative_tests,
            params.include_security_tests,
        )
        planned = []
        for template in templates:
            planned.append(
                {
                    "purpose": template["description"],
                    "target_artifact": target,
                    "test_category": template["category"],
                    "preconditions": [
                        "ATF is installed and target fields are verified before creation."
                    ],
                    "test_data_strategy": (
                        "Generate scoped test data and clean it up after execution."
                    ),
                    "atf_steps": template["steps"],
                    "assertions": template["assertions"],
                    "cleanup_strategy": template["cleanup"],
                    "suite_key": template.get("suite_key"),
                    "suite_name": template.get("suite_name"),
                    "expected_result": "All assertions pass and generated data is cleaned.",
                }
            )
        return {
            "status": "success",
            "test_plan": {
                "target": target,
                "testing_depth": params.testing_depth,
                "planned_tests": planned,
                "suite_structure": _four_suite_plan_from_templates(templates),
                "required_test_data": [item["required_inputs"] for item in templates],
                "required_roles": ["atf_test_admin or equivalent scoped ATF permissions"],
                "required_runners": (
                    ["client_runner"]
                    if any(
                        "client_runner" in item["required_instance_capabilities"]
                        for item in templates
                    )
                    else ["standard"]
                ),
                "risks": [],
                "manual_fallbacks": [
                    fb for item in templates for fb in item.get("manual_fallbacks", [])
                ],
            },
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.generate_atf_test_plan", exc)


def create_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFTestCreateRequest
) -> Dict[str, Any]:
    """Create ATF test.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return create_atf_test_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.create_atf_test", exc)


def create_atf_test_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteCreateRequest
) -> Dict[str, Any]:
    """Create ATF test suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return create_atf_suite_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.create_atf_test_suite", exc)


def update_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteUpdateParams
) -> Dict[str, Any]:
    """Update ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        payload = {}
        if params.name is not None:
            payload["name"] = params.name
        if params.description is not None:
            payload["description"] = params.description
        if params.active is not None:
            payload["active"] = str(params.active).lower()
        if params.suite_type is not None:
            payload["type"] = params.suite_type
        if params.execution_order_strategy is not None:
            payload["execution_order_strategy"] = params.execution_order_strategy
        before = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "update_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "planned_payload": payload,
                "before": normalize(before),
                "rollback": [],
            }
        updated = client.update_record("sys_atf_test_suite", params.suite_sys_id, payload)
        return {
            "status": "success",
            "operation": "update_atf_suite",
            "suite": normalize(updated),
            "evidence": [record_ref("sys_atf_test_suite", updated, "updated")],
            "rollback": [
                {
                    "action": "update_record",
                    "table": "sys_atf_test_suite",
                    "sys_id": params.suite_sys_id,
                    "payload": normalize(before),
                }
            ],
        }
    except Exception as exc:
        return _error("atf_framework.update_atf_suite", exc)


def delete_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteDeleteParams
) -> Dict[str, Any]:
    """Delete ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        mappings = (
            client.query_suite_tests(params.suite_sys_id)
            if params.delete_suite_test_mappings
            else []
        )
        if params.dry_run or not params.force:
            return {
                "status": "dry_run",
                "operation": "delete_atf_suite",
                "requires": "Set dry_run=false and force=true to delete.",
                "suite": normalize(suite),
                "suite_test_mappings_to_delete": mappings,
                "warnings": [
                    "Deleting a suite does not delete tests, only suite/mapping records when "
                    "requested."
                ],
            }
        deleted_mappings = (
            client.delete_suite_mappings(params.suite_sys_id)
            if params.delete_suite_test_mappings
            else []
        )
        deleted_suite = client.delete_record("sys_atf_test_suite", params.suite_sys_id)
        return {
            "status": "success",
            "operation": "delete_atf_suite",
            "deleted_suite": deleted_suite,
            "deleted_suite_test_mappings": deleted_mappings,
            "rollback": [
                {
                    "action": "recreate_record",
                    "table": "sys_atf_test_suite",
                    "payload": normalize(suite),
                }
            ],
        }
    except Exception as exc:
        return _error("atf_framework.delete_atf_suite", exc)


def clone_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFSuiteCloneParams
) -> Dict[str, Any]:
    """Clone ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        source = client.get_record("sys_atf_test_suite", params.source_suite_sys_id)
        if not source:
            return {
                "status": "failed",
                "operation": "clone_atf_suite",
                "warnings": ["Source ATF suite not found."],
            }
        source_name = str(value(source.get("name")) or params.source_suite_sys_id)
        payload = _copy_payload(
            source,
            {
                "name": params.cloned_suite_name or f"{source_name} - Clone",
                "description": params.cloned_description or value(source.get("description")),
            },
            {
                "sys_id",
                "sys_created_on",
                "sys_created_by",
                "sys_updated_on",
                "sys_updated_by",
                "number",
            },
        )
        source_mappings = (
            client.query_suite_tests(params.source_suite_sys_id) if params.include_tests else []
        )
        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "clone_atf_suite",
                "source_suite": normalize(source),
                "planned_suite_payload": payload,
                "tests_to_clone_or_link": source_mappings,
                "clone_tests": params.clone_tests,
            }
        created_suite = client.create_record("sys_atf_test_suite", payload)
        created_suite_id = str(value(created_suite.get("sys_id")))
        linked_tests = []
        cloned_tests = []
        for index, mapping in enumerate(source_mappings, 1):
            source_test_id = str(value(mapping.get("test")) or "")
            if not source_test_id:
                continue
            target_test_id = source_test_id
            if params.clone_tests:
                clone_result = clone_atf_test(
                    config,
                    auth_manager,
                    ATFTestCloneParams(
                        source_test_sys_id=source_test_id,
                        cloned_test_name=None,
                        include_steps=True,
                        dry_run=False,
                    ),
                )
                target_test_id = str(clone_result.get("cloned_test_sys_id") or source_test_id)
                cloned_tests.append(clone_result)
            linked_tests.append(client.add_test_to_suite(created_suite_id, target_test_id, index))
        return {
            "status": "success",
            "operation": "clone_atf_suite",
            "source_suite_sys_id": params.source_suite_sys_id,
            "cloned_suite_sys_id": created_suite_id,
            "cloned_suite": normalize(created_suite),
            "linked_tests": linked_tests,
            "cloned_tests": cloned_tests,
            "rollback": [
                {
                    "action": "delete_record",
                    "table": "sys_atf_test_suite",
                    "sys_id": created_suite_id,
                }
            ],
        }
    except Exception as exc:
        return _error("atf_framework.clone_atf_suite", exc)


def add_tests_to_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: AddTestsToATFSuiteParams
) -> Dict[str, Any]:
    """Add tests to ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        if not suite:
            return {
                "status": "failed",
                "suite_sys_id": params.suite_sys_id,
                "added_tests": [],
                "existing_tests": [],
                "warnings": ["ATF suite was not found."],
                "rollback": [],
            }
        if params.dry_run:
            return {
                "status": "dry_run",
                "suite_sys_id": params.suite_sys_id,
                "added_tests": params.test_sys_ids,
                "existing_tests": [],
                "warnings": [],
                "rollback": [],
            }
        added = []
        existing = []
        for index, test_id in enumerate(params.test_sys_ids, 1):
            result = client.add_test_to_suite(params.suite_sys_id, test_id, index)
            target = existing if result["operation"] == "reused" else added
            target.append(
                {
                    "test_sys_id": test_id,
                    "operation": result["operation"],
                    "record": normalize(result["record"]),
                }
            )
        return {
            "status": "updated",
            "suite_sys_id": params.suite_sys_id,
            "added_tests": added,
            "existing_tests": existing,
            "warnings": [],
            "rollback": [],
        }
    except Exception as exc:
        return _error("atf_framework.add_tests_to_atf_suite", exc)


def remove_tests_from_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: RemoveTestsFromATFSuiteParams
) -> Dict[str, Any]:
    """Remove specific tests from an existing ATF test suite.

    Deletes the ``sys_atf_test_suite_test`` mapping records that link each
    requested test to the suite.  The ATF test records themselves are not
    touched. Supports dry-run previews and returns rollback information
    describing the mappings that were removed.

    Args:
        config: ServiceNow server configuration.
        auth_manager: Authentication manager for client initialization.
        params: Parameters specifying which suite and test sys_ids to remove.

    Returns:
        Dict with status, removed/not_found test lists, warnings, and rollback.
    """
    try:
        client = _client(config, auth_manager)
        suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        if not suite:
            return {
                "status": "failed",
                "operation": "remove_tests_from_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "removed_tests": [],
                "not_found_tests": [],
                "warnings": ["ATF suite was not found."],
                "rollback": [],
            }

        # Fetch current mappings so we can report what would be deleted and
        # build rollback payloads before the records are gone.
        existing_mappings = client.query_suite_tests(params.suite_sys_id)
        mapping_by_test_id: Dict[str, Dict[str, Any]] = {}
        for mapping in existing_mappings:
            test_id = str(value(mapping.get("test")) or "")
            if test_id:
                mapping_by_test_id[test_id] = mapping

        found = [tid for tid in params.test_sys_ids if tid in mapping_by_test_id]
        not_found = [tid for tid in params.test_sys_ids if tid not in mapping_by_test_id]

        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "remove_tests_from_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "mappings_to_remove": [normalize(mapping_by_test_id[tid]) for tid in found],
                "not_found_tests": not_found,
                "warnings": (
                    [f"These tests are not members of the suite and will be skipped: {not_found}"]
                    if not_found
                    else []
                ),
                "rollback": [],
            }

        removed = []
        rollback = []
        for test_id in found:
            mapping = mapping_by_test_id[test_id]
            result = client.remove_test_from_suite(params.suite_sys_id, test_id)
            removed.append(
                {
                    "test_sys_id": test_id,
                    "mapping_sys_id": str(value(mapping.get("sys_id")) or ""),
                    "deleted": result is not None,
                }
            )
            rollback.append(
                {
                    "action": "recreate_record",
                    "table": "sys_atf_test_suite_test",
                    "payload": normalize(mapping),
                }
            )

        warnings = []
        if not_found:
            warnings.append(
                f"These tests were not members of the suite and were skipped: {not_found}"
            )

        return {
            "status": "success",
            "operation": "remove_tests_from_atf_suite",
            "suite_sys_id": params.suite_sys_id,
            "removed_tests": removed,
            "not_found_tests": not_found,
            "warnings": warnings,
            "rollback": rollback,
        }
    except Exception as exc:
        return _error("atf_framework.remove_tests_from_atf_suite", exc)


def reorder_tests_in_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ReorderTestsInATFSuiteParams
) -> Dict[str, Any]:
    """Reorder the tests within an ATF test suite.

    Updates the ``order`` field on each ``sys_atf_test_suite_test`` mapping
    record so the tests execute in the requested sequence.  All test sys_ids
    in ``ordered_test_sys_ids`` must already be members of the suite;
    otherwise the call is rejected to avoid partial updates.  Supports
    dry-run previews.

    Args:
        config: ServiceNow server configuration.
        auth_manager: Authentication manager for client initialization.
        params: Parameters specifying suite sys_id and desired test ordering.

    Returns:
        Dict with status, planned/applied reorder, before/after snapshots,
        warnings, and rollback information.
    """
    try:
        client = _client(config, auth_manager)
        suite = client.get_record("sys_atf_test_suite", params.suite_sys_id)
        if not suite:
            return {
                "status": "failed",
                "operation": "reorder_tests_in_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "warnings": ["ATF suite was not found."],
                "updated_mappings": [],
            }

        existing_mappings = client.query_suite_tests(params.suite_sys_id)
        mapping_by_test_id: Dict[str, Dict[str, Any]] = {}
        for mapping in existing_mappings:
            test_id = str(value(mapping.get("test")) or "")
            if test_id:
                mapping_by_test_id[test_id] = mapping

        # Validate: every requested test must already be in the suite.
        missing = [tid for tid in params.ordered_test_sys_ids if tid not in mapping_by_test_id]
        if missing:
            return {
                "status": "failed",
                "operation": "reorder_tests_in_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "warnings": [
                    f"These test sys_ids are not members of suite {params.suite_sys_id} "
                    f"and cannot be reordered: {missing}"
                ],
                "updated_mappings": [],
            }

        planned = [
            {
                "test_sys_id": test_id,
                "mapping_sys_id": str(value(mapping_by_test_id[test_id].get("sys_id")) or ""),
                "order": index,
                "before_order": value(mapping_by_test_id[test_id].get("order")),
            }
            for index, test_id in enumerate(params.ordered_test_sys_ids, 1)
        ]

        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "reorder_tests_in_atf_suite",
                "suite_sys_id": params.suite_sys_id,
                "planned_updates": planned,
                "before": existing_mappings,
                "warnings": [],
            }

        updated_mappings = []
        for item in planned:
            mapping_id = item["mapping_sys_id"]
            if not mapping_id:
                continue
            updated = normalize(
                client.update_record(
                    "sys_atf_test_suite_test", mapping_id, {"order": item["order"]}
                )
            )
            updated_mappings.append(
                {
                    "test_sys_id": item["test_sys_id"],
                    "mapping_sys_id": mapping_id,
                    "order": item["order"],
                    "mapping": updated,
                }
            )

        return {
            "status": "success",
            "operation": "reorder_tests_in_atf_suite",
            "suite_sys_id": params.suite_sys_id,
            "updated_mappings": updated_mappings,
            "rollback": [
                {
                    "action": "update_record",
                    "table": "sys_atf_test_suite_test",
                    "sys_id": item["mapping_sys_id"],
                    "payload": {"order": item["before_order"]},
                }
                for item in planned
                if item["mapping_sys_id"]
            ],
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.reorder_tests_in_atf_suite", exc)


def _suite_pack_test_ids(params: ATFFourSuitePackParams) -> Dict[str, List[str]]:
    return {
        "smoke": params.smoke_test_sys_ids,
        "configuration": params.configuration_test_sys_ids,
        "end_to_end": params.end_to_end_test_sys_ids,
        "security_regression": params.security_regression_test_sys_ids,
    }


def _suite_type_for_suite_key(suite_key: str) -> str:
    if suite_key == "smoke":
        return "smoke"
    if suite_key == "security_regression":
        return "security"
    if suite_key == "configuration":
        return "application"
    return "regression"


def create_atf_four_suite_pack(
    config: ServerConfig, auth_manager: AuthManager, params: ATFFourSuitePackParams
) -> Dict[str, Any]:
    """Create the standard four-suite ATF structure for a release or app."""
    try:
        client = _client(config, auth_manager)
        suites = []
        warnings = []
        rollback = []
        test_ids_by_suite = _suite_pack_test_ids(params)

        for suite_key in ATF_SUITE_ORDER:
            suite_info = ATF_SUITE_STRUCTURE[suite_key]
            suite_name = f"{params.pack_name} - {suite_info['name']}"
            suite_result = create_atf_suite_service(
                client,
                ATFSuiteCreateRequest(
                    suite_name=suite_name,
                    description=suite_info["description"],
                    application_scope=params.application_scope,
                    release_name=params.release_name,
                    suite_type=_suite_type_for_suite_key(suite_key),  # type: ignore[arg-type]
                    test_sys_ids=test_ids_by_suite[suite_key],
                    execution_order_strategy=(
                        "smoke_first" if suite_key == "smoke" else "given_order"
                    ),
                    dry_run=params.dry_run,
                ),
            )
            suites.append(
                {
                    "suite_key": suite_key,
                    "suite_name": suite_name,
                    "description": suite_info["description"],
                    "test_sys_ids": test_ids_by_suite[suite_key],
                    "result": suite_result,
                }
            )
            warnings.extend(suite_result.get("warnings", []))
            rollback.extend(suite_result.get("rollback", []))

        failed = [item for item in suites if item["result"].get("status") == "failed"]
        return {
            "status": "dry_run" if params.dry_run else ("partial" if failed else "success"),
            "operation": "create_atf_four_suite_pack",
            "pack_name": params.pack_name,
            "application_scope": params.application_scope,
            "release_name": params.release_name,
            "suite_structure": [ATF_SUITE_STRUCTURE[key] for key in ATF_SUITE_ORDER],
            "suites": suites,
            "warnings": list(dict.fromkeys(warnings)),
            "rollback": rollback,
        }
    except Exception as exc:
        return _error("atf_framework.create_atf_four_suite_pack", exc)


def generate_atf_tests_for_update_set(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateSetGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for update set.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        updates = client.query_records(
            "sys_update_xml", f"update_set={safe_query(params.update_set_sys_id)}", limit=500
        )
        artifacts = [classify_update(normalize(item)) for item in updates]
        generated = [
            _generate_from_templates(
                client,
                artifact["artifact_type"],
                artifact["artifact_sys_id"],
                artifact["artifact_name"],
                params.application_scope,
                params.testing_depth,
                params.dry_run,
                artifact_table=artifact["artifact_table"],
            )
            for artifact in artifacts
        ]
        suite = None
        if params.create_suite:
            test_ids = [
                test.get("test_sys_id")
                for bundle in generated
                for test in bundle.get("created_tests", [])
                if test.get("test_sys_id")
            ]
            suite = create_atf_suite_service(
                client,
                ATFSuiteCreateRequest(
                    suite_name=f"ATF Regression - {params.update_set_sys_id}",
                    application_scope=params.application_scope,
                    test_sys_ids=test_ids,
                    dry_run=params.dry_run,
                ),
            )
        return {
            "status": "dry_run" if params.dry_run else "success",
            "artifacts": artifacts,
            "created_tests": generated,
            "suite": suite,
            "warnings": [],
        }
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_update_set", exc)


def generate_atf_tests_for_catalog_item(
    config: ServerConfig, auth_manager: AuthManager, params: CatalogItemGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for catalog item.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "catalog_item",
            params.catalog_item_sys_id,
            params.catalog_item_name,
            params.application_scope,
            params.test_depth,
            params.dry_run,
            params.include_negative_tests,
            params.include_role_based_tests,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_catalog_item", exc)


def generate_atf_tests_for_record_producer(
    config: ServerConfig, auth_manager: AuthManager, params: RecordProducerGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for record producer.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        result = _generate_from_templates(
            _client(config, auth_manager),
            "record_producer",
            params.record_producer_sys_id,
            "Record Producer",
            params.application_scope,
            params.test_depth,
            params.dry_run,
            params.include_negative_tests,
            True,
            "sc_cat_item_producer",
        )
        result["target_table_validation"] = params.target_table or "discovered_from_record_producer"
        result["warnings"] = result.get("warnings", []) + [
            "Record producer tests validate target table records, not only sc_req_item."
        ]
        return result
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_record_producer", exc)


def generate_atf_tests_for_flow(
    config: ServerConfig, auth_manager: AuthManager, params: FlowGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for flow.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        result = _generate_from_templates(
            _client(config, auth_manager),
            "flow",
            params.flow_sys_id,
            params.flow_name,
            params.application_scope,
            "standard",
            params.dry_run,
            params.include_error_path_validation,
        )
        result["warnings"] = result.get("warnings", []) + [
            "Flow context tables vary by family and permission; generated tests assert observable "
            "outcomes when internals are inaccessible."
        ]
        result["flow_type"] = params.flow_type
        result["trigger_table"] = params.trigger_table
        result["trigger_condition"] = params.trigger_condition
        return result
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_flow", exc)


def generate_atf_tests_for_table(
    config: ServerConfig, auth_manager: AuthManager, params: TableGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for table.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "table",
            params.table_name,
            params.table_name,
            params.application_scope,
            "standard",
            params.dry_run,
            True,
            params.test_acl_access,
            params.table_name,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_table", exc)


def _business_rule_assertions(script: str) -> List[Dict[str, Any]]:
    hints = []
    for token, message in (
        ("current.", "Assert current record field mutation."),
        ("previous.", "Include before/after comparison."),
        ("gs.addErrorMessage", "Assert user-facing error message."),
        ("setAbortAction", "Assert record operation is aborted."),
        ("GlideRecord", "Assert related-record side effects."),
        ("sys_id", "Review potential hardcoded sys_id risk."),
    ):
        if token.lower() in script.lower():
            hints.append({"signal": token, "recommendation": message})
    return hints


def generate_atf_tests_for_business_rule(
    config: ServerConfig, auth_manager: AuthManager, params: BusinessRuleGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for business rule.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        record = client.get_record("sys_script", params.business_rule_sys_id)
        result = _generate_from_templates(
            client,
            "business_rule",
            params.business_rule_sys_id,
            str(value(record.get("name")) or "Business Rule"),
            params.application_scope,
            "standard",
            params.dry_run,
            params.include_negative_test,
        )
        result["assertion_recommendations"] = _business_rule_assertions(
            str(value(record.get("script")) or "")
        )
        return result
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_business_rule", exc)


def generate_atf_tests_for_script_include(
    config: ServerConfig, auth_manager: AuthManager, params: ScriptIncludeGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for a Script Include contract."""
    try:
        client = _client(config, auth_manager)
        record = client.get_record("sys_script_include", params.script_include_sys_id)
        return _generate_from_templates(
            client,
            "script_include",
            params.script_include_sys_id,
            str(value(record.get("name")) or "Script Include"),
            params.application_scope,
            "standard",
            params.dry_run,
            params.include_negative_tests,
            params.include_security_tests,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_script_include", exc)


def generate_atf_tests_for_client_script(
    config: ServerConfig, auth_manager: AuthManager, params: ClientScriptGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for client script.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        result = _generate_from_templates(
            _client(config, auth_manager),
            "client_script",
            params.client_script_sys_id,
            "Client Script",
            params.application_scope,
            "standard",
            params.dry_run,
        )
        if params.ui_type in {"portal", "workspace"}:
            result["warnings"] = result.get("warnings", []) + [
                f"{params.ui_type} behavior may require manual evidence depending on ATF runner "
                f"support."
            ]
        return result
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_client_script", exc)


def generate_atf_tests_for_ui_policy(
    config: ServerConfig, auth_manager: AuthManager, params: UIPolicyGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for UI policy.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "ui_policy",
            params.ui_policy_sys_id,
            params.ui_policy_type,
            params.application_scope,
            "standard",
            params.dry_run,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_ui_policy", exc)


def generate_atf_tests_for_notification(
    config: ServerConfig, auth_manager: AuthManager, params: NotificationGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for notification trigger, recipient, content, and suppression behavior."""
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "notification",
            params.notification_sys_id,
            params.notification_name,
            params.application_scope,
            "standard",
            params.dry_run,
            params.include_negative_tests,
            True,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_notification", exc)


def generate_atf_tests_for_report(
    config: ServerConfig, auth_manager: AuthManager, params: ReportGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for report filters, visibility, grouping, and aggregations."""
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "report",
            params.report_sys_id,
            params.report_name,
            params.application_scope,
            "standard",
            params.dry_run,
            True,
            params.include_visibility_validation,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_report", exc)


def generate_atf_tests_for_dashboard(
    config: ServerConfig, auth_manager: AuthManager, params: DashboardGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for dashboard role visibility and widget filter behavior."""
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "dashboard",
            params.dashboard_sys_id,
            params.dashboard_name,
            params.application_scope,
            "standard",
            params.dry_run,
            True,
            params.include_visibility_validation,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_dashboard", exc)


def generate_atf_tests_for_scoped_app(
    config: ServerConfig, auth_manager: AuthManager, params: ScopedAppGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for scoped-app role, protected-data, and cross-scope security."""
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "scoped_app",
            params.application_sys_id,
            params.application_name,
            params.application_scope,
            "standard",
            params.dry_run,
            True,
            params.include_unauthorized_validation or params.include_cross_scope_validation,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_scoped_app", exc)


def generate_atf_tests_for_acl(
    config: ServerConfig, auth_manager: AuthManager, params: ACLGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for acl.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return _generate_from_templates(
            _client(config, auth_manager),
            "acl",
            params.acl_sys_id,
            params.target_table or params.target_field or "ACL",
            "mcp",
            "standard",
            params.dry_run,
            True,
            True,
        )
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_acl", exc)


def generate_atf_tests_for_rest_integration(
    config: ServerConfig, auth_manager: AuthManager, params: RestIntegrationGenerationParams
) -> Dict[str, Any]:
    """Generate ATF tests for rest integration.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        result = _generate_from_templates(
            _client(config, auth_manager),
            "rest_integration",
            params.api_name,
            params.api_name,
            "mcp",
            "standard",
            params.dry_run,
            True,
            True,
        )
        result["security"] = {"auth_strategy": params.auth_strategy, "credentials_exposed": False}
        return result
    except Exception as exc:
        return _error("atf_framework.generate_atf_tests_for_rest_integration", exc)


def run_atf_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFExecutionRequest
) -> Dict[str, Any]:
    """Run ATF test.

    This helper supports MCP workflow operations and returns normalized results.
    """
    params.suite_sys_id = None
    try:
        return run_atf_execution_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.run_atf_test", exc)


def run_atf_test_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ATFExecutionRequest
) -> Dict[str, Any]:
    """Run ATF test suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    params.test_sys_id = None
    try:
        result = run_atf_execution_service(_client(config, auth_manager), params)
        if "result_summary" in result:
            result["summary"] = {
                "total_tests": result["result_summary"].get("total", 0),
                "passed": result["result_summary"].get("passed", 0),
                "failed": result["result_summary"].get("failed", 0),
                "skipped": result["result_summary"].get("skipped", 0),
                "blocked": result["result_summary"].get("blocked", 0),
            }
            result["suite_execution_id"] = result.get("execution_id")
            result["failed_tests"] = result.get("failed_steps", [])
        return result
    except Exception as exc:
        return _error("atf_framework.run_atf_test_suite", exc)


def run_atf_and_collect_results(
    config: ServerConfig, auth_manager: AuthManager, params: ATFRunAndCollectParams
) -> Dict[str, Any]:
    """Run ATF and collect results.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        if not params.test_sys_id and not params.suite_sys_id:
            return {
                "status": "blocked",
                "operation": "run_atf_and_collect_results",
                "warnings": ["Provide test_sys_id or suite_sys_id."],
                "run": {},
                "results": {},
            }
        if params.test_sys_id and params.suite_sys_id:
            return {
                "status": "blocked",
                "operation": "run_atf_and_collect_results",
                "warnings": ["Provide only one target: test_sys_id or suite_sys_id."],
                "run": {},
                "results": {},
            }

        target_table = "sys_atf_test_suite" if params.suite_sys_id else "sys_atf_test"
        target_id = params.suite_sys_id or params.test_sys_id or ""
        target_record = normalize(client.get_record(target_table, target_id))
        run_result = run_atf_execution_service(
            client,
            ATFExecutionRequest(
                test_sys_id=params.test_sys_id,
                suite_sys_id=params.suite_sys_id,
                runner_strategy=params.runner_strategy,
                wait_for_completion=params.wait_for_completion,
                timeout_seconds=params.timeout_seconds,
                store_evidence=params.store_evidence,
                dry_run=params.dry_run,
            ),
        )
        execution_id = run_result.get("execution_id")
        collected = collect_atf_results_service(
            client,
            CollectATFResultsParams(
                test_sys_id=params.test_sys_id,
                suite_sys_id=params.suite_sys_id,
                execution_id=execution_id,
                include_step_results=params.include_step_results,
                include_failure_details=params.include_failure_details,
            ),
        )
        target_detail: Dict[str, Any] = {
            "table": target_table,
            "sys_id": target_id,
            "record": target_record,
        }
        if params.include_test_details and target_id:
            if params.test_sys_id:
                target_detail["test"] = _test_detail(client, target_record, True, True, True)
            else:
                target_detail["suite"] = _suite_detail(client, target_record, True, True, True)
        summary = collected.get("summary") or run_result.get("result_summary", {})
        failed_steps = collected.get("failed_steps") or run_result.get("failed_steps", [])
        status = run_result.get("status")
        if status in {"passed", "failed", "timeout", "blocked", "running", "dry_run"}:
            overall_status = status
        else:
            overall_status = (
                "failed"
                if summary.get("failed", 0)
                else "passed" if summary.get("total", 0) else "partial"
            )
        return {
            "status": overall_status,
            "operation": "run_atf_and_collect_results",
            "target": target_detail,
            "run": run_result,
            "results": collected,
            "summary": {
                "total": summary.get("total", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "blocked": summary.get("blocked", 0),
            },
            "execution_id": execution_id,
            "suite_execution_id": run_result.get("suite_execution_id")
            or collected.get("suite_execution_id"),
            "test_result_ids": run_result.get("test_result_ids")
            or collected.get("test_result_ids", []),
            "result_table": run_result.get("result_table") or collected.get("result_table"),
            "result_url": run_result.get("result_url") or collected.get("result_url"),
            "result_list_url": run_result.get("result_list_url")
            or collected.get("result_list_url"),
            "result_records": run_result.get("result_records")
            or collected.get("result_records", []),
            "failed_steps": failed_steps,
            "evidence": run_result.get("evidence", []) + collected.get("evidence", []),
            "warnings": run_result.get("warnings", []) + collected.get("warnings", []),
        }
    except Exception as exc:
        return _error("atf_framework.run_atf_and_collect_results", exc)


def run_existing_atf_and_show_results(
    config: ServerConfig, auth_manager: AuthManager, params: RunExistingATFParams
) -> Dict[str, Any]:
    """Run existing ATF and show results.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        resolved = _resolve_existing_atf_test(client, params)
        if resolved.get("status") != "success":
            return {
                "status": "blocked",
                "operation": "run_existing_atf_and_show_results",
                "reason": resolved.get("reason"),
                "message": resolved.get("message"),
                "target": {
                    "test_sys_id": params.test_sys_id,
                    "test_name": params.test_name,
                    "query": params.query,
                },
                "candidates": resolved.get("candidates", []),
                "run": {},
                "results": [],
                "step_results": [],
                "warnings": [],
                "evidence": [],
            }

        test = resolved["test"]
        test_id = str(value(test.get("sys_id")) or params.test_sys_id or "")
        target = {
            "table": "sys_atf_test",
            "test_sys_id": test_id,
            "test_name": normalize(test).get("name"),
            "record": _compact_atf_test(test),
        }
        if not test_id:
            return {
                "status": "blocked",
                "operation": "run_existing_atf_and_show_results",
                "reason": "missing_test_sys_id",
                "message": "Resolved ATF test did not include a sys_id.",
                "target": target,
                "candidates": resolved.get("candidates", []),
                "run": {},
                "results": [],
                "step_results": [],
                "warnings": [],
                "evidence": [],
            }

        if params.dry_run:
            return {
                "status": "dry_run",
                "operation": "run_existing_atf_and_show_results",
                "message": "Dry run only. No ATF test was started.",
                "target": target,
                "runner_strategy": params.runner_strategy,
                "run": {
                    "status": "dry_run",
                    "test_sys_id": test_id,
                    "runner_strategy": params.runner_strategy,
                    "wait_for_completion": params.wait_for_completion,
                    "timeout_seconds": params.timeout_seconds,
                },
                "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "blocked": 0},
                "execution_id": None,
                "results": [],
                "step_results": [],
                "warnings": [],
                "evidence": [
                    {"type": "atf_execution_plan", "target_kind": "test", "target_sys_id": test_id}
                ],
            }

        run_result = run_atf_execution_service(
            client,
            ATFExecutionRequest(
                test_sys_id=test_id,
                runner_strategy=params.runner_strategy,
                wait_for_completion=params.wait_for_completion,
                timeout_seconds=params.timeout_seconds,
                store_evidence=params.store_evidence,
                dry_run=False,
            ),
        )
        execution_id = run_result.get("execution_id")
        collected = collect_atf_results_service(
            client,
            CollectATFResultsParams(
                test_sys_id=test_id,
                execution_id=execution_id,
                include_step_results=params.include_step_results,
                include_failure_details=params.include_failure_details,
            ),
        )
        summary = collected.get("summary") or run_result.get("result_summary", {})
        step_results = collected.get("failed_steps") or run_result.get("failed_steps", [])
        status = run_result.get("status")
        if status not in {"passed", "failed", "timeout", "blocked", "running", "dry_run"}:
            status = (
                "failed"
                if summary.get("failed", 0)
                else "passed" if summary.get("total", 0) else "partial"
            )
        return {
            "status": status,
            "operation": "run_existing_atf_and_show_results",
            "message": (
                "Existing ATF test run completed."
                if status in {"passed", "failed"}
                else "Existing ATF test run reported a non-final status."
            ),
            "target": target,
            "runner_strategy": params.runner_strategy,
            "run": run_result,
            "summary": {
                "total": summary.get("total", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "blocked": summary.get("blocked", 0),
            },
            "execution_id": execution_id,
            "suite_execution_id": run_result.get("suite_execution_id")
            or collected.get("suite_execution_id"),
            "test_result_ids": run_result.get("test_result_ids")
            or collected.get("test_result_ids", []),
            "result_table": run_result.get("result_table") or collected.get("result_table"),
            "result_url": run_result.get("result_url") or collected.get("result_url"),
            "result_list_url": run_result.get("result_list_url")
            or collected.get("result_list_url"),
            "result_records": run_result.get("result_records")
            or collected.get("result_records", []),
            "results": [
                _compact_atf_result(item)
                for item in collected.get("results", [])
                if isinstance(item, dict)
            ],
            "step_results": [
                _compact_atf_result(item) for item in step_results if isinstance(item, dict)
            ],
            "warnings": run_result.get("warnings", []) + collected.get("warnings", []),
            "evidence": run_result.get("evidence", []) + collected.get("evidence", []),
        }
    except Exception as exc:
        return _error("atf_framework.run_existing_atf_and_show_results", exc)


def collect_atf_results(
    config: ServerConfig, auth_manager: AuthManager, params: CollectATFResultsParams
) -> Dict[str, Any]:
    """Collect ATF results.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return collect_atf_results_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.collect_atf_results", exc)


def explain_atf_failures(
    config: ServerConfig, auth_manager: AuthManager, params: ExplainATFFailuresParams
) -> Dict[str, Any]:
    """Explain ATF failures.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return explain_atf_failures_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.explain_atf_failures", exc)


def collect_atf_evidence(
    config: ServerConfig, auth_manager: AuthManager, params: CollectATFEvidenceParams
) -> Dict[str, Any]:
    """Collect ATF evidence.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return collect_atf_evidence_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.collect_atf_evidence", exc)


def validate_atf_quality_gate(
    config: ServerConfig, auth_manager: AuthManager, params: ATFQualityGateRequest
) -> Dict[str, Any]:
    """Validate ATF quality gate.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return validate_atf_quality_gate_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.validate_atf_quality_gate", exc)


def create_atf_regression_pack(
    config: ServerConfig, auth_manager: AuthManager, params: RegressionPackParams
) -> Dict[str, Any]:
    """Create ATF regression pack.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        client = _client(config, auth_manager)
        coverage = analyze_atf_coverage_service(
            client,
            AnalyzeATFCoverageParams(application_scope=params.application_scope, dry_run=True),
        )
        generated = []
        if params.generate_missing_tests:
            planned_artifacts = []
            for item in coverage.get("recommended_tests", []):
                artifact = item.get("artifact")
                if artifact:
                    planned_artifacts.append(artifact)
            for artifact in planned_artifacts:
                if params.artifact_types and artifact["artifact_type"] not in params.artifact_types:
                    continue
                generated.append(
                    _generate_from_templates(
                        client,
                        artifact["artifact_type"],
                        artifact["artifact_sys_id"],
                        artifact["artifact_name"],
                        params.application_scope,
                        params.testing_depth,
                        params.dry_run,
                    )
                )
        test_ids = [
            test.get("test_sys_id")
            for bundle in generated
            for test in bundle.get("created_tests", [])
            if test.get("test_sys_id")
        ]
        existing_tests = []
        if params.include_existing_tests:
            for item in coverage.get("existing_tests", []):
                for test in item.get("tests", []):
                    if value(test.get("sys_id")):
                        test_id = str(value(test.get("sys_id")))
                        test_ids.append(test_id)
                        existing_tests.append(test)
        suite = create_atf_suite_service(
            client,
            ATFSuiteCreateRequest(
                suite_name=params.pack_name,
                application_scope=params.application_scope,
                release_name=params.release_name,
                suite_type="regression",
                test_sys_ids=list(dict.fromkeys(test_ids)),
                dry_run=params.dry_run,
            ),
        )
        return {
            "status": "dry_run" if params.dry_run else "success",
            "regression_suite_sys_id": suite.get("suite_sys_id", ""),
            "included_tests": list(dict.fromkeys(test_ids)),
            "existing_tests_added": existing_tests,
            "generated_tests": generated,
            "missing_tests": coverage.get("missing_tests", []),
            "tests_needing_configuration": coverage.get("tests_needing_configuration", []),
            "suite_result": suite,
            "warnings": suite.get("warnings", []),
        }
    except Exception as exc:
        return _error("atf_framework.create_atf_regression_pack", exc)


def schedule_atf_suite(
    config: ServerConfig, auth_manager: AuthManager, params: ScheduleATFSuiteParams
) -> Dict[str, Any]:
    """Schedule ATF suite.

    This helper supports MCP workflow operations and returns normalized results.
    """
    try:
        return schedule_atf_suite_service(_client(config, auth_manager), params)
    except Exception as exc:
        return _error("atf_framework.schedule_atf_suite", exc)


def generate_manual_test_fallback(
    config: ServerConfig, auth_manager: AuthManager, params: ManualTestFallbackParams
) -> Dict[str, Any]:
    """Generate manual test fallback.

    This helper supports MCP workflow operations and returns normalized results.
    """
    case = {
        "title": f"Manual fallback: {params.test_objective}",
        "preconditions": [
            f"Artifact {params.artifact_type}:{params.artifact_sys_id} is available in the target "
            f"instance."
        ],
        "steps": [
            "Prepare documented test data.",
            "Execute the business scenario manually.",
            "Capture actual result and evidence.",
            "Clean up generated test data.",
        ],
        "expected_result": params.test_objective,
        "evidence_required": (
            ["screenshot", "record link", "approval note"] if params.evidence_required else []
        ),
        "owner": None,
        "reason_atf_not_possible": params.reason_atf_not_possible,
    }
    return {
        "status": "dry_run" if params.dry_run else "success",
        "manual_test_cases": [case],
        "warnings": [],
        "evidence": [],
    }
