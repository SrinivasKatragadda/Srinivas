"""ATF artifact coverage analysis.

This module calculates ATF coverage across release artifacts, update sets, and
application scopes. It discovers candidate artifacts, finds existing test
coverage, classifies coverage gaps, and produces a release-ready summary that
other quality gate workflows consume.

The implementation is intentionally heuristic-driven because ServiceNow ATF
coverage is spread across multiple record types and mapping tables. The module
therefore combines direct artifact discovery, suite membership lookup, template
expectations, and step-level summaries into a single coverage picture.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from servicenow_mcp.atf_framework.models import AnalyzeATFCoverageParams
from servicenow_mcp.atf_framework.service import (
    ATFServiceNowClient,
    normalize,
    safe_query,
    value,
)
from servicenow_mcp.atf_framework.templates import select_templates

UPDATE_XML_TYPE_MAP = {
    "sc_cat_item": "catalog_item",
    "sc_cat_item_producer": "record_producer",
    "sys_hub_flow": "flow",
    "sys_db_object": "table",
    "sys_script": "business_rule",
    "sys_script_include": "script_include",
    "sys_script_client": "client_script",
    "sys_ui_policy": "ui_policy",
    "sys_security_acl": "acl",
    "sysevent_email_action": "notification",
    "sys_report": "report",
    "pa_dashboards": "dashboard",
    "sys_app": "scoped_app",
    "sys_ws_operation": "rest_integration",
}


def classify_update(record: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a sys_update_xml record into artifact type and metadata.

    The function maps update set XML names back to the artifact types that ATF
    understands. This is the bridge between raw update-set records and the
    normalized artifact model used by coverage analysis.
    """
    name = str(record.get("name") or record.get("target_name") or "")
    table = next((table for table in UPDATE_XML_TYPE_MAP if table in name), "")
    artifact_type = UPDATE_XML_TYPE_MAP.get(table, "custom")
    return {
        "artifact_type": artifact_type,
        "artifact_table": table or str(record.get("target_table") or ""),
        "artifact_sys_id": str(record.get("target_sys_id") or record.get("sys_id") or ""),
        "artifact_name": str(record.get("target_name") or record.get("name") or ""),
        "source": "update_set",
        "raw": record,
    }


def discover_artifacts(
    client: ATFServiceNowClient, params: AnalyzeATFCoverageParams
) -> List[Dict[str, Any]]:
    """Discover all artifacts in update sets and application scopes based on coverage params."""
    artifacts: List[Dict[str, Any]] = []
    for update_set_id in params.update_set_sys_ids:
        updates = client.query_records(
            "sys_update_xml", f"update_set={safe_query(update_set_id)}", limit=500
        )
        artifacts.extend(classify_update(normalize(item)) for item in updates)
    if params.application_scope:
        include_attr = {
            "catalog_item": "include_catalog_items",
            "record_producer": "include_record_producers",
            "flow": "include_flows",
            "table": "include_tables",
            "business_rule": "include_business_rules",
            "script_include": "include_script_includes",
            "client_script": "include_client_scripts",
            "ui_policy": "include_ui_policies",
            "acl": "include_acls",
            "notification": "include_notifications",
            "report": "include_reports",
            "dashboard": "include_dashboards",
            "scoped_app": "include_scoped_apps",
            "rest_integration": "include_integrations",
        }
        for table, artifact_type in UPDATE_XML_TYPE_MAP.items():
            if not getattr(params.artifact_filters, include_attr.get(artifact_type, ""), True):
                continue
            try:
                records = client.query_records(
                    table, f"sys_scope={safe_query(params.application_scope)}", limit=100
                )
                artifacts.extend(
                    {
                        "artifact_type": artifact_type,
                        "artifact_table": table,
                        "artifact_sys_id": str(value(item.get("sys_id"))),
                        "artifact_name": str(
                            value(item.get("name"))
                            or value(item.get("title"))
                            or value(item.get("sys_name"))
                        ),
                        "source": "application_scope",
                        "raw": normalize(item),
                    }
                    for item in records
                )
            except Exception:
                continue
    deduped = {}
    for artifact in artifacts:
        key = (artifact["artifact_type"], artifact["artifact_sys_id"] or artifact["artifact_name"])
        deduped[key] = artifact
    return list(deduped.values())


def _sys_id(record: Dict[str, Any]) -> str:
    return str(value(record.get("sys_id")) or value(record.get("test")) or "")


def _test_category(test: Dict[str, Any]) -> str:
    text = " ".join(
        str(test.get(key, "")) for key in ("name", "description", "test_category", "category")
    ).lower()
    for category in (
        "smoke",
        "regression",
        "negative",
        "security",
        "configuration",
        "end_to_end",
        "api",
        "data_validation",
        "role_based",
    ):
        if category in text or category.replace("_", " ") in text:
            return category
    if "mandatory" in text:
        return "negative"
    return "functional"


def _suite_membership(
    client: ATFServiceNowClient, suite_sys_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    membership: Dict[str, Dict[str, Any]] = {}
    for suite_id in suite_sys_ids:
        try:
            suite = normalize(client.get_record("sys_atf_test_suite", suite_id))
            mappings = client.query_suite_tests(suite_id)
        except Exception:
            suite = {"sys_id": suite_id, "name": suite_id}
            mappings = []
        for mapping in mappings:
            test_id = str(value(mapping.get("test")) or "")
            if not test_id:
                continue
            membership.setdefault(test_id, {"test_sys_id": test_id, "suites": []})["suites"].append(
                {
                    "suite_sys_id": suite_id,
                    "suite_name": suite.get("name") or suite_id,
                    "order": value(mapping.get("order")),
                    "mapping_sys_id": value(mapping.get("sys_id")),
                }
            )
    return membership


def _test_step_summary(client: ATFServiceNowClient, test_sys_id: str) -> Dict[str, Any]:
    try:
        steps = client.query_atf_steps(test_sys_id)
    except Exception:
        steps = []
    configured = [
        step
        for step in steps
        if any(value(step.get(field)) for field in ("step_config", "config", "step_config_id"))
    ]
    return {
        "step_count": len(steps),
        "configured_step_count": len(configured),
        "unconfigured_step_count": max(0, len(steps) - len(configured)),
        "steps": steps,
    }


def find_existing_tests(
    client: ATFServiceNowClient,
    artifacts: List[Dict[str, Any]],
    scope: str = "mcp",
    suite_sys_ids: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Find existing ATF tests that cover specified artifacts.

    Queries ATF mapping table for each artifact to find related tests.
    Optionally filters by suite membership.

    Args:
        client: ServiceNow ATF client for artifact mapping queries
        artifacts: List of artifact dicts with artifact_type and artifact_sys_id
        scope: Application scope for artifact mapping table
        suite_sys_ids: Optional list of suite sys_ids to restrict search

    Returns:
        List of existing test records with coverage details (test_sys_id, artifact_sys_id, etc.)
    """
    existing = []
    membership = _suite_membership(client, suite_sys_ids or [])
    for artifact in artifacts:
        mappings = []
        try:
            mappings = client.get_atf_mapping_for_artifact(
                scope, artifact["artifact_type"], artifact["artifact_sys_id"]
            )
        except Exception:
            mappings = []
        tests = []
        search_terms = [artifact.get("artifact_sys_id"), artifact.get("artifact_name")]
        for term in [term for term in search_terms if term]:
            try:
                tests.extend(
                    client.query_records(
                        "sys_atf_test",
                        f"nameLIKE{safe_query(term)}^ORdescriptionLIKE{safe_query(term)}",
                        limit=20,
                    )
                )
            except Exception:
                continue
        normalized_tests = []
        seen = set()
        for test in [normalize(test) for test in tests]:
            test_id = _sys_id(test)
            if not test_id or test_id in seen:
                continue
            seen.add(test_id)
            step_summary = _test_step_summary(client, test_id)
            normalized_tests.append(
                {
                    **test,
                    "test_sys_id": test_id,
                    "detected_category": _test_category(test),
                    "suite_membership": membership.get(
                        test_id, {"test_sys_id": test_id, "suites": []}
                    )["suites"],
                    "step_summary": step_summary,
                    "configuration_status": (
                        "configured"
                        if step_summary["step_count"]
                        and step_summary["unconfigured_step_count"] == 0
                        else (
                            "needs_configuration"
                            if step_summary["step_count"]
                            else "no_steps_found"
                        )
                    ),
                }
            )
        mapped_test_ids = {str(value(item.get("test_sys_id")) or "") for item in mappings}
        for test_id in [item for item in mapped_test_ids if item and item not in seen]:
            try:
                test = normalize(client.get_record("sys_atf_test", test_id))
            except Exception:
                test = {"sys_id": test_id, "name": test_id}
            step_summary = _test_step_summary(client, test_id)
            normalized_tests.append(
                {
                    **test,
                    "test_sys_id": test_id,
                    "detected_category": _test_category(test),
                    "suite_membership": membership.get(
                        test_id, {"test_sys_id": test_id, "suites": []}
                    )["suites"],
                    "step_summary": step_summary,
                    "configuration_status": (
                        "configured"
                        if step_summary["step_count"]
                        and step_summary["unconfigured_step_count"] == 0
                        else (
                            "needs_configuration"
                            if step_summary["step_count"]
                            else "no_steps_found"
                        )
                    ),
                }
            )
        if mappings or normalized_tests:
            existing.append({"artifact": artifact, "mappings": mappings, "tests": normalized_tests})
    return existing


def analyze_atf_coverage_service(
    client: ATFServiceNowClient, params: AnalyzeATFCoverageParams
) -> Dict[str, Any]:
    """
    Analyze ATF test coverage for artifacts in update sets or application scopes.

    Workflow:
    1. Discover artifacts from update sets and/or application scope
    2. Find existing tests covering each artifact
    3. Compare discovered artifacts with template expectations
    4. Calculate coverage percentage and category coverage gaps
    5. Return a release-oriented summary with recommendations and warnings

    Args:
        client: ServiceNow ATF client for artifact and test queries
        params: AnalyzeATFCoverageParams with update sets, scope, minimum coverage requirement

    Returns:
        Dict with coverage_percent, covered_artifacts, uncovered_artifacts, coverage_summary
        by artifact_type, recommendations, and warnings.
    """
    artifacts = discover_artifacts(client, params)
    existing = (
        find_existing_tests(
            client, artifacts, params.application_scope or "mcp", params.suite_sys_ids
        )
        if params.include_existing_tests
        else []
    )
    covered_keys = {
        (
            item["artifact"]["artifact_type"],
            item["artifact"]["artifact_sys_id"] or item["artifact"]["artifact_name"],
        )
        for item in existing
    }
    missing = [
        artifact
        for artifact in artifacts
        if (artifact["artifact_type"], artifact["artifact_sys_id"] or artifact["artifact_name"])
        not in covered_keys
    ]
    total = len(artifacts)
    covered = len(covered_keys)
    coverage_percent = round((covered / total) * 100, 2) if total else 100
    by_type = defaultdict(lambda: {"total": 0, "covered": 0, "missing": 0})
    category_details = []
    suite_details = []
    for artifact in artifacts:
        bucket = by_type[artifact["artifact_type"]]
        bucket["total"] += 1
        key = (artifact["artifact_type"], artifact["artifact_sys_id"] or artifact["artifact_name"])
        if key in covered_keys:
            bucket["covered"] += 1
        else:
            bucket["missing"] += 1
        recommended_categories = {
            template["category"] for template in select_templates(artifact["artifact_type"])
        }
        artifact_existing = next(
            (item for item in existing if item["artifact"] == artifact), {"tests": []}
        )
        covered_categories = {
            test["detected_category"] for test in artifact_existing.get("tests", [])
        }
        missing_categories = sorted(recommended_categories - covered_categories)
        category_percent = (
            round(
                (
                    len(recommended_categories - set(missing_categories))
                    / len(recommended_categories)
                )
                * 100,
                2,
            )
            if recommended_categories
            else 100
        )
        category_details.append(
            {
                "artifact": artifact,
                "recommended_categories": sorted(recommended_categories),
                "covered_categories": sorted(covered_categories),
                "missing_categories": missing_categories,
                "category_coverage_percent": category_percent,
                "test_count": len(artifact_existing.get("tests", [])),
                "configured_test_count": len(
                    [
                        test
                        for test in artifact_existing.get("tests", [])
                        if test.get("configuration_status") == "configured"
                    ]
                ),
                "tests_needing_configuration": [
                    test
                    for test in artifact_existing.get("tests", [])
                    if test.get("configuration_status") != "configured"
                ],
            }
        )
    if params.include_suite_membership and params.suite_sys_ids:
        suite_members = _suite_membership(client, params.suite_sys_ids)
        all_existing_test_ids = {
            test["test_sys_id"]
            for item in existing
            for test in item.get("tests", [])
            if test.get("test_sys_id")
        }
        for suite_id in params.suite_sys_ids:
            suite_test_ids = {
                test_id
                for test_id, detail in suite_members.items()
                if any(suite.get("suite_sys_id") == suite_id for suite in detail.get("suites", []))
            }
            suite_details.append(
                {
                    "suite_sys_id": suite_id,
                    "suite_test_count": len(suite_test_ids),
                    "matched_existing_test_count": len(suite_test_ids & all_existing_test_ids),
                    "missing_existing_tests": sorted(all_existing_test_ids - suite_test_ids),
                    "extra_suite_tests": sorted(suite_test_ids - all_existing_test_ids),
                }
            )
    category_coverage_values = [item["category_coverage_percent"] for item in category_details]
    avg_category_coverage = (
        round(sum(category_coverage_values) / len(category_coverage_values), 2)
        if category_coverage_values
        else 100
    )
    tests_needing_configuration = [
        test for detail in category_details for test in detail["tests_needing_configuration"]
    ]
    recommended = [
        {
            "artifact": artifact,
            "templates": [
                template["template_id"] for template in select_templates(artifact["artifact_type"])
            ],
            "missing_categories": next(
                (
                    detail["missing_categories"]
                    for detail in category_details
                    if detail["artifact"] == artifact
                ),
                [],
            ),
        }
        for artifact in artifacts
        if artifact in missing
        or next(
            (
                detail
                for detail in category_details
                if detail["artifact"] == artifact and detail["missing_categories"]
            ),
            None,
        )
    ]
    return {
        "status": "success" if total or params.dry_run else "partial",
        "coverage_summary": {
            "total_artifacts": total,
            "covered_artifacts": covered,
            "uncovered_artifacts": len(missing),
            "coverage_percent": coverage_percent,
            "average_category_coverage_percent": avg_category_coverage,
            "tests_needing_configuration": len(tests_needing_configuration),
            "critical_uncovered_artifacts": len(
                [a for a in missing if a["artifact_type"] in {"acl", "business_rule", "flow"}]
            ),
        },
        "coverage_by_artifact_type": [
            {"artifact_type": key, **value} for key, value in sorted(by_type.items())
        ],
        "existing_tests": existing,
        "missing_tests": missing,
        "test_case_coverage": category_details,
        "suite_coverage": suite_details,
        "tests_needing_configuration": tests_needing_configuration,
        "recommended_tests": recommended,
        "quality_gate_result": (
            "pass"
            if coverage_percent >= params.minimum_required_coverage_percent
            and avg_category_coverage >= params.minimum_required_category_coverage_percent
            and not tests_needing_configuration
            else "fail"
        ),
        "warnings": (
            []
            if total
            else ["No artifacts discovered from the provided release/update set/scope filters."]
        ),
    }
