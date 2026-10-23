"""ATF execution and result collection helpers.

This module coordinates the runtime side of ATF workflows:
- Launching test and suite executions through supported runner strategies
- Polling and collecting execution result records
- Normalizing result payloads into compact, report-friendly structures
- Interpreting runner unavailability and final execution state

The functions here are used by the MCP tool layer to provide deterministic
execution summaries and post-run evidence. They are deliberately focused on
result shaping and orchestration, while the ServiceNow client layer handles
the raw API communication.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from servicenow_mcp.atf_framework.models import ATFExecutionRequest, CollectATFResultsParams
from servicenow_mcp.atf_framework.service import ATFServiceNowClient, normalize, value
from servicenow_mcp.atf_framework.table_discovery import discover_atf_capabilities_service


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Summarize ATF test results into pass/fail/skip/blocked counts.
    
    The classifier uses a small precedence order that mirrors ATF reporting:
    1. Prefer records from sys_atf_test_result when available
    2. Inspect status/state/result text for pass/skip/block/fail markers
    3. Classify based on the first matching category
    4. Return a compact count summary for reporting and quality gates
    
    Args:
        results: List of ATF result records (from test or suite result tables)
        
    Returns:
        Dict with counts: total, passed, failed, skipped, blocked
    """
    result_records = [result for result in results if result.get("table") == "sys_atf_test_result"]
    source = result_records or results
    summary = {"total": len(source), "passed": 0, "failed": 0, "skipped": 0, "blocked": 0}
    for result in source:
        text = " ".join(str(result.get(key, "")) for key in ("status", "state", "result")).lower()
        if "pass" in text or "success" in text:
            summary["passed"] += 1
        elif "skip" in text:
            summary["skipped"] += 1
        elif "block" in text:
            summary["blocked"] += 1
        elif "fail" in text or "error" in text:
            summary["failed"] += 1
    return summary


def _execution_id(results: List[Dict[str, Any]]) -> Optional[str]:
    """Extract the execution ID (sys_id of first result)."""
    return str(value(results[0].get("sys_id"))) if results else None


def _record_key(record: Dict[str, Any]) -> tuple[str, str]:
    """Create a deduplication key from table name and sys_id."""
    return (str(record.get("table") or ""), str(value(record.get("sys_id")) or ""))


def _dedupe_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate results by table and sys_id (preserves first occurrence)."""
    seen: set[tuple[str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for result in results:
        key = _record_key(result)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def _result_url(
    client: ATFServiceNowClient, table: Optional[str], sys_id: Optional[str]
) -> Optional[str]:
    if not table or not sys_id:
        return None
    path = f"/{table}.do?sys_id={sys_id}"
    base = str(getattr(getattr(client, "config", None), "instance_url", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def _result_list_url(
    client: ATFServiceNowClient, table: Optional[str], sys_id: Optional[str]
) -> Optional[str]:
    if not table or not sys_id:
        return None
    path = f"/{table}_list.do?sysparm_query=sys_id={sys_id}"
    base = str(getattr(getattr(client, "config", None), "instance_url", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def _result_metadata(
    client: ATFServiceNowClient, results: List[Dict[str, Any]], target_kind: str
) -> Dict[str, Any]:
    preferred_table = (
        "sys_atf_test_suite_result" if target_kind == "suite" else "sys_atf_test_result"
    )
    primary = next((result for result in results if result.get("table") == preferred_table), None)
    if not primary:
        primary = results[0] if results else {}
    primary_table = primary.get("table")
    primary_id = str(value(primary.get("sys_id")) or "") or None
    test_result_ids = [
        str(value(result.get("sys_id")))
        for result in results
        if result.get("table") == "sys_atf_test_result" and value(result.get("sys_id"))
    ]
    suite_result_ids = [
        str(value(result.get("sys_id")))
        for result in results
        if result.get("table") == "sys_atf_test_suite_result" and value(result.get("sys_id"))
    ]
    return {
        "execution_id": primary_id,
        "result_table": primary_table,
        "result_url": _result_url(client, primary_table, primary_id),
        "result_list_url": _result_list_url(client, primary_table, primary_id),
        "suite_execution_id": suite_result_ids[0] if suite_result_ids else None,
        "test_result_ids": test_result_ids,
        "test_result_urls": [
            _result_url(client, "sys_atf_test_result", sys_id) for sys_id in test_result_ids
        ],
        "result_records": [
            {
                "table": result.get("table"),
                "sys_id": str(value(result.get("sys_id"))),
                "url": _result_url(client, result.get("table"), str(value(result.get("sys_id")))),
                "list_url": _result_list_url(
                    client, result.get("table"), str(value(result.get("sys_id")))
                ),
            }
            for result in results
            if value(result.get("sys_id"))
        ],
    }


def _unwrap_api_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = payload
    while (
        isinstance(current, dict)
        and set(current.keys()) == {"result"}
        and isinstance(current.get("result"), dict)
    ):
        current = current["result"]
    return current if isinstance(current, dict) else {}


def _link_id(payload: Dict[str, Any], link_name: str) -> Optional[str]:
    payload = _unwrap_api_payload(payload)
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    link = links.get(link_name) if isinstance(links, dict) else None
    if isinstance(link, dict):
        link_id = value(link.get("id"))
        if link_id:
            return str(link_id)
    for key in (f"{link_name}_id", f"{link_name}Id"):
        link_id = value(payload.get(key))
        if link_id:
            return str(link_id)
    return None


def _progress_id(payload: Dict[str, Any]) -> Optional[str]:
    return (
        _link_id(payload, "progress")
        or str(value(_unwrap_api_payload(payload).get("progress_id")) or "")
        or None
    )


def _results_id(payload: Dict[str, Any]) -> Optional[str]:
    return (
        _link_id(payload, "results")
        or _link_id(payload, "result")
        or str(value(_unwrap_api_payload(payload).get("result_id")) or "")
        or None
    )


def _state_text(payload: Dict[str, Any]) -> str:
    """Build normalized lowercase state text from heterogeneous runner payload fields."""
    payload = _unwrap_api_payload(payload)
    parts = [
        payload.get("state"),
        payload.get("status"),
        payload.get("status_label"),
        payload.get("status_message"),
        payload.get("message"),
        payload.get("error"),
    ]
    return " ".join(str(value(part) or "") for part in parts).casefold()


def _is_final_state(text: str) -> bool:
    """Return True when runner state text indicates terminal completion/failure."""
    return any(
        token in text
        for token in (
            "completed",
            "complete",
            "successful",
            "success",
            "failed",
            "failure",
            "error",
        )
    )


def _runner_unavailable_text(items: List[Any]) -> bool:
    """Detect runner-unavailable signatures from mixed response payload fragments."""
    text = " ".join(str(item) for item in items if item).casefold()
    return any(
        token in text
        for token in (
            "scheduled client test runner",
            "satisfies these client constraints",
            "choose a browser to run this test",
            "client-side steps",
            "runner is not available",
            "runner unavailable",
            "no runner",
        )
    )


def _runner_unavailable_warning(run_in_cloud: bool) -> str:
    if run_in_cloud:
        return (
            "Cloud Runner could not execute the client-side ATF steps. Verify Cloud Runner is "
            "enabled and available for this instance."
        )
    return (
        "No scheduled client test runner matched the requested browser/OS constraints. "
        "Use Cloud Runner or start/select a scheduled client runner in ServiceNow before running "
        "client-side ATF steps."
    )


def _new_result_records(
    client: ATFServiceNowClient,
    target_kind: str,
    target_id: str,
    before_keys: set[tuple[str, str]],
    result_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch and return result rows that were created after execution start."""
    records: List[Dict[str, Any]] = []
    if result_id:
        records.extend(client.query_atf_results(execution_id=result_id))
    if target_kind == "suite":
        records.extend(client.query_atf_results(suite_sys_id=target_id))
    else:
        records.extend(client.query_atf_results(test_sys_id=target_id))
    records = _dedupe_results(records)
    new_records = [record for record in records if _record_key(record) not in before_keys]
    return new_records or (records if result_id else [])


def _blocked_response(
    target_kind: str,
    params: ATFExecutionRequest,
    warnings: List[str],
    evidence: Optional[List[Dict[str, Any]]] = None,
    runner_job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build standardized blocked-run response when execution cannot proceed."""
    return {
        "status": "blocked",
        "test_sys_id": params.test_sys_id,
        "suite_sys_id": params.suite_sys_id,
        "runner_strategy": params.runner_strategy,
        "execution_id": None,
        "result_table": None,
        "result_url": None,
        "result_list_url": None,
        "suite_execution_id": None,
        "test_result_ids": [],
        "result_summary": {},
        "failed_steps": [],
        "runner_job": runner_job or {},
        "warnings": warnings,
        "evidence": evidence
        or [
            {
                "type": "runner_requirement",
                "runner_strategy": params.runner_strategy,
                "target_kind": target_kind,
            }
        ],
    }


def _with_suite_target(params: ATFExecutionRequest, suite_sys_id: str) -> ATFExecutionRequest:
    return ATFExecutionRequest(
        test_sys_id=params.test_sys_id,
        suite_sys_id=suite_sys_id,
        runner_strategy=params.runner_strategy,
        wait_for_completion=params.wait_for_completion,
        timeout_seconds=params.timeout_seconds,
        store_evidence=params.store_evidence,
        dry_run=params.dry_run,
    )


def _ensure_single_test_runner_suite(
    client: ATFServiceNowClient,
    test_sys_id: str,
    test_record: Dict[str, Any],
    warnings: List[str],
) -> Optional[str]:
    suite_name = f"MCP ATF Runner - {test_sys_id}"
    try:
        suite_result = client.create_atf_suite(
            {
                "name": suite_name,
                "description": (
                    "Created by MCP to run a single ATF test through the ServiceNow CI/CD "
                    "test-suite runner so native sys_atf_test_result rows are generated."
                ),
            }
        )
        suite = normalize(suite_result.get("record", suite_result))
        suite_id = str(value(suite.get("sys_id")) or "")
        if not suite_id:
            warnings.append(
                "Could not create or resolve an MCP runner suite for the single ATF test."
            )
            return None
        client.add_test_to_suite(suite_id, test_sys_id, 1)
        operation = suite_result.get("operation")
        test_name = str(value(test_record.get("name")) or test_sys_id)
        warnings.append(
            f"Single test '{test_name}' is being executed via runner suite '{suite_name}' "
            f"({suite_id}); "
            "the visible child row is in sys_atf_test_result."
        )
        if operation == "created":
            warnings.append(
                "The MCP runner suite was created and left in place for repeatable future runs."
            )
        return suite_id
    except Exception as exc:
        warnings.append(f"Could not prepare an MCP runner suite for the single ATF test: {exc}")
        return None


def _finalize_run_result(
    client: ATFServiceNowClient,
    params: ATFExecutionRequest,
    target_kind: str,
    results: List[Dict[str, Any]],
    warnings: List[str],
    status: str,
    runner_job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    summary = summarize_results(results)
    metadata = _result_metadata(client, results, target_kind)
    execution_id = metadata["execution_id"]
    failed_steps = (
        client.query_atf_step_results(execution_id)
        if execution_id and params.store_evidence
        else []
    )
    failed_steps = [
        step for step in failed_steps if "fail" in str(step).lower() or "error" in str(step).lower()
    ]
    evidence = [
        {
            "type": "atf_result",
            "execution_id": execution_id,
            "result_table": metadata.get("result_table"),
            "test_result_ids": metadata.get("test_result_ids", []),
            "summary": summary,
        }
    ]
    return {
        "status": status,
        "test_sys_id": params.test_sys_id,
        "suite_sys_id": params.suite_sys_id,
        "runner_strategy": params.runner_strategy,
        **metadata,
        "result_summary": summary,
        "results": results,
        "failed_steps": failed_steps,
        "runner_job": runner_job or {},
        "warnings": warnings,
        "evidence": evidence,
    }


def _run_standard_suite_execution(
    client: ATFServiceNowClient,
    params: ATFExecutionRequest,
    target_id: str,
    warnings: List[str],
    run_in_cloud: bool = False,
) -> Dict[str, Any]:
    """Execute suite via CI/CD runner and optionally poll until terminal state."""
    if not hasattr(client, "start_atf_suite_runner"):
        warnings.append(
            "Standard REST execution requires the ServiceNow CI/CD test-suite runner API."
        )
        return _blocked_response("suite", params, warnings)
    before_keys = {
        _record_key(result) for result in client.query_atf_results(suite_sys_id=target_id)
    }
    try:
        runner_job = client.start_atf_suite_runner(target_id, run_in_cloud=run_in_cloud)
    except Exception as exc:
        runner_name = "Cloud Runner" if run_in_cloud else "standard ATF suite runner"
        warnings.append(f"Could not start the {runner_name}: {exc}")
        return _blocked_response("suite", params, warnings)

    deadline = time.time() + max(1, params.timeout_seconds)
    progress_id = _progress_id(runner_job)
    result_id = _results_id(runner_job)
    progress: Dict[str, Any] = {}
    results: List[Dict[str, Any]] = []
    final_state = False
    while True:
        if progress_id and hasattr(client, "get_cicd_progress"):
            try:
                progress = client.get_cicd_progress(progress_id)
                result_id = result_id or _results_id(progress)
                final_state = _is_final_state(_state_text(progress))
            except Exception as exc:
                warnings.append(f"Could not read CI/CD runner progress: {exc}")
        results = _new_result_records(client, "suite", target_id, before_keys, result_id)
        if not params.wait_for_completion or (results and final_state) or time.time() >= deadline:
            break
        if results and not progress_id:
            break
        time.sleep(2)

    result_details: Dict[str, Any] = {}
    if result_id and hasattr(client, "get_cicd_testsuite_results"):
        try:
            result_details = client.get_cicd_testsuite_results(result_id)
        except Exception:
            result_details = {}

    summary = summarize_results(results)
    state = _state_text(progress or runner_job)
    runner_unavailable = _runner_unavailable_text([runner_job, progress, result_details, results])
    if params.wait_for_completion and time.time() >= deadline and not results:
        status = "timeout"
    elif "failed" in state or "failure" in state or "error" in state or summary.get("failed"):
        status = "failed"
    elif runner_unavailable:
        status = "blocked"
        warning = _runner_unavailable_warning(run_in_cloud)
        if warning not in warnings:
            warnings.append(warning)
    elif summary.get("total") and summary.get("skipped") == summary.get("total"):
        status = "blocked"
        warning = _runner_unavailable_warning(run_in_cloud)
        if warning not in warnings:
            warnings.append(warning)
    elif summary.get("blocked"):
        status = "blocked"
    elif summary.get("total") and not summary.get("failed"):
        status = "passed"
    elif final_state and not results:
        status = "partial"
        warnings.append(
            "The suite runner finished, but no sys_atf_test_result rows were visible yet."
        )
    else:
        status = "running"

    return _finalize_run_result(
        client,
        params,
        "suite",
        results,
        warnings,
        status,
        {
            "runner": "sn_cicd/testsuite/run",
            "run_in_cloud": run_in_cloud,
            "start_response": runner_job,
            "progress_id": progress_id,
            "progress": progress,
            "results_id": result_id,
            "result_details": result_details,
        },
    )


def _run_cloud_runner_execution(
    client: ATFServiceNowClient,
    params: ATFExecutionRequest,
    target_kind: str,
    target_id: str,
    warnings: List[str],
) -> Dict[str, Any]:
    """Execute ATF target through Cloud Runner and aggregate resulting records."""
    before_results = client.query_atf_results(
        test_sys_id=params.test_sys_id, suite_sys_id=params.suite_sys_id
    )
    before_keys = {_record_key(result) for result in before_results}
    runner_job = client.start_atf_cloud_runner(target_id)
    snboq_id = str(
        value(runner_job.get("snboqId") or runner_job.get("snboq_id") or runner_job.get("sys_id"))
        or ""
    )
    if not snboq_id:
        warnings.append(
            str(
                runner_job.get("message")
                or "Cloud Runner did not return a Browser Orchestration Queue id."
            )
        )
        return _blocked_response(
            target_kind,
            params,
            warnings,
            evidence=[
                {
                    "type": "cloud_runner_start",
                    "target_kind": target_kind,
                    "target_sys_id": target_id,
                }
            ],
            runner_job=runner_job,
        )

    deadline = time.time() + max(1, params.timeout_seconds)
    progress: Dict[str, Any] = {"snboqId": snboq_id}
    results: List[Dict[str, Any]] = []
    final_state = False
    while True:
        if params.wait_for_completion:
            progress = client.get_atf_cloud_runner_progress(snboq_id)
            final_state = _is_final_state(_state_text(progress))
        results = _new_result_records(client, target_kind, target_id, before_keys)
        if not params.wait_for_completion or (results and final_state) or time.time() >= deadline:
            break
        time.sleep(2)

    summary = summarize_results(results)
    state = _state_text(progress)
    if params.wait_for_completion and time.time() >= deadline and not results:
        status = "timeout"
    elif "failed" in state or summary.get("failed"):
        status = "failed"
    elif summary.get("passed"):
        status = "passed"
    elif final_state and not results:
        status = "partial"
        warnings.append("Cloud Runner finished, but no ATF result rows were visible yet.")
    else:
        status = "running"
    run = _finalize_run_result(
        client,
        params,
        target_kind,
        results,
        warnings,
        status,
        {"snboq_id": snboq_id, "start_response": runner_job, "progress": progress},
    )
    run["evidence"].insert(
        0,
        {
            "type": "cloud_runner_start",
            "target_kind": target_kind,
            "target_sys_id": target_id,
            "snboq_id": snboq_id,
        },
    )
    return run


def run_atf_execution_service(
    client: ATFServiceNowClient, params: ATFExecutionRequest
) -> Dict[str, Any]:
    """Execute ATF test/suite using the selected runner strategy.

    The service validates target identity, checks runner capabilities, dispatches
    execution to cloud or standard paths, and returns normalized status with
    evidence and warnings suitable for automated orchestration.
    """
    target_kind = "suite" if params.suite_sys_id else "test"
    target_id = params.suite_sys_id or params.test_sys_id
    if not target_id:
        return {
            "status": "blocked",
            "warnings": ["Provide test_sys_id or suite_sys_id."],
            "evidence": [],
        }
    capabilities = discover_atf_capabilities_service(client)
    warnings: List[str] = []
    if params.runner_strategy == "cloud_runner_if_available" and not capabilities[
        "capabilities"
    ].get("cloud_runner"):
        warnings.append("Cloud Runner is not available; use standard or scheduled client runner.")
    if params.dry_run:
        return {
            "status": "dry_run",
            "test_sys_id": params.test_sys_id,
            "suite_sys_id": params.suite_sys_id,
            "execution_id": None,
            "result_summary": {},
            "failed_steps": [],
            "warnings": warnings,
            "evidence": [
                {
                    "type": "atf_execution_plan",
                    "target_kind": target_kind,
                    "target_sys_id": target_id,
                }
            ],
        }
    table = "sys_atf_test_suite" if params.suite_sys_id else "sys_atf_test"
    record = client.get_record(table, target_id)
    if not record:
        return {
            "status": "blocked",
            "warnings": [f"ATF {target_kind} was not found."],
            "evidence": [],
        }
    if params.runner_strategy in {"client_runner", "scheduled_client_runner"}:
        warnings.append(
            "This ATF execution requires a ServiceNow client runner or schedule. Use schedule_atf_"
            "suite if unattended execution is required."
        )
        return _blocked_response(target_kind, params, warnings)
    if params.runner_strategy == "cloud_runner_if_available":
        if capabilities["capabilities"].get("cloud_runner"):
            try:
                return _run_cloud_runner_execution(client, params, target_kind, target_id, warnings)
            except Exception as exc:
                warnings.append(
                    f"Cloud Runner API start failed; trying CI/CD Cloud Runner path: {exc}"
                )
        else:
            warnings.append(
                "Cloud Runner capability could not be verified; trying the CI/CD Cloud Runner "
                "path before falling back."
            )
        if params.suite_sys_id:
            return _run_standard_suite_execution(
                client, params, target_id, warnings, run_in_cloud=True
            )
        runner_suite_id = _ensure_single_test_runner_suite(client, target_id, record, warnings)
        if runner_suite_id:
            return _run_standard_suite_execution(
                client,
                _with_suite_target(params, runner_suite_id),
                runner_suite_id,
                warnings,
                run_in_cloud=True,
            )
        return _blocked_response(target_kind, params, warnings)
    if params.runner_strategy == "standard":
        if params.suite_sys_id:
            return _run_standard_suite_execution(client, params, target_id, warnings)
        runner_suite_id = _ensure_single_test_runner_suite(client, target_id, record, warnings)
        if runner_suite_id:
            return _run_standard_suite_execution(
                client, _with_suite_target(params, runner_suite_id), runner_suite_id, warnings
            )
        return _blocked_response(target_kind, params, warnings)
    return _blocked_response(
        target_kind, params, warnings + [f"Unsupported runner strategy: {params.runner_strategy}"]
    )


def collect_atf_results_service(
    client: ATFServiceNowClient, params: CollectATFResultsParams
) -> Dict[str, Any]:
    """Collect, summarize, and optionally enrich ATF execution result records.

    Supports filtering by test, suite, or execution id and can include detailed
    step-level failure data for downstream diagnostics.
    """
    results = client.query_atf_results(
        test_sys_id=params.test_sys_id,
        suite_sys_id=params.suite_sys_id,
        execution_id=params.execution_id,
    )
    summary = summarize_results(results)
    target_kind = "suite" if params.suite_sys_id else "test"
    metadata = _result_metadata(client, results, target_kind)
    failed_steps: List[Dict[str, Any]] = []
    if params.include_step_results:
        execution_ids = (
            [params.execution_id]
            if params.execution_id
            else [str(value(item.get("sys_id"))) for item in results if value(item.get("sys_id"))]
        )
        for execution_id in execution_ids:
            failed_steps.extend(
                step
                for step in client.query_atf_step_results(execution_id)
                if params.include_failure_details or "fail" not in str(step).lower()
            )
    evidence = [
        {
            "type": "atf_result",
            "source_table": item.get("table"),
            "source_sys_id": value(item.get("sys_id")),
            "summary": normalize(item),
        }
        for item in results
    ]
    return {
        "status": "success" if results else "partial",
        "results": results,
        **metadata,
        "summary": summary,
        "failed_steps": failed_steps,
        "evidence": evidence,
        "warnings": [] if results else ["No ATF result records matched the supplied filters."],
    }
